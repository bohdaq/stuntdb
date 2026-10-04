import sqlalchemy as sa
import pytest
from hypothesis import given, strategies as st
from typer.testing import CliRunner

from stuntdb.cli import app
from stuntdb.core import Slice, extract, sql_export
from stuntdb.literals import JSONDocument
from stuntdb.masking import mask_slice, check_leaks, LeakError, MaskingError

SALT = 'test-only-project-salt-1234567890'


def fixture():
    metadata = sa.MetaData()
    sa.Table('user', metadata, sa.Column('email', sa.String(100), primary_key=True),
             sa.Column('name', sa.String(50)), sa.Column('notes', sa.Text),
             sa.Column('payload', sa.JSON), sa.Column('blob', sa.LargeBinary))
    sa.Table('orders', metadata, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('email', sa.String(100), sa.ForeignKey('user.email')))
    return Slice(metadata, {
        'user': [{'email': 'canary@example.invalid', 'name': 'Source Canary Person',
                  'notes': 'Private Canary Notes', 'payload': JSONDocument('{"secret":"JSON Canary"}'),
                  'blob': b'Binary Canary'}],
        'orders': [{'id': 1, 'email': 'canary@example.invalid'}]})


def test_determinism_domains_and_clearing():
    original = fixture()
    one = mask_slice(original, SALT)
    assert one.rows == mask_slice(original, SALT).rows
    assert one.rows != mask_slice(original, SALT + 'changed').rows
    assert one.rows['orders'][0]['email'] == one.rows['user'][0]['email']
    assert one.rows['user'][0]['name'] != original.rows['user'][0]['name']
    assert all(one.rows['user'][0][name] is None for name in ('notes', 'payload', 'blob'))
    assert original.rows['user'][0]['email'] == 'canary@example.invalid'
    assert one.masked


def test_masked_roundtrip():
    original = fixture()
    masked = mask_slice(original, SALT)
    engine = sa.create_engine('sqlite://')
    original.metadata.create_all(engine)
    with engine.connect() as c:
        raw = c.connection.driver_connection
        raw.executescript(sql_export(masked, engine.dialect))
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert raw.execute('SELECT email FROM user').fetchone()[0] == masked.rows['user'][0]['email']
        assert 'canary@example.invalid'.encode().hex() not in sql_export(masked, engine.dialect)


@pytest.mark.parametrize('value', ['Source Canary Person', 'copied: Source Canary Person'])
def test_leak_check_catches_copied_values(value):
    original = fixture()
    masked = mask_slice(original, SALT)
    masked.rows['user'][0]['notes'] = value
    with pytest.raises(LeakError):
        check_leaks(original, masked, [('user', 'name')])


def test_binary_leak():
    original = fixture()
    masked = mask_slice(original, SALT)
    masked.rows['user'][0]['blob'] = b'Binary Canary'
    with pytest.raises(LeakError):
        check_leaks(original, masked, [('user', 'blob')])


@pytest.mark.parametrize('kind', [sa.Enum('F', 'M'), sa.Integer(), sa.Date()])
def test_sensitive_unsupported_types_fail(kind):
    metadata = sa.MetaData()
    sa.Table('person', metadata, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('birth_date', kind))
    with pytest.raises(MaskingError):
        mask_slice(Slice(metadata, {'person': [{'id': 1, 'birth_date': None}]}), SALT)


def test_unique_clearing_fails():
    metadata = sa.MetaData()
    sa.Table('notes', metadata, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('text', sa.Text, nullable=False, unique=True))
    with pytest.raises(MaskingError, match='unique'):
        mask_slice(Slice(metadata, {'notes': [{'id': 1, 'text': 'one'}, {'id': 2, 'text': 'two'}]}), SALT)


def test_short_salt_and_token_collision_fail():
    with pytest.raises(MaskingError, match='Salt'):
        mask_slice(fixture(), 'short')
    metadata = sa.MetaData()
    sa.Table('short', metadata, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('name', sa.String(1)))
    rows = [{'id': i, 'name': str(i)} for i in range(17)]
    with pytest.raises(MaskingError, match='collision'):
        mask_slice(Slice(metadata, {'short': rows}), SALT)


@given(st.text(min_size=1, max_size=100, alphabet=st.characters(blacklist_categories=('Cs',))))
def test_deterministic_tokens_for_unicode(value):
    original = fixture()
    original.rows['user'][0]['name'] = value
    try:
        result = mask_slice(original, SALT)
    except LeakError:
        # Short source values can equal a token; fail closed is intentional.
        return
    assert result.rows == mask_slice(original, SALT).rows
    assert len(result.rows['user'][0]['name']) <= 50


def test_cli_masking_default_and_failed_run_preserves_output(tmp_path):
    database = tmp_path / 'source.db'
    engine = sa.create_engine(f'sqlite:///{database}')
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE user (id INTEGER PRIMARY KEY, email VARCHAR(100), notes TEXT)')
        c.exec_driver_sql("INSERT INTO user VALUES (1, 'canary@example.invalid', 'Private Canary Notes')")
    output = tmp_path / 'seed.sql'
    output.write_text('old output')
    args = ['snapshot', f'sqlite:///{database}', '--seed', 'user.id=1', '-o', str(output)]
    runner = CliRunner()
    failed = runner.invoke(app, args, env={'STUNTDB_SALT': ''})
    assert failed.exit_code == 1
    assert output.read_text() == 'old output'
    success = runner.invoke(app, args, env={'STUNTDB_SALT': SALT})
    assert success.exit_code == 0, success.output
    assert 'MASKED' in output.read_text()
    assert 'canary@example.invalid'.encode().hex() not in output.read_text()


def test_cli_leak_exit_code_and_atomic_output(monkeypatch, tmp_path):
    import stuntdb.cli as cli
    database = tmp_path / 'source.db'
    engine = sa.create_engine(f'sqlite:///{database}')
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE example (id INTEGER PRIMARY KEY)')
        c.exec_driver_sql('INSERT INTO example VALUES (1)')
    def leak(*args):
        raise LeakError('canary survived')
    monkeypatch.setattr(cli, 'mask_slice', leak)
    output = tmp_path / 'seed.sql'
    output.write_text('previous')
    result = CliRunner().invoke(app, ['snapshot', f'sqlite:///{database}', '--seed', 'example.id=1', '-o', str(output)], env={'STUNTDB_SALT': SALT})
    assert result.exit_code == 2
    assert output.read_text() == 'previous'
    assert 'canary survived' not in result.output


def test_computed_columns_fail_closed():
    metadata = sa.MetaData()
    sa.Table('person', metadata, sa.Column('id', sa.Integer, primary_key=True),
             sa.Column('derived', sa.Integer, sa.Computed('id + 1')))
    with pytest.raises(MaskingError, match='Computed'):
        mask_slice(Slice(metadata, {'person': [{'id': 1, 'derived': 2}]}), SALT)


def test_unique_prefix_collision_fails_closed():
    metadata = sa.MetaData()
    table = sa.Table('people', metadata, sa.Column('id', sa.Integer, primary_key=True),
                     sa.Column('name', sa.String(100)))
    sa.Index('unique_prefix', table.c.name, unique=True, mysql_length=1)
    rows = [{'id': i, 'name': chr(65 + i)} for i in range(17)]
    with pytest.raises(MaskingError, match='unique'):
        mask_slice(Slice(metadata, {'people': rows}), SALT)
