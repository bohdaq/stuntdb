"""Bounded local format hints; results never contain sampled values."""
import ipaddress
import re

import sqlalchemy as sa

EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')
EMAIL_FRAGMENT = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')


def _luhn(digits):
    total = 0
    for i, char in enumerate(reversed(digits)):
        value = int(char)
        if i % 2:
            value = value * 2
            value = value - 9 if value > 9 else value
        total += value
    return total % 10 == 0


def formats(value):
    """Return categories only. Numeric identifiers are deliberately ambiguous."""
    found = set()
    if EMAIL_FRAGMENT.search(value):
        found.add('email' if EMAIL.fullmatch(value) else 'embedded_email')
    digits = ''.join(c for c in value if c in '0123456789')
    if re.fullmatch(r'[0-9+(). \-]+', value) and 7 <= len(digits) <= 15:
        found.add('phone' if any(c in value for c in '+(). -') else 'numeric_identifier')
    if re.fullmatch(r'[0-9]{3}-[0-9]{2}-[0-9]{4}', value):
        found.add('ssn')
    if re.fullmatch(r'[0-9 -]+', value) and 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _luhn(digits):
        found.add('payment_card')
    try:
        ipaddress.ip_address(value)
        found.add('ip_address')
    except ValueError:
        pass
    return found


def sample_suggestions(connection, metadata, settings, sample_rows=100):
    """At most 1,000 rows, 10,000 cells, 513 characters per cell globally."""
    if not 0 <= sample_rows <= 1000:
        raise ValueError('Invalid sample bound')
    findings = {}
    remaining_rows, remaining_cells = 1000, 10000
    for name, table in sorted(metadata.tables.items()):
        columns = [c for c in table.c if
                   (isinstance(c.type, (sa.String, sa.Integer, sa.Numeric))
                    and not isinstance(c.type, (sa.Enum, sa.Float, sa.Boolean))
                    and not hasattr(c.type, 'values') and c.computed is None
                    and not c.info.get('mysql_spatial'))]
        columns = columns[:remaining_cells]
        limit = min(sample_rows, remaining_rows, remaining_cells // len(columns)) if columns else 0
        if not limit:
            continue
        query = sa.select(*(sa.func.substr(sa.cast(c, sa.String()), 1, 513).label(c.name) for c in columns)).limit(limit)
        if len(table.primary_key.columns):
            query = query.order_by(*table.primary_key.columns)
        rows = connection.execute(query).mappings().all()
        remaining_rows -= len(rows)
        remaining_cells -= len(rows) * len(columns)
        for column in columns:
            values = [str(r[column.name]) for r in rows if r[column.name] not in (None, '')]
            matches = [formats(v) for v in values]
            categories = sorted(set().union(*matches)) if matches else []
            if not categories:
                continue
            homogeneous = len(values) >= 3 and all(len(v) < 513 for v in values)
            action = next((kind for kind in ('email', 'phone') if homogeneous and all(m == {kind} for m in matches)), 'review')
            if not isinstance(column.type, sa.String):
                action = 'review'
            label = f'{name}.{column.name}'
            # Advisory detection never relaxes an existing unsupported-type rule.
            if settings['rules'][label] == 'review':
                action = 'review'
            settings['rules'][label] = action
            findings[label] = {'suggested_action': action,
                               'confidence': 'high' if action != 'review' else 'low',
                               'reason': 'homogeneous_format' if action != 'review' else 'ambiguous_or_mixed_format',
                               'formats': categories}
    return {'sample_rows': sample_rows, 'max_total_rows': 1000, 'max_total_cells': 10000,
            'max_cell_characters': 513, 'findings': findings}


def validate_detection(data):
    fields = {'sample_rows', 'max_total_rows', 'max_total_cells', 'max_cell_characters', 'findings'}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError('Invalid detection metadata')
    if type(data['sample_rows']) is not int or not 0 <= data['sample_rows'] <= 1000:
        raise ValueError('Invalid sample bound')
    for key, expected in [('max_total_rows', 1000), ('max_total_cells', 10000), ('max_cell_characters', 513)]:
        if type(data[key]) is not int or data[key] != expected:
            raise ValueError('Invalid detection bound')
    if not isinstance(data['findings'], dict):
        raise ValueError('Invalid findings')
    allowed = {'email', 'embedded_email', 'phone', 'numeric_identifier', 'ssn', 'payment_card', 'ip_address'}
    for label, finding in data['findings'].items():
        if not isinstance(label, str) or not isinstance(finding, dict) or set(finding) != {'suggested_action', 'confidence', 'reason', 'formats'}:
            raise ValueError('Invalid finding')
        if finding['suggested_action'] not in {'email', 'phone', 'review'} or finding['confidence'] not in {'high', 'low'} or finding['reason'] not in {'homogeneous_format', 'ambiguous_or_mixed_format'}:
            raise ValueError('Invalid detection suggestion')
        if not isinstance(finding['formats'], list) or not finding['formats'] or any(not isinstance(v, str) or v not in allowed for v in finding['formats']):
            raise ValueError('Invalid detected format')
