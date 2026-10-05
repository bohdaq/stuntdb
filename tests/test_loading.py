import hashlib
import json
import sqlite3

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from stuntdb.cli import app
from stuntdb.core import Slice, sql_export
from stuntdb.manifest import build_manifest
from stuntdb.loading import load_export, parse_export
from stuntdb.core import IntegrityError


@pytest.fixture
def export(tmp_path):
    engine = sa.create_engine(f'sqlite:///{tmp_path / "target.db"}')
    metadata = sa.MetaData()
    sa.Table('weird, "table', metadata, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('parent', sa.Integer, sa.ForeignKey('weird, "table.id')),
             sa.Column('value', sa.String(200)))
    metadata.create_all(engine)
    result = Slice(metadata, {'weird, "table': [{'id': 1, 'parent': 2, 'value': "snow 雪; 50% \\ O'Reilly\n"},
                                               {'id': 2, 'parent': 1, 'value': None}]})
    # Use reflected metadata to match SQLite's nullable PK reflection.
    from stuntdb.core import reflect
    with engine.connect() as c:
        result.metadata = reflect(c)
    sql = sql_export(result, engine.dialect)
    receipt = build_manifest(result, sql, 'sqlite', {})
    path = tmp_path / 'slice.sql'
    path.write_text(sql)
    manifest = tmp_path / 'slice.sql.manifest.json'
    manifest.write_text(json.dumps(receipt))
    return engine, sql, receipt, path


def rows(engine):
    with engine.connect() as c:
        return c.exec_driver_sql('SELECT * FROM "weird, ""table" ORDER BY id').all()


def test_cli_load_cycles_and_repeat_rejected(export):
    engine, _, _, path = export
    args = ['load', str(path), '--target', str(engine.url)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    before = rows(engine)
    assert len(before) == 2 and before[0][1] == 2 and before[1][1] == 1
    assert CliRunner().invoke(app, args).exit_code == 3
    assert rows(engine) == before


def test_checksum_fails_before_writes(export):
    engine, sql, receipt, _ = export
    with pytest.raises(IntegrityError):
        load_export(engine, (sql + '-- change').encode(), receipt)
    assert rows(engine) == []


def test_foreign_key_failure_rolls_back_and_restores_settings(export):
    engine, sql, receipt, _ = export
    sql = sql.replace('(1, 2,', '(1, 999,')
    receipt['export_sha256'] = hashlib.sha256(sql.encode()).hexdigest()
    with engine.connect() as c:
        c.exec_driver_sql('PRAGMA foreign_keys=ON')
    with pytest.raises(IntegrityError):
        load_export(engine, sql.encode(), receipt)
    assert rows(engine) == []
    with engine.connect() as c:
        assert c.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1


def test_insert_failure_rolls_back(export):
    engine, sql, receipt, _ = export
    sql = sql.replace('(2, 1, NULL)', '(1, 1, NULL)')
    receipt['export_sha256'] = hashlib.sha256(sql.encode()).hexdigest()
    with pytest.raises(sqlite3.IntegrityError):
        load_export(engine, sql.encode(), receipt)
    assert rows(engine) == []


@pytest.mark.parametrize('statement', [
    'COMMIT;', 'DROP TABLE x;', 'INSERT INTO x (id) VALUES (sleep(10));',
    'INSERT INTO x (id) VALUES (1); DROP TABLE x;',
    'INSERT INTO x (id) VALUES ((SELECT 1));'])
def test_arbitrary_sql_rejected_even_with_matching_checksum(export, statement):
    _, sql, receipt, _ = export
    lines = sql.splitlines()
    lines[3] = statement
    content = ('\n'.join(lines) + '\n').encode()
    receipt['export_sha256'] = hashlib.sha256(content).hexdigest()
    with pytest.raises(ValueError):
        parse_export(content, receipt)


def test_trigger_target_rejected(export):
    engine, sql, receipt, _ = export
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER changed AFTER INSERT ON "weird, ""table" BEGIN UPDATE "weird, ""table" SET value="changed"; END')
    with pytest.raises(ValueError):
        load_export(engine, sql.encode(), receipt)
    assert rows(engine) == []


def test_schema_mismatch_preserves_target(export):
    engine, sql, receipt, _ = export
    with engine.begin() as c:
        c.exec_driver_sql('ALTER TABLE "weird, ""table" ADD COLUMN extra INTEGER')
    with pytest.raises(IntegrityError):
        load_export(engine, sql.encode(), receipt)
    assert rows(engine) == []


def test_manifest_count_mismatch_fails_preflight(export):
    engine, sql, receipt, _ = export
    receipt['tables']['weird, "table'] = 3
    with pytest.raises(IntegrityError):
        load_export(engine, sql.encode(), receipt)
    assert rows(engine) == []
