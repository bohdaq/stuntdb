"""Conservative deterministic masking with explicit failures for unsafe schemas."""
import hashlib
import hmac
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa

from stuntdb.core import Slice, verify
from stuntdb.literals import JSONDocument
from stuntdb.providers import PROVIDERS, provide, validate_provider


class MaskingError(ValueError):
    pass


class LeakError(MaskingError):
    pass


PRIVATE_NAME = re.compile(r"email|phone|mobile|birth|\bdob\b|ssn|social_security|passport|credit_card|card_number|password|secret|token|address|postal|zip_code", re.I)


def domains(metadata):
    parent = {c: c for t in metadata.tables.values() for c in t.c}
    def root(c):
        while parent[c] is not c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c
    for table in metadata.tables.values():
        for fk in table.foreign_key_constraints:
            for e in fk.elements:
                parent[root(e.parent)] = root(e.column)
    groups = defaultdict(list)
    for c in parent:
        groups[root(c)].append(c)
    return list(groups.values())


def _strings(value):
    if isinstance(value, JSONDocument):
        value = json.loads(value.text)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return [bytes(value).hex()] if value else []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, (list, tuple, set)):
        return [s for v in value for s in _strings(v)]
    return []


def _scalar_fingerprint(value):
    if isinstance(value, bool):
        return ('bool', value)
    if isinstance(value, (int, float, Decimal)):
        return ('number', str(Decimal(str(value)).normalize()))
    if isinstance(value, datetime):
        return ('datetime', value.isoformat())
    if isinstance(value, date):
        return ('date', value.isoformat())
    if isinstance(value, timedelta):
        return ('time', value.total_seconds())
    return None


def check_leaks(original: Slice, masked: Slice, columns):
    columns = list(columns)
    scalar_originals = {_scalar_fingerprint(row[column]) for name, column in columns
                        for row in original.rows[name]} - {None}
    originals = {s for name, column in columns for row in original.rows[name]
                 for s in _strings(row[column])}
    fingerprints = {hashlib.sha256(s.encode()).digest() for s in originals}
    # Scan all output columns, catching copies into otherwise unrelated fields.
    for rows in masked.rows.values():
        for row in rows:
            for value in row.values():
                if _scalar_fingerprint(value) in scalar_originals:
                    raise LeakError("Source scalar survived masking")
                for s in _strings(value):
                    if hashlib.sha256(s.encode()).digest() in fingerprints:
                        raise LeakError("Source value survived masking")
                    if any(len(v) >= 8 and v in s for v in originals):
                        raise LeakError("Source value survived inside output text")


