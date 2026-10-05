import json
import sqlite3

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from stuntdb.cli import app
from stuntdb.manifest import load_manifest

RUNNER = CliRunner()
SALT = 'test-only-project-salt-1234567890'


@pytest.fixture
def exported(tmp_path):
    source = tmp_path / 'source.db'
    target = tmp_path / 'target.db'
    ddl = 'CREATE TABLE person (id INTEGER PRIMARY KEY, email VARCHAR(100), parent INTEGER REFERENCES person(id))'
    for path in (source, target):
        with sqlite3.connect(path) as c:
            c.execute(ddl)
    with sqlite3.connect(source) as c:
        c.execute('INSERT INTO person VALUES (1, ?, 1)', ('secret@example.org',))
    output = tmp_path / 'slice.sql'
    result = RUNNER.invoke(app, ['snapshot', f'sqlite:///{source}', '--seed', 'person.id=1', '-o', str(output)], env={'STUNTDB_SALT': SALT})
    assert result.exit_code == 0, result.output
    with sqlite3.connect(target) as c:
        c.executescript(output.read_text())
    return source, target, output, tmp_path / 'slice.sql.manifest.json'


def test_manifest_has_counts_resolved_strategies_and_no_values(exported):
    source, target, output, path = exported
    data = load_manifest(path)
    assert data['tables'] == {'person': 1}
    assert data['strategies'] == {'person.id': 'keep', 'person.email': 'token', 'person.parent': 'keep'}
    assert data['masked'] is True
    content = path.read_text()
    for secret in (SALT, str(source), 'secret@example.org', 'person.id=1'):
        assert secret not in content


def test_file_checksum_detects_tampering_without_executing(exported):
    _, _, output, path = exported
    args = ['verify', str(output), '--manifest', str(path)]
    assert RUNNER.invoke(app, args).exit_code == 0
    output.write_text(output.read_text() + 'DROP TABLE person;\n')
    assert RUNNER.invoke(app, args).exit_code == 3


def test_database_verify_and_leaks(exported):
    source, target, _, path = exported
    args = ['verify', f'sqlite:///{target}', '--manifest', str(path), '--reference-source', f'sqlite:///{source}', '--seed', 'person.id=1']
    result = RUNNER.invoke(app, args)
    assert result.exit_code == 0, result.output
    with sqlite3.connect(target) as c:
        c.execute("UPDATE person SET email = 'secret@example.org'")
    assert RUNNER.invoke(app, args).exit_code == 2
    with sqlite3.connect(target) as c:
        c.execute('UPDATE person SET parent = 999')
    assert RUNNER.invoke(app, args).exit_code == 3


@pytest.mark.parametrize('change', ['count', 'schema', 'coverage'])
def test_database_drift_and_manifest_coverage(exported, change):
    _, target, _, path = exported
    if change == 'coverage':
        data = json.loads(path.read_text())
        del data['strategies']['person.email']
        path.write_text(json.dumps(data))
    else:
        with sqlite3.connect(target) as c:
            c.execute('DELETE FROM person' if change == 'count' else 'ALTER TABLE person ADD COLUMN other INTEGER')
    result = RUNNER.invoke(app, ['verify', f'sqlite:///{target}', '--manifest', str(path)])
    assert result.exit_code == 3, result.output


def test_failed_manifest_staging_preserves_export(exported):
    source, _, output, _ = exported
    before = output.read_bytes()
    result = RUNNER.invoke(app, ['snapshot', f'sqlite:///{source}', '--seed', 'person.id=1', '-o', str(output), '--manifest', str(output.parent / 'missing' / 'receipt.json')], env={'STUNTDB_SALT': SALT})
    assert result.exit_code == 1
    assert output.read_bytes() == before
    assert not list(output.parent.glob('tmp*'))


def test_manifest_duplicate_fields_rejected(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError):
        load_manifest(path)
