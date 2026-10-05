import json

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from stuntdb.cli import app
from stuntdb.config import generated_config, schema_signature
from stuntdb.core import Slice, reflect, sql_export
from stuntdb.drift import differences, SchemaDriftError
from stuntdb.manifest import build_manifest, check_schema, load_manifest


def model(default="'secret-default'", generated='id + 1', check='id > 0', index_length=12, collation='utf8mb4_bin'):
    m = sa.MetaData()
    table = sa.Table('person', m, sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('email', sa.String(100, collation=collation), server_default=sa.text(default)),
        sa.Column('derived', sa.Integer, sa.Computed(generated, persisted=True)), sa.CheckConstraint(check))
    sa.Index('email_lookup', table.c.email, unique=True, mysql_length=index_length)
    return m


@pytest.mark.parametrize('changed,path', [
    ({'default': "'other-secret'"}, 'default_sha256'), ({'generated': 'id + 2'}, 'generated_sha256'),
    ({'check': 'id >= 0'}, 'checks'), ({'index_length': 20}, 'indexes'),
    ({'collation': 'utf8mb4_general_ci'}, 'collation')])
def test_each_new_fingerprint_feature_detects_changes(changed, path):
    before = schema_signature(model())
    after = schema_signature(model(**changed))
    report = differences(before, after)
    assert any(path in line for line in report)
    assert 'secret-default' not in json.dumps(before) + ' '.join(report)
    assert 'other-secret' not in ' '.join(report)


def test_names_and_auto_increment_counters_do_not_define_shape():
    m = model()
    before = schema_signature(m)
    next(iter(m.tables['person'].indexes)).name = 'renamed_index'
    assert schema_signature(m) == before


def test_primary_key_order_and_foreign_key_actions():
    def metadata(order, action):
        m = sa.MetaData()
        sa.Table('parent', m, sa.Column('a', sa.Integer), sa.Column('b', sa.Integer), sa.PrimaryKeyConstraint(*order))
        sa.Table('child', m, sa.Column('id', sa.Integer, primary_key=True), sa.Column('a', sa.Integer), sa.Column('b', sa.Integer),
                 sa.ForeignKeyConstraint(['a', 'b'], ['parent.a', 'parent.b'], ondelete=action))
        return m
    before = schema_signature(metadata(['a', 'b'], 'CASCADE'))
    assert any('primary_key' in p for p in differences(before, schema_signature(metadata(['b', 'a'], 'CASCADE'))))
    assert any('references' in p for p in differences(before, schema_signature(metadata(['a', 'b'], 'RESTRICT'))))


def test_manifest_contains_hashed_expressions_and_validates_hash(tmp_path):
    result = Slice(model(), {'person': []})
    script = sql_export(result, sa.create_engine('sqlite://').dialect)
    receipt = build_manifest(result, script, 'sqlite', {})
    assert receipt['version'] == 2
    assert 'secret-default' not in json.dumps(receipt)
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(receipt))
    assert load_manifest(path)['schema'] == receipt['schema']
    receipt['schema']['person']['checks'] = []
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        load_manifest(path)


def test_legacy_manifest_is_checksum_only(tmp_path):
    m = model()
    script = sql_export(Slice(m, {'person': []}), sa.create_engine('sqlite://').dialect)
    receipt = build_manifest(Slice(m, {'person': []}), script, 'sqlite', {})
    receipt['version'] = 1
    del receipt['schema']
    path = tmp_path / 'old.json'
    output = tmp_path / 'old.sql'
    output.write_text(script)
    path.write_text(json.dumps(receipt))
    assert CliRunner().invoke(app, ['verify', str(output), '--manifest', str(path)]).exit_code == 0
    with pytest.raises(SchemaDriftError, match='legacy'):
        check_schema(m, receipt, 'sqlite')


def test_snapshot_drift_message_has_location_and_no_default(tmp_path):
    url = f'sqlite:///{tmp_path / "source.db"}'
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE person (id INTEGER PRIMARY KEY, value VARCHAR(100) DEFAULT 'private-default')")
        c.exec_driver_sql('INSERT INTO person(id) VALUES (1)')
    config = tmp_path / 'config.json'
    with engine.connect() as c:
        settings = generated_config(reflect(c))
    settings['schema']['person']['columns']['value']['default_sha256'] = '0' * 64
    config.write_text(json.dumps(settings))
    output = tmp_path / 'output.sql'
    result = CliRunner().invoke(app, ['snapshot', url, '--config', str(config), '--seed', 'person.id=1', '--allow-unmasked', '-o', str(output)])
    assert result.exit_code == 3
    assert 'person.columns.value.default_sha256: changed' in result.output
    assert 'private-default' not in result.output
    assert not output.exists()
