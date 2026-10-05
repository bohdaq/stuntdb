"""Stable schema fingerprints and value-free mismatch diagnostics."""
import hashlib
import json

import sqlalchemy as sa
from stuntdb.core import IntegrityError
from stuntdb.masking import MaskingError

SCHEMA_VERSION = 2


def expression_hash(value):
    return None if value is None else hashlib.sha256(str(value).encode()).hexdigest()


def signature(metadata):
    tables = {}
    for name, table in metadata.tables.items():
        columns = {}
        for c in table.c:
            kind = c.type
            columns[c.name] = {
                'type': str(kind), 'nullable': c.nullable, 'primary_key': c.primary_key,
                'computed': c.computed is not None, 'spatial': bool(c.info.get('mysql_spatial')),
                'unsigned': bool(getattr(kind, 'unsigned', False)),
                'charset': getattr(kind, 'charset', None), 'collation': getattr(kind, 'collation', None),
                'default_sha256': expression_hash(c.server_default.arg) if c.server_default is not None and c.computed is None else None,
                'generated_sha256': expression_hash(c.computed.sqltext) if c.computed is not None else None,
                'generated_stored': c.computed.persisted if c.computed is not None else None,
                'values_sha256': expression_hash(json.dumps(getattr(kind, 'enums', getattr(kind, 'values', None)))),
            }
            columns[c.name].update(c.info.get('schema_details', {}))
        indexes = []
        for index in table.indexes:
            options = dict(index.dialect_options['mysql'])
            indexes.append({'columns': [getattr(c, 'name', None) for c in index.expressions],
                            'expressions_sha256': [expression_hash(c) for c in index.expressions],
                            'unique': bool(index.unique), 'options': options})
        unique = [{'columns': [c.name for c in constraint.columns], 'unique': True}
                  for constraint in table.constraints if isinstance(constraint, sa.UniqueConstraint)]
        checks = table.info.get('schema_checks', sorted(
            [expression_hash(c.sqltext) for c in table.constraints if isinstance(c, sa.CheckConstraint)]))
        tables[name] = {'columns': columns, 'primary_key': [c.name for c in table.primary_key],
                        'indexes': table.info.get('schema_indexes', sorted(indexes, key=lambda v: json.dumps(v, sort_keys=True))),
                        'unique_constraints': sorted(unique, key=lambda v: json.dumps(v, sort_keys=True)),
                        'checks': checks, 'options': table.info.get('schema_options', {}),
                        'references': sorted([{'from': [e.parent.name for e in fk.elements],
                            'table': fk.referred_table.name, 'to': [e.column.name for e in fk.elements],
                            'ondelete': fk.ondelete, 'onupdate': fk.onupdate,
                            'deferrable': fk.deferrable, 'initially': fk.initially}
                            for fk in table.foreign_key_constraints], key=lambda r: (r['from'], r['table'], r['to']))}
    return tables


def differences(expected, actual, limit=12):
    """Report paths and change kinds only; never print schema literal values."""
    result = []
    def walk(left, right, path):
        if len(result) >= limit:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                current = f'{path}.{key}' if path else key
                if key not in left:
                    result.append(f'{current}: added')
                elif key not in right:
                    result.append(f'{current}: removed')
                else:
                    walk(left[key], right[key], current)
                if len(result) >= limit:
                    return
        elif left != right:
            result.append(f'{path}: changed')
    walk(expected, actual, '')
    return result


class SchemaDriftError(MaskingError, IntegrityError):
    def __init__(self, paths):
        self.paths = paths
        super().__init__('Schema changed; regenerate and review: ' + '; '.join(paths))


def capture_details(connection, metadata):
    """Catalog fields reflection omits; exclude names/counters/statistics."""
    if connection.dialect.name != 'mysql':
        return
    maria = metadata.info['dialect'] == 'mariadb'
    details = connection.exec_driver_sql(
        'SELECT TABLE_NAME,COLUMN_NAME,CHARACTER_SET_NAME,COLLATION_NAME,'
        'COLUMN_DEFAULT,EXTRA,GENERATION_EXPRESSION,COLUMN_TYPE '
        'FROM information_schema.columns WHERE table_schema=DATABASE()')
    for table_name, name, charset, collation, default, extra, generated, column_type in details:
        if table_name in metadata.tables and name in metadata.tables[table_name].c:
            metadata.tables[table_name].c[name].info['schema_details'] = {
                'charset': charset, 'collation': collation,
                'default_sha256': expression_hash(default),
                'generated_sha256': expression_hash(generated or None),
                'extra_sha256': expression_hash(extra), 'storage_type_sha256': expression_hash(column_type)}
    for name, engine, collation in connection.exec_driver_sql(
        'SELECT TABLE_NAME,ENGINE,TABLE_COLLATION FROM information_schema.tables '
        "WHERE table_schema=DATABASE() AND table_type='BASE TABLE'"):
        if name in metadata.tables:
            metadata.tables[name].info['schema_options'] = {'engine': engine, 'collation': collation}
    for table in metadata.tables.values():
        table.info['schema_checks'] = []
        table.info['schema_indexes'] = []
    if maria:
        query = ('SELECT TABLE_NAME,CHECK_CLAUSE FROM information_schema.check_constraints '
                 'WHERE constraint_schema=DATABASE()')
        for name, clause in connection.exec_driver_sql(query):
            if name in metadata.tables:
                metadata.tables[name].info['schema_checks'].append({'expression_sha256': expression_hash(clause), 'enforced': True})
    else:
        query = ('SELECT t.TABLE_NAME,c.CHECK_CLAUSE,t.ENFORCED '
                 'FROM information_schema.table_constraints t JOIN information_schema.check_constraints c '
                 'ON t.CONSTRAINT_SCHEMA=c.CONSTRAINT_SCHEMA AND t.CONSTRAINT_NAME=c.CONSTRAINT_NAME '
                 "WHERE t.CONSTRAINT_SCHEMA=DATABASE() AND t.CONSTRAINT_TYPE='CHECK'")
        for name, clause, enforced in connection.exec_driver_sql(query):
            if name in metadata.tables:
                metadata.tables[name].info['schema_checks'].append({'expression_sha256': expression_hash(clause), 'enforced': enforced == 'YES'})
    expression = 'NULL' if maria else 'EXPRESSION'
    index_rows = connection.exec_driver_sql(
        'SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,SUB_PART,'
        f'COLLATION,INDEX_TYPE,{expression} FROM information_schema.statistics '
        "WHERE table_schema=DATABASE() AND INDEX_NAME <> 'PRIMARY' ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX")
    groups = {}
    for table, name, non_unique, seq, column, prefix, order, kind, expr in index_rows:
        if table not in metadata.tables:
            continue
        group = groups.setdefault((table, name), {'unique': not bool(non_unique), 'type': kind, 'columns': []})
        group['columns'].append({'name': column, 'prefix': prefix, 'order': order, 'expression_sha256': expression_hash(expr)})
    for (table, _), index in groups.items():
        metadata.tables[table].info['schema_indexes'].append(index)
    for table in metadata.tables.values():
        for key in ('schema_checks', 'schema_indexes'):
            table.info[key].sort(key=lambda v: json.dumps(v, sort_keys=True))