def mask_slice(original: Slice, salt: str, rules: dict[str, str] | None = None) -> Slice:
    if len(salt.encode()) < 16:
        raise MaskingError("Salt must contain at least 16 bytes")
    metadata = original.metadata
    rules = rules or {}
    known = {f'{t.name}.{c.name}' for t in metadata.tables.values() for c in t.c}
    if set(rules) - known or any(v not in ({'auto', 'review', 'keep', 'clear', 'token'} | PROVIDERS) for v in rules.values()):
        raise MaskingError("Unknown column or rule")
    policies = {}
    domain_columns = {}
    groups = domains(metadata)
    key_columns = {c for t in metadata.tables.values() for c in t.primary_key}
    key_columns.update(e.parent for t in metadata.tables.values()
                       for fk in t.foreign_key_constraints for e in fk.elements)
    key_columns.update(e.column for t in metadata.tables.values()
                       for fk in t.foreign_key_constraints for e in fk.elements)
    for group in groups:
        present = any(original.rows[c.table.name] for c in group)
        if not present:
            continue
        explicit = {rules.get(f'{c.table.name}.{c.name}', 'auto') for c in group} - {'auto'}
        if 'review' in explicit:
            raise MaskingError("Selected columns still require review")
        if len(explicit) > 1:
            raise MaskingError("Conflicting rules in a relationship domain")
        strategy = next(iter(explicit), None)
        if strategy is not None:
            if any(c.computed is not None for c in group):
                raise MaskingError("Computed-column masking is not supported")
            if strategy in PROVIDERS:
                try:
                    validate_provider(strategy, group)
                except ValueError as error:
                    raise MaskingError('Provider does not fit column types') from error
            if strategy == 'token' and any(not isinstance(c.type, sa.String) or isinstance(c.type, sa.Enum) or hasattr(c.type, 'values') for c in group):
                raise MaskingError("Token rules require ordinary string columns")
            if strategy == 'clear':
                if any(c in key_columns for c in group):
                    raise MaskingError("Cannot clear relationship or primary keys")
                if any(not c.nullable and not isinstance(c.type, (sa.String, sa.JSON, sa.LargeBinary)) for c in group):
                    raise MaskingError("Required scalar columns cannot be cleared")
                if any(not c.nullable and (isinstance(c.type, sa.Enum) or hasattr(c.type, 'values')) for c in group):
                    raise MaskingError("Required enum/SET columns cannot be cleared")
        else:
            if any(c.computed is not None for c in group):
                raise MaskingError("Computed columns require reviewed masking rules")
            if any(isinstance(c.type, sa.Enum) or hasattr(c.type, 'values') for c in group):
                raise MaskingError("Enum and SET columns require reviewed masking rules")
            strings = all(isinstance(c.type, sa.String) for c in group)
            sensitive = any(PRIVATE_NAME.search(c.name) for c in group)
            if strings:
                clear = any(isinstance(c.type, sa.Text) for c in group) and not any(c in key_columns for c in group)
                strategy = "clear" if clear else "token"
            elif all(isinstance(c.type, (sa.JSON, sa.LargeBinary)) for c in group):
                if any(c in key_columns for c in group):
                    raise MaskingError("Opaque relationship keys require reviewed masking rules")
                strategy = "clear"
            elif sensitive or any(c.info.get('mysql_spatial') for c in group):
                raise MaskingError("Sensitive non-text columns require reviewed masking rules")
            elif all(isinstance(c.type, (sa.Integer, sa.Numeric, sa.Float, sa.Boolean, sa.Date, sa.DateTime, sa.Time))
                     or c.type.__class__.__name__ in {"YEAR", "BIT"} for c in group):
                strategy = "keep"
            else:
                raise MaskingError("Unknown column types require reviewed masking rules")
        label = min(f"{c.table.name}.{c.name}" for c in group)
        domain_columns[label] = group
        length = min([c.type.length for c in group if getattr(c.type, 'length', None)] or [128])
        for c in group:
            policies[c] = (strategy, label, min(length, 32) if strategy == "token" else length)
    rows = {name: [] for name in original.rows}
    masked_columns = set()
    mappings = {}
    outputs = defaultdict(dict)
    for name, records in original.rows.items():
        table = metadata.tables[name]
        for row in records:
            result = dict(row)
            for c in table.c:
                strategy, domain, length = policies[c]
                value = row[c.name]
                if strategy == "keep":
                    continue
                masked_columns.add((name, c.name))
                if value is None:
                    continue
                if strategy == "clear":
                    if c.nullable:
                        replacement = None
                    elif isinstance(c.type, sa.JSON):
                        replacement = JSONDocument('null')
                    elif isinstance(c.type, sa.LargeBinary):
                        replacement = b''
                    else:
                        replacement = ''
                else:
                    if strategy == 'token' and not isinstance(value, str):
                        raise MaskingError("Unsupported string value")
                    identity = (domain, value)
                    replacement = mappings.get(identity)
                    if replacement is None:
                        if strategy in PROVIDERS:
                            try:
                                replacement = provide(strategy, value, salt, domain, domain_columns[domain], length)
                            except ValueError as error:
                                raise MaskingError('Unsupported provider input or column bounds') from error
                        else:
                            payload = json.dumps([domain, value], ensure_ascii=False, separators=(',', ':')).encode()
                            replacement = hmac.new(salt.encode(), payload, hashlib.sha256).hexdigest()[:length]
                        if replacement in outputs[domain] and outputs[domain][replacement] != value:
                            raise MaskingError("Mask collision; increase column width or review rules")
                        outputs[domain][replacement] = value
                        mappings[identity] = replacement
                result[c.name] = replacement
            rows[name].append(result)
    masked = Slice(metadata, rows, masked=True)
    # Clearing unique fields may produce collisions; never silently emit them.
    for name, records in rows.items():
        table = metadata.tables[name]
        constraints = [(list(table.primary_key.columns), {})]
        constraints += [(list(u.columns), {}) for u in table.constraints if isinstance(u, sa.UniqueConstraint)]
        for index in table.indexes:
            if index.unique:
                prefix = index.dialect_options["mysql"].get("length") or {}
                if isinstance(prefix, int):
                    prefix = {c.name: prefix for c in index.columns}
                constraints.append((list(index.columns), prefix))
        for columns, prefix in constraints:
            seen = set()
            for row in records:
                values = tuple(row[c.name][:prefix[c.name]]
                               if c.name in prefix and row[c.name] is not None
                               else row[c.name] for c in columns)
                if any(v is None for v in values):
                    continue
                if values in seen:
                    raise MaskingError("Masking violates a unique constraint")
                seen.add(values)
    verify(masked)
    check_leaks(original, masked, masked_columns)
    return masked
