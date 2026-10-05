"""Transactional loading of the restricted SQL format emitted by stuntdb."""
import hashlib
import re

import sqlalchemy as sa

from stuntdb.config import schema_signature
from stuntdb.core import IntegrityError, reflect
from stuntdb.manifest import digest, verify_database

IDENT = r'(?:`(?:``|[^`])+`|"(?:""|[^"])+"|[A-Za-z_][A-Za-z0-9_$]*)'
INSERT = re.compile(rf'INSERT INTO ({IDENT}) \((.+)\) VALUES \((.+)\);')
LITERAL = re.compile(
    r"(?:NULL|true|false|[-+]?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?|"
    r"'[0-9:+ .-]+'|X'[0-9a-fA-F]*'|"
    r"CONVERT\(X'[0-9a-fA-F]*' USING utf8mb4\)|"
    r"CAST\(X'[0-9a-fA-F]*' AS TEXT\)|"
    r"ST_GeomFromWKB\(X'[0-9a-fA-F]*', [0-9]+\))")


def _parts(text):
    parts, start, depth, quote, i = [], 0, 0, None, 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 1
                else:
                    quote = None
        elif char in "'\"`":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise ValueError('Invalid SQL structure')
        elif char == ',' and not depth:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    if quote or depth:
        raise ValueError('Invalid SQL structure')
    return parts + [text[start:].strip()]


def _identifier(value):
    if not re.fullmatch(IDENT, value):
        raise ValueError('Invalid SQL identifier')
    if value[0] in '`"':
        return value[1:-1].replace(value[0] * 2, value[0])
    return value


def parse_export(content, manifest):
    """Reject arbitrary SQL, including commits, DDL and executable expressions."""
    if hashlib.sha256(content).hexdigest() != manifest['export_sha256']:
        raise IntegrityError('Export checksum mismatch')
    lines = content.decode('utf-8').splitlines()
    status = 'MASKED' if manifest['masked'] else 'UNMASKED'
    header = f'-- stuntdb: {status} development data; requires an existing schema'
    mysql = manifest['dialect'] in {'mysql', 'mariadb'}
    prefix = [header] + (["SET @stuntdb_old_fk_checks = @@FOREIGN_KEY_CHECKS;",
                        "SET FOREIGN_KEY_CHECKS = 0;", "START TRANSACTION;"] if mysql else
                       ["PRAGMA foreign_keys = OFF;", "BEGIN TRANSACTION;"])
    suffix = ['COMMIT;', 'SET FOREIGN_KEY_CHECKS = @stuntdb_old_fk_checks;' if mysql else 'PRAGMA foreign_keys = ON;']
    if lines[:len(prefix)] != prefix or lines[-2:] != suffix:
        raise ValueError('Not a supported stuntdb export')
    statements = []
    counts = {name: 0 for name in manifest['tables']}
    for line in lines[len(prefix):-2]:
        match = INSERT.fullmatch(line)
        if not match:
            raise ValueError('Unsupported export statement')
        table = _identifier(match[1])
        columns = [_identifier(c) for c in _parts(match[2])]
        values = _parts(match[3])
        if table not in counts or len(columns) != len(values) or len(set(columns)) != len(columns) or any(not LITERAL.fullmatch(v) for v in values):
            raise ValueError('Unsupported insert')
        counts[table] += 1
        statements.append((table, columns, line))
    if counts != manifest['tables']:
        raise IntegrityError('Export row counts differ')
    return statements


def load_export(engine, content, manifest):
    statements = parse_export(content, manifest)  # Validate bytes before connecting.
    mysql = engine.dialect.name == 'mysql'
    if mysql != (manifest['dialect'] in {'mysql', 'mariadb'}):
        raise IntegrityError('Target dialect differs')
    if mysql:
        engine = engine.execution_options(isolation_level='SERIALIZABLE')
    with engine.connect() as connection:
        old_checks, old_mode = None, None
        try:
            if mysql:
                version = connection.exec_driver_sql('SELECT VERSION()').scalar_one()
                from stuntdb.source import validate_server
                if validate_server(version) != manifest['dialect']:
                    raise IntegrityError('Target server family differs')
                bad = connection.exec_driver_sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE' AND engine <> 'InnoDB'").scalar_one()
                triggers = connection.exec_driver_sql('SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=DATABASE()').scalar_one()
                if bad or triggers:
                    raise ValueError('Target requires InnoDB and no triggers')
                old_checks = connection.exec_driver_sql('SELECT @@SESSION.FOREIGN_KEY_CHECKS').scalar_one()
                old_mode = connection.exec_driver_sql('SELECT @@SESSION.sql_mode').scalar_one()
                connection.rollback()
                connection.exec_driver_sql('SET SESSION FOREIGN_KEY_CHECKS=0')
                modes = set(old_mode.split(',')) - {''}
                modes.update({'STRICT_ALL_TABLES', 'NO_ENGINE_SUBSTITUTION'})
                connection.exec_driver_sql('SET SESSION sql_mode=%s', (','.join(sorted(modes)),))
            else:
                old_checks = connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one()
                triggers = connection.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").scalar_one()
                if triggers:
                    raise ValueError('Target must have no triggers')
                connection.rollback()
                connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
            connection.commit()
            with connection.begin():
                if not mysql:
                    connection.exec_driver_sql('BEGIN IMMEDIATE')
                metadata = reflect(connection)
                if digest(schema_signature(metadata)) != manifest['schema_sha256'] or set(metadata.tables) != set(manifest['tables']):
                    raise IntegrityError('Target schema differs')
                for table in metadata.tables.values():
                    if connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one():
                        raise IntegrityError('Target tables must be empty')
                for name, columns, statement in statements:
                    table = metadata.tables[name]
                    expected = {c.name for c in table.c if c.computed is None}
                    if set(columns) != expected:
                        raise IntegrityError('Insert columns differ')
                    # No parameter interpolation: exported values can contain percent signs.
                    cursor = connection.connection.driver_connection.cursor()
                    try:
                        cursor.execute(statement)
                        if mysql and cursor.warning_count:
                            raise IntegrityError('Target coerced an inserted value')
                    finally:
                        cursor.close()
                verify_database(connection, manifest)
        finally:
            connection.rollback()
            if old_checks is not None:
                if mysql:
                    connection.exec_driver_sql('SET SESSION FOREIGN_KEY_CHECKS=%s', (old_checks,))
                    if old_mode is not None:
                        connection.exec_driver_sql('SET SESSION sql_mode=%s', (old_mode,))
                else:
                    connection.exec_driver_sql(f'PRAGMA foreign_keys={int(old_checks)}')
                connection.commit()
