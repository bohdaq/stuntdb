"""Value-free export receipts and read-only verification."""
import hashlib
import json
from pathlib import Path

import sqlalchemy as sa

from stuntdb import __version__
from stuntdb.config import schema_signature
from stuntdb.core import Slice, IntegrityError, reflect, verify
from stuntdb.literals import JSONDocument, select_rows
from stuntdb.providers import PROVIDERS


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def build_manifest(result, sql, dialect, settings):
    dialect = result.metadata.info.get('dialect', dialect)
    return {'version': 2, 'schema': schema_signature(result.metadata), 'tool_version': __version__, 'dialect': dialect,
            'masked': result.masked, 'export_sha256': hashlib.sha256(sql.encode()).hexdigest(),
            'config_sha256': digest(settings), 'schema_sha256': digest(schema_signature(result.metadata)),
            'tables': {name: len(rows) for name, rows in result.rows.items()},
            'strategies': result.strategies if result.masked else {
                f'{t.name}.{c.name}': 'keep' for t in result.metadata.tables.values() for c in t.c}}


def load_manifest(path: Path):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError('Duplicate manifest field')
            data[key] = value
        return data
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    fields = {'version', 'tool_version', 'dialect', 'masked', 'export_sha256',
              'config_sha256', 'schema_sha256', 'tables', 'strategies'}
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] not in {1, 2} or set(data) != (fields | {'schema'} if data['version'] == 2 else fields):
        raise ValueError('Unsupported manifest')
    if type(data['masked']) is not bool or data['dialect'] not in {'mysql', 'mariadb', 'sqlite'} or not isinstance(data['tool_version'], str):
        raise ValueError('Invalid manifest metadata')
    for key in ('export_sha256', 'config_sha256', 'schema_sha256'):
        value = data[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid manifest hash')
    if not isinstance(data['tables'], dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in data['tables'].items()):
        raise ValueError('Invalid manifest counts')
    if not isinstance(data['strategies'], dict) or any(not isinstance(k, str) or v not in ({'keep', 'clear', 'token'} | PROVIDERS) for k, v in data['strategies'].items()):
        raise ValueError('Invalid manifest strategies')
    if not data['masked'] and any(v != 'keep' for v in data['strategies'].values()):
        raise ValueError('Unmasked manifest has masking rules')
    if data['version'] == 2 and (not isinstance(data['schema'], dict) or digest(data['schema']) != data['schema_sha256']):
        raise ValueError('Invalid manifest schema hash')
    return data


def verify_file(path, manifest):
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(chunk)
    if hasher.hexdigest() != manifest['export_sha256']:
        raise IntegrityError('Export checksum mismatch')


def verify_database(connection, manifest):
    from stuntdb.source import dialect_name
    metadata = reflect(connection)
    check_schema(metadata, manifest, dialect_name(connection.dialect))
    if set(metadata.tables) != set(manifest['tables']):
        raise IntegrityError('Target tables differ')
    expected_columns = {f'{t.name}.{c.name}' for t in metadata.tables.values() if manifest['tables'][t.name] for c in t.c}
    if not expected_columns <= set(manifest['strategies']) or set(manifest['strategies']) - {f'{t.name}.{c.name}' for t in metadata.tables.values() for c in t.c}:
        raise IntegrityError('Manifest column coverage differs')
    rows = {}
    for name, table in metadata.tables.items():
        count = connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
        if count != manifest['tables'][name]:
            raise IntegrityError('Target row count differs')
        rows[name] = [dict(row) for row in connection.execute(select_rows(table)).mappings()]
        for row in rows[name]:
            for c in table.c:
                if isinstance(c.type, sa.JSON) and row[c.name] is not None:
                    row[c.name] = JSONDocument(row[c.name])
    result = Slice(metadata, rows, manifest['masked'], manifest['strategies'])
    verify(result)
    return result


def check_schema(metadata, manifest, dialect):
    from stuntdb.drift import differences, SchemaDriftError
    if manifest['version'] != 2:
        raise SchemaDriftError(['schema_version: regenerate legacy export with snapshot'])
    if digest(manifest['schema']) != manifest['schema_sha256']:
        raise IntegrityError('Manifest schema hash differs')
    if set(manifest['schema']) != set(manifest['tables']):
        raise IntegrityError('Manifest schema tables differ')
    if dialect != manifest['dialect']:
        raise SchemaDriftError(['dialect: changed'])
    paths = differences(manifest['schema'], schema_signature(metadata))
    if paths:
        raise SchemaDriftError(paths)
