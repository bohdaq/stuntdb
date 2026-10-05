"""Strict, reviewable JSON configuration without embedded credentials or salts."""
import json
from pathlib import Path

from stuntdb.masking import MaskingError
from stuntdb.providers import PROVIDERS

ACTIONS = {'auto', 'review', 'keep', 'clear', 'token'} | PROVIDERS
FIELDS = {'version', 'source_env', 'salt_env', 'seed', 'children', 'max_rows', 'schema', 'rules', 'detection', 'schema_version'}


def schema_signature(metadata):
    from stuntdb.drift import signature
    return signature(metadata)


def generated_config(metadata):
    import sqlalchemy as sa
    from stuntdb.masking import PRIVATE_NAME
    rules = {}
    for table in metadata.tables.values():
        for c in table.c:
            unsupported = c.computed is not None or c.info.get('mysql_spatial') or isinstance(c.type, sa.Enum) or hasattr(c.type, 'values')
            supported = isinstance(c.type, (sa.String, sa.JSON, sa.LargeBinary, sa.Integer, sa.Numeric, sa.Float, sa.Boolean, sa.Date, sa.DateTime, sa.Time)) or c.type.__class__.__name__ in {'YEAR', 'BIT'}
            unsupported |= not supported
            unsupported |= bool(PRIVATE_NAME.search(c.name)) and not isinstance(c.type, (sa.String, sa.JSON, sa.LargeBinary))
            action = 'review' if unsupported else 'auto'
            label = c.name.lower()
            ordinary_string = isinstance(c.type, sa.String) and not isinstance(c.type, sa.Enum) and not hasattr(c.type, 'values')
            if not c.computed and not c.info.get('mysql_spatial'):
                if ordinary_string and 'email' in label:
                    action = 'email'
                elif ordinary_string and any(part in label for part in ('phone', 'mobile')):
                    action = 'phone'
                elif ordinary_string and label in {'name', 'first_name', 'last_name', 'full_name', 'given_name', 'family_name'}:
                    action = 'name'
                elif isinstance(c.type, sa.Date) and not isinstance(c.type, sa.DateTime) and any(part in label for part in ('birth', 'dob')):
                    action = 'date'
            rules[f'{table.name}.{c.name}'] = action
    return {'version': 1, 'source_env': 'STUNTDB_SOURCE', 'salt_env': 'STUNTDB_SALT',
            'seed': None, 'children': 0, 'max_rows': 10000,
            'schema_version': 2, 'schema': schema_signature(metadata), 'rules': rules}


def load_config(path: Path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate config key')
            result[key] = value
        return result
    data = json.loads(path.read_text(), object_pairs_hook=unique_keys)
    if not isinstance(data, dict) or set(data) - FIELDS or type(data.get('version')) is not int or data.get('version') != 1:
        raise ValueError('Unsupported config structure')
    for key in ('source_env', 'salt_env'):
        if key in data and (not isinstance(data[key], str) or not data[key]):
            raise ValueError('Invalid environment variable name')
    if data.get('seed') is not None and not isinstance(data['seed'], str):
        raise ValueError('Invalid seed')
    for key, minimum in [('children', 0), ('max_rows', 1)]:
        if key in data and (type(data[key]) is not int or data[key] < minimum):
            raise ValueError('Invalid traversal bound')
    rules = data.get('rules', {})
    if not isinstance(rules, dict) or any(not isinstance(k, str) or not isinstance(v, str) or v not in ACTIONS for k, v in rules.items()):
        raise ValueError('Invalid masking rules')
    if 'schema' in data and not isinstance(data['schema'], dict):
        raise ValueError('Invalid schema baseline')
    if 'schema_version' in data and (type(data['schema_version']) is not int or data['schema_version'] != 2):
        raise ValueError('Unsupported schema version')
    if 'detection' in data:
        from stuntdb.detection import validate_detection
        validate_detection(data['detection'])
    return data


def validate_schema(config, metadata):
    known = {f'{t.name}.{c.name}' for t in metadata.tables.values() for c in t.c}
    if set(config.get('rules', {})) - known:
        raise MaskingError('Rule references an unknown column')
    if 'schema' in config:
        from stuntdb.drift import differences, SchemaDriftError
        if config.get('schema_version') != 2:
            raise SchemaDriftError(['schema_version: regenerate legacy baseline with init'])
        paths = differences(config['schema'], schema_signature(metadata))
        if paths:
            raise SchemaDriftError(paths)
