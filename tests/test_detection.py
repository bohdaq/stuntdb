import json

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from stuntdb.cli import app
from stuntdb.config import generated_config, load_config
from stuntdb.core import reflect
from stuntdb.detection import formats, sample_suggestions


@pytest.mark.parametrize('value,kind', [
    ('alice@example.org', 'email'), ('message alice@example.org here', 'embedded_email'),
    ('+48 (123) 456-789', 'phone'), ('123456789', 'numeric_identifier'),
    ('123-45-6789', 'ssn'), ('192.0.2.1', 'ip_address'),
    ('4111 1111 1111 1111', 'payment_card')])
def test_recognized_formats(value, kind):
    assert kind in formats(value)


def test_invalid_card_and_ordinary_text():
    assert 'payment_card' not in formats('4111111111111112')
    assert not formats('ordinary text')


@pytest.fixture
def source(tmp_path):
    url = f'sqlite:///{tmp_path / "source.db"}'
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE entry (id INTEGER PRIMARY KEY, opaque VARCHAR(100), contact VARCHAR(100), mixed VARCHAR(100), number BIGINT)')
        for i in range(3):
            c.exec_driver_sql('INSERT INTO entry VALUES (?, ?, ?, ?, ?)',
                (i, f'canary{i}@example.org', '+48 (123) 456-789', 'hidden@example.org' if i == 0 else 'ordinary', 123456789))
    return engine, url


def test_cli_value_free_hints_and_review(source, tmp_path):
    _, url = source
    path = tmp_path / 'config.json'
    result = CliRunner().invoke(app, ['init', url, '-o', str(path)])
    assert result.exit_code == 0, result.output
    settings = load_config(path)
    assert settings['rules']['entry.opaque'] == 'email'
    assert settings['rules']['entry.contact'] == 'phone'
    assert settings['rules']['entry.mixed'] == 'review'
    assert settings['rules']['entry.number'] == 'review'
    assert settings['detection']['findings']['entry.opaque']['confidence'] == 'high'
    for secret in ('canary0', 'hidden@example.org', '+48', '123456789', url):
        assert secret not in path.read_text() + result.output
    settings['rules']['entry.mixed'] = 'clear'
    path.write_text(json.dumps(settings))
    assert load_config(path)['rules']['entry.mixed'] == 'clear'


def test_small_sample_requires_review_and_zero_is_schema_only(source, tmp_path):
    _, url = source
    runner = CliRunner()
    for size in (0, 1):
        path = tmp_path / f'{size}.json'
        assert runner.invoke(app, ['init', url, '-o', str(path), '--sample-rows', str(size)]).exit_code == 0
        settings = load_config(path)
        assert settings['rules']['entry.opaque'] == ('auto' if size == 0 else 'review')
        assert ('detection' in settings) == bool(size)


def test_sampling_has_query_and_cell_bounds(source):
    engine, _ = source
    queries = []
    sa.event.listen(engine, 'before_cursor_execute', lambda c, cur, sql, params, ctx, many: queries.append((sql, params)))
    with engine.connect() as c:
        m = reflect(c)
        queries.clear()
        settings = generated_config(m)
        sample_suggestions(c, m, settings, 2)
    assert len(queries) == 1
    sql, params = queries[0]
    assert 'substr' in sql and 'LIMIT' in sql
    assert 513 in params and 2 in params


def test_malformed_detection_metadata_fails(source, tmp_path):
    _, url = source
    path = tmp_path / 'config.json'
    assert CliRunner().invoke(app, ['init', url, '-o', str(path)]).exit_code == 0
    settings = json.loads(path.read_text())
    settings['detection']['findings']['entry.opaque']['sample'] = 'private'
    path.write_text(json.dumps(settings))
    with pytest.raises(ValueError):
        load_config(path)


def test_global_sampling_budget():
    engine = sa.create_engine('sqlite://')
    metadata = sa.MetaData()
    for name in ('a', 'b'):
        sa.Table(name, metadata, sa.Column('id', sa.Integer, primary_key=True),
                 *(sa.Column(f'c{i}', sa.String(100)) for i in range(19)))
    metadata.create_all(engine)
    with engine.begin() as c:
        for table in metadata.tables.values():
            c.execute(table.insert(), [{'id': i, **{f'c{j}': 'ordinary' for j in range(19)}} for i in range(600)])
    queries = []
    sa.event.listen(engine, 'before_cursor_execute', lambda c, cur, sql, params, ctx, many: queries.append((sql, params)))
    with engine.connect() as c:
        sample_suggestions(c, metadata, generated_config(metadata), 1000)
    assert len(queries) == 1  # 500 rows × 20 columns exhaust the global cell budget.
    assert queries[0][1][-2] == 500
