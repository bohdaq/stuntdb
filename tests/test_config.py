import json

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from stuntdb.cli import app
from stuntdb.config import generated_config, load_config, validate_schema
from stuntdb.core import Slice
from stuntdb.masking import mask_slice, MaskingError

SALT = 'test-only-project-salt-1234567890'


def metadata():
    m = sa.MetaData()
    sa.Table('person', m, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('email', sa.String(100)), sa.Column('gender', sa.Enum('M', 'F')),
             sa.Column('birth_date', sa.Date), sa.Column('notes', sa.Text))
    return m


def test_generated_config_marks_review_and_has_no_secrets():
    config = generated_config(metadata())
    assert config['rules']['person.gender'] == 'review'
    assert config['rules']['person.birth_date'] == 'date'
    assert config['rules']['person.email'] == 'email'
    assert config['source_env'] == 'STUNTDB_SOURCE'
    assert config['salt_env'] == 'STUNTDB_SALT'


@pytest.mark.parametrize('data', [
    {'version': True}, {'version': 2}, {'version': 1, 'salt': 'secret'},
    {'version': 1, 'rules': {'person.email': 'typo'}},
    {'version': 1, 'max_rows': True}, {'version': 1, 'children': -1},
    {'version': 1, 'source_env': None}, {'version': 1, 'rules': []}])
def test_invalid_config_fails(tmp_path, data):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_config(path)


def test_duplicate_keys_fail(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError, match='Duplicate'):
        load_config(path)


def test_schema_changes_and_unknown_columns_fail():
    m = metadata()
    config = generated_config(m)
    validate_schema(config, m)
    m.tables['person'].append_column(sa.Column('new_email', sa.String(100)))
    with pytest.raises(MaskingError, match='Schema'):
        validate_schema(config, m)
    with pytest.raises(MaskingError, match='unknown'):
        validate_schema({'rules': {'person.typo': 'keep'}}, m)


def test_reviewed_keep_clear_and_token_rules():
    original = Slice(metadata(), {'person': [{'id': 1, 'email': 'canary@example.invalid',
        'gender': 'M', 'birth_date': None, 'notes': 'private notes'}]})
    with pytest.raises(MaskingError, match='review'):
        mask_slice(original, SALT, generated_config(original.metadata)['rules'])
    rules = {'person.gender': 'keep', 'person.birth_date': 'clear', 'person.notes': 'token'}
    result = mask_slice(original, SALT, rules)
    assert result.rows['person'][0]['gender'] == 'M'
    assert result.rows['person'][0]['notes'] != 'private notes'
    assert result.rows['person'][0]['notes'] is not None


def test_nullable_enum_can_be_cleared():
    m = metadata()
    row = {'id': 1, 'email': 'canary@example.invalid', 'gender': 'M', 'birth_date': None, 'notes': None}
    result = mask_slice(Slice(m, {'person': [row]}), SALT, {'person.gender': 'clear', 'person.birth_date': 'clear'})
    assert result.rows['person'][0]['gender'] is None


def test_conflicting_linked_rules_and_key_clearing_fail():
    m = sa.MetaData()
    sa.Table('parent', m, sa.Column('email', sa.String(100), primary_key=True))
    sa.Table('child', m, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('email', sa.String(100), sa.ForeignKey('parent.email')))
    original = Slice(m, {'parent': [{'email': 'canary@example.invalid'}], 'child': [{'id': 1, 'email': 'canary@example.invalid'}]})
    with pytest.raises(MaskingError, match='Conflicting'):
        mask_slice(original, SALT, {'parent.email': 'keep', 'child.email': 'token'})
    with pytest.raises(MaskingError, match='Cannot clear'):
        mask_slice(original, SALT, {'parent.email': 'clear'})
    kept = mask_slice(original, SALT, {'parent.email': 'keep'})
    assert kept.rows == original.rows


def test_required_sensitive_scalar_cannot_clear():
    m = sa.MetaData()
    sa.Table('person', m, sa.Column('id', sa.Integer, primary_key=True), sa.Column('phone', sa.Integer, nullable=False))
    with pytest.raises(MaskingError, match='Required'):
        mask_slice(Slice(m, {'person': [{'id': 1, 'phone': 123456789}]}), SALT, {'person.phone': 'clear'})


def test_init_config_cli_roundtrip_and_drift(tmp_path):
    database = tmp_path / 'source.db'
    source = f'sqlite:///{database}'
    engine = sa.create_engine(source)
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE person (id INTEGER PRIMARY KEY, email VARCHAR(100))')
        c.exec_driver_sql("INSERT INTO person VALUES (1, 'canary@example.invalid')")
    config = tmp_path / 'stuntdb.json'
    runner = CliRunner()
    assert runner.invoke(app, ['init', source, '-o', str(config), '--sample-rows', '0']).exit_code == 0
    content = config.read_text()
    assert source not in content and 'canary' not in content
    assert runner.invoke(app, ['init', source, '-o', str(config), '--sample-rows', '0']).exit_code == 1
    assert config.read_text() == content
    settings = json.loads(content)
    settings['seed'] = 'person.id=1'
    config.write_text(json.dumps(settings))
    output = tmp_path / 'seed.sql'
    args = ['snapshot', '--config', str(config), '-o', str(output)]
    env = {'STUNTDB_SOURCE': source, 'STUNTDB_SALT': SALT}
    result = runner.invoke(app, args, env=env)
    assert result.exit_code == 0, result.output
    before = output.read_text()
    with engine.begin() as c:
        c.exec_driver_sql('ALTER TABLE person ADD COLUMN new_secret VARCHAR(100)')
    assert runner.invoke(app, args, env=env).exit_code == 1
    assert output.read_text() == before


def test_cleared_sensitive_numeric_value_cannot_survive_elsewhere():
    from stuntdb.masking import LeakError
    m = sa.MetaData()
    sa.Table('person', m, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('phone', sa.Integer), sa.Column('reference', sa.Integer))
    original = Slice(m, {'person': [{'id': 1, 'phone': 123456789, 'reference': 123456789}]})
    with pytest.raises(LeakError):
        mask_slice(original, SALT, {'person.phone': 'clear'})
