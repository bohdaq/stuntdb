"""Dedicated disposable MySQL database required; never use a real source URL."""
import os

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from stuntdb.cli import app
from stuntdb.core import extract, sql_export
from stuntdb.source import consistent_source, engine_for

pytestmark = pytest.mark.mysql


@pytest.fixture()
def mysql_engine():
    url = os.environ.get("STUNTDB_TEST_MYSQL_URL")
    if not url:
        pytest.skip("STUNTDB_TEST_MYSQL_URL is not set")
    engine = engine_for(url)
    # An explicit test database name guards against accidental destructive use.
    if engine.url.database != "stuntdb_test":
        pytest.fail("Integration tests require a dedicated stuntdb_test database")
    with engine.begin() as c:
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
        for name in ["typed_values", "child", "parent", "b", "a"]:
            c.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
        c.exec_driver_sql("CREATE TABLE a (id INT PRIMARY KEY, b_id INT, note VARCHAR(200), derived INT GENERATED ALWAYS AS (id + 1) STORED) ENGINE=InnoDB")
        c.exec_driver_sql("CREATE TABLE b (id INT PRIMARY KEY, a_id INT, FOREIGN KEY(a_id) REFERENCES a(id)) ENGINE=InnoDB")
        c.exec_driver_sql("ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id)")
        c.exec_driver_sql("INSERT INTO a (id, note) VALUES (1, %s), (9, 'unselected')", ("50% O'Reilly \\ path",))
        c.exec_driver_sql("INSERT INTO b VALUES (2,1)")
        c.exec_driver_sql("UPDATE a SET b_id = 2 WHERE id = 1")
        c.exec_driver_sql("CREATE TABLE parent (x INT, y INT, PRIMARY KEY(x,y)) ENGINE=InnoDB")
        c.exec_driver_sql("CREATE TABLE child (id INT PRIMARY KEY, x INT, y INT, FOREIGN KEY(x,y) REFERENCES parent(x,y)) ENGINE=InnoDB")
        c.exec_driver_sql("INSERT INTO parent VALUES (1,2), (1,3)")
        c.exec_driver_sql("INSERT INTO child VALUES (1,1,2), (2,1,NULL)")
    yield engine
    engine.dispose()


def test_mysql_cycle_export_roundtrip(mysql_engine):
    with consistent_source(mysql_engine) as c:
        result = extract(c, "a", "id", "1")
    assert len(result.rows["a"]) == len(result.rows["b"]) == 1
    script = sql_export(result, mysql_engine.dialect)
    with mysql_engine.connect() as c:
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
        c.exec_driver_sql("DELETE FROM b")
        c.exec_driver_sql("DELETE FROM a")
        c.commit()
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
        # Each generated statement occupies one line for this fixture.
        # Execute the SQL file without DBAPI placeholder interpolation.
        with c.connection.driver_connection.cursor() as cursor:
            for line in script.splitlines():
                if not line.startswith("--"):
                    cursor.execute(line)
        assert c.exec_driver_sql("SELECT id, b_id, note, derived FROM a").one() == (1, 2, "50% O'Reilly \\ path", 2)
        assert c.exec_driver_sql("SELECT @@FOREIGN_KEY_CHECKS").scalar_one() == 1
        assert c.exec_driver_sql("SELECT COUNT(*) FROM a LEFT JOIN b ON a.b_id=b.id WHERE b.id IS NULL").scalar_one() == 0


def test_mysql_composite(mysql_engine):
    with consistent_source(mysql_engine) as c:
        assert extract(c, "child", "id", "1").rows["parent"] == [{"x": 1, "y": 2}]
        assert extract(c, "child", "id", "2").rows["parent"] == []


def test_mysql_cli(mysql_engine, tmp_path):
    url = mysql_engine.url.render_as_string(hide_password=False)
    output = tmp_path / "slice.sql"
    result = CliRunner().invoke(app, ["snapshot", url, "--seed", "a.id=1", "--allow-unmasked", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_mysql_children(mysql_engine):
    with consistent_source(mysql_engine) as c:
        result = extract(c, "parent", "x", "1", children=1)
        assert len(result.rows["parent"]) == 2
        # A partially NULL composite key is not a declared dependent of (1,2).
        assert result.rows["child"] == [{"id": 1, "x": 1, "y": 2}]
        cyclic = extract(c, "a", "id", "1", children=20)
        assert len(cyclic.rows["a"]) == len(cyclic.rows["b"]) == 1


def test_mysql_consistent_read_only_snapshot(mysql_engine):
    with consistent_source(mysql_engine) as c:
        before = extract(c, "a", "id", "1").rows["a"][0]["note"]
        with mysql_engine.begin() as writer:
            writer.exec_driver_sql("UPDATE a SET note = 'changed elsewhere' WHERE id = 1")
        assert extract(c, "a", "id", "1").rows["a"][0]["note"] == before
        with pytest.raises(sa.exc.DBAPIError):
            c.exec_driver_sql("INSERT INTO a (id) VALUES (100)")
    with mysql_engine.connect() as c:
        assert c.exec_driver_sql("SELECT note FROM a WHERE id=1").scalar_one() == "changed elsewhere"
        assert c.exec_driver_sql("SELECT COUNT(*) FROM a WHERE id=100").scalar_one() == 0


@pytest.mark.parametrize("sql_mode", ["", "NO_BACKSLASH_ESCAPES"])
def test_mysql_typed_values_roundtrip(mysql_engine, sql_mode):
    import json
    note = "雪\x00\n50% O'Reilly \\ path"
    blob = b"\x00\xff'\\"
    payload = {"nested": ["雪", None, True]}
    with mysql_engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE typed_values (id INT PRIMARY KEY, note TEXT, blob_value BLOB, payload JSON) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        c.exec_driver_sql("INSERT INTO typed_values VALUES (1, %s, %s, %s)",
                          (note, blob, json.dumps(payload)))
        c.exec_driver_sql("INSERT INTO typed_values (id, payload) VALUES (2, 'null'), (3, NULL)")
    with consistent_source(mysql_engine) as c:
        results = [extract(c, "typed_values", "id", str(i)) for i in (1, 2, 3)]
    with mysql_engine.connect() as c:
        old_mode = c.exec_driver_sql("SELECT @@SESSION.sql_mode").scalar_one()
        c.exec_driver_sql("DELETE FROM typed_values")
        c.commit()
        c.exec_driver_sql("SET SESSION sql_mode = %s", (sql_mode,))
        try:
            with c.connection.driver_connection.cursor() as cursor:
                for result in results:
                    for line in sql_export(result, mysql_engine.dialect).splitlines():
                        if not line.startswith("--"):
                            cursor.execute(line)
            row = c.exec_driver_sql("SELECT note, blob_value, payload FROM typed_values WHERE id=1").one()
            assert row[0] == note
            assert row[1] == blob
            assert json.loads(row[2]) == payload
            assert c.exec_driver_sql("SELECT id, payload IS NULL FROM typed_values WHERE id>1 ORDER BY id").all() == [(2, 0), (3, 1)]
        finally:
            c.exec_driver_sql("SET SESSION sql_mode = %s", (old_mode,))


def test_mysql_masked_natural_key_roundtrip(mysql_engine):
    from stuntdb.masking import mask_slice
    with mysql_engine.begin() as c:
        c.exec_driver_sql('DROP TABLE IF EXISTS masked_order')
        c.exec_driver_sql('DROP TABLE IF EXISTS masked_user')
        c.exec_driver_sql('CREATE TABLE masked_user (email VARCHAR(100) PRIMARY KEY, notes TEXT, payload JSON, photo BLOB) ENGINE=InnoDB')
        c.exec_driver_sql('CREATE TABLE masked_order (id INT PRIMARY KEY, email VARCHAR(100), FOREIGN KEY(email) REFERENCES masked_user(email)) ENGINE=InnoDB')
        c.exec_driver_sql("INSERT INTO masked_user VALUES ('canary@example.invalid', 'Private Canary Notes', '{\"secret\":\"JSON Canary\"}', X'1234')")
        c.exec_driver_sql("INSERT INTO masked_order VALUES (1, 'canary@example.invalid')")
    with consistent_source(mysql_engine) as c:
        result = mask_slice(extract(c, 'masked_order', 'id', '1'), 'test-only-project-salt-1234567890')
    script = sql_export(result, mysql_engine.dialect)
    with mysql_engine.connect() as c:
        c.exec_driver_sql('DELETE FROM masked_order')
        c.exec_driver_sql('DELETE FROM masked_user')
        c.commit()
        with c.connection.driver_connection.cursor() as cursor:
            for line in script.splitlines():
                if not line.startswith('--'):
                    cursor.execute(line)
        assert c.exec_driver_sql('SELECT COUNT(*) FROM masked_order JOIN masked_user USING(email)').scalar_one() == 1
        row = c.exec_driver_sql('SELECT email, notes, payload, photo FROM masked_user').one()
        assert row[0] != 'canary@example.invalid'
        assert row[1:] == (None, None, None)
        c.exec_driver_sql('DROP TABLE masked_order')
        c.exec_driver_sql('DROP TABLE masked_user')


def test_mysql_reviewed_config_cli(mysql_engine, tmp_path):
    import json
    with mysql_engine.begin() as c:
        c.exec_driver_sql('DROP TABLE IF EXISTS reviewed_person')
        c.exec_driver_sql("CREATE TABLE reviewed_person (id INT PRIMARY KEY, email VARCHAR(100), status ENUM('active','inactive') NOT NULL) ENGINE=InnoDB")
        c.exec_driver_sql("INSERT INTO reviewed_person VALUES (1,'canary@example.invalid','active')")
    config = tmp_path / 'stuntdb.json'
    url = mysql_engine.url.render_as_string(hide_password=False)
    runner = CliRunner()
    assert runner.invoke(app, ['init', url, '-o', str(config)]).exit_code == 0
    settings = json.loads(config.read_text())
    settings['seed'] = 'reviewed_person.id=1'
    output = tmp_path / 'snapshot.sql'
    config.write_text(json.dumps(settings))
    args = ['snapshot', '--config', str(config), '-o', str(output)]
    env = {'STUNTDB_SOURCE': url, 'STUNTDB_SALT': 'test-only-project-salt-1234567890'}
    assert runner.invoke(app, args, env=env).exit_code == 1
    assert not output.exists()
    settings['rules']['reviewed_person.status'] = 'keep'
    settings['rules']['reviewed_person.email'] = 'email'
    config.write_text(json.dumps(settings))
    result = runner.invoke(app, args, env=env)
    assert result.exit_code == 0, result.output
    assert 'canary@example.invalid'.encode().hex() not in output.read_text()
    with mysql_engine.begin() as c:
        c.exec_driver_sql('DROP TABLE reviewed_person')


def test_mysql_provider_roundtrip(mysql_engine):
    from datetime import date
    from decimal import Decimal
    from stuntdb.masking import mask_slice
    with mysql_engine.begin() as c:
        c.exec_driver_sql('DROP TABLE IF EXISTS provider_order')
        c.exec_driver_sql('DROP TABLE IF EXISTS provider_person')
        c.exec_driver_sql('CREATE TABLE provider_person (email VARCHAR(100) PRIMARY KEY, name VARCHAR(100), phone VARCHAR(30), birth_date DATE, ssn INT, amount DECIMAL(8,2)) ENGINE=InnoDB')
        c.exec_driver_sql('CREATE TABLE provider_order (id INT PRIMARY KEY, email VARCHAR(100), FOREIGN KEY(email) REFERENCES provider_person(email)) ENGINE=InnoDB')
        c.exec_driver_sql("INSERT INTO provider_person VALUES ('canary@example.org','Source Canary Person','+48 (123) 456-789','1960-02-29',123456789,-123.45)")
        c.exec_driver_sql("INSERT INTO provider_order VALUES (1,'canary@example.org')")
    rules = {'provider_person.email': 'email', 'provider_person.name': 'name',
             'provider_person.phone': 'phone', 'provider_person.birth_date': 'date',
             'provider_person.ssn': 'integer', 'provider_person.amount': 'number'}
    with consistent_source(mysql_engine) as c:
        original = extract(c, 'provider_order', 'id', '1')
        masked = mask_slice(original, 'test-only-project-salt-1234567890', rules)
    with mysql_engine.connect() as c:
        c.exec_driver_sql('DELETE FROM provider_order')
        c.exec_driver_sql('DELETE FROM provider_person')
        c.commit()
        with c.connection.driver_connection.cursor() as cursor:
            for line in sql_export(masked, mysql_engine.dialect).splitlines():
                if not line.startswith('--'):
                    cursor.execute(line)
        row = c.exec_driver_sql('SELECT email, name, phone, birth_date, ssn, amount FROM provider_person').one()
        assert row[0].endswith('@example.invalid')
        assert row[1] != 'Source Canary Person'
        assert row[2] != '+48 (123) 456-789'
        assert row[3] != date(1960, 2, 29)
        assert row[4] != 123456789
        assert row[5] != Decimal('-123.45') and row[5].as_tuple().exponent == -2
        assert c.exec_driver_sql('SELECT COUNT(*) FROM provider_order JOIN provider_person USING(email)').scalar_one() == 1
        assert c.exec_driver_sql('SELECT @@FOREIGN_KEY_CHECKS').scalar_one() == 1
        c.exec_driver_sql('DROP TABLE provider_order')
        c.exec_driver_sql('DROP TABLE provider_person')


def test_mysql_transactional_loader(mysql_engine, tmp_path):
    import json
    import hashlib
    from stuntdb.manifest import build_manifest
    with consistent_source(mysql_engine) as c:
        result = extract(c, 'a', 'id', '1')
    sql = sql_export(result, mysql_engine.dialect)
    receipt = build_manifest(result, sql, 'mysql', {})
    path = tmp_path / 'load.sql'
    manifest = tmp_path / 'load.sql.manifest.json'
    url = mysql_engine.url.render_as_string(hide_password=False)
    with mysql_engine.begin() as c:
        c.exec_driver_sql('SET FOREIGN_KEY_CHECKS=0')
        for table in result.metadata.tables.values():
            c.execute(table.delete())
        c.exec_driver_sql('SET FOREIGN_KEY_CHECKS=1')
    # Same valid format/counts/checksum, with an unresolved FK; both inserts roll back.
    broken = sql.replace('(1, 2,', '(1, 999,')
    assert broken != sql
    path.write_text(broken)
    bad_receipt = dict(receipt, export_sha256=hashlib.sha256(broken.encode()).hexdigest())
    manifest.write_text(json.dumps(bad_receipt))
    runner = CliRunner()
    args = ['load', str(path), '--target', url]
    assert runner.invoke(app, args).exit_code == 3
    with mysql_engine.connect() as c:
        assert c.exec_driver_sql('SELECT COUNT(*) FROM a').scalar_one() == 0
        assert c.exec_driver_sql('SELECT COUNT(*) FROM b').scalar_one() == 0
    path.write_text(sql)
    manifest.write_text(json.dumps(receipt))
    loaded = runner.invoke(app, args)
    assert loaded.exit_code == 0, loaded.output
    assert runner.invoke(app, args).exit_code == 3
    with mysql_engine.connect() as c:
        assert c.exec_driver_sql('SELECT id, b_id, derived FROM a').one() == (1, 2, 2)
        assert c.exec_driver_sql('SELECT @@FOREIGN_KEY_CHECKS').scalar_one() == 1


def test_json_spatial_generated_loader_roundtrip(mysql_engine, tmp_path):
    import json
    from stuntdb.core import reflect
    from stuntdb.literals import JSONDocument
    from stuntdb.manifest import build_manifest, verify_database
    from stuntdb.loading import load_export
    from stuntdb.masking import mask_slice
    from stuntdb.source import dialect_name
    with mysql_engine.begin() as c:
        c.exec_driver_sql('DROP TABLE IF EXISTS server_types')
        c.exec_driver_sql('CREATE TABLE server_types (id INT PRIMARY KEY, payload JSON NOT NULL, location POINT, derived INT GENERATED ALWAYS AS (id + 1) STORED) ENGINE=InnoDB')
        c.exec_driver_sql("INSERT INTO server_types (id, payload, location) VALUES (1, 'null', ST_GeomFromText('POINT(1 2)', 4326))")
        c.exec_driver_sql("INSERT INTO server_types (id, payload) VALUES (2, '{\"canary\":\"secret\"}')")
    try:
        with consistent_source(mysql_engine) as c:
            metadata = reflect(c)
            assert isinstance(metadata.tables['server_types'].c.payload.type, sa.JSON)
            result = extract(c, 'server_types', 'id', '1')
            assert result.rows['server_types'][0]['payload'] == JSONDocument('null')
            assert metadata.tables['server_types'].c.derived.computed is not None
            raw_location = result.rows['server_types'][0]['location']
        script = sql_export(result, mysql_engine.dialect)
        receipt = build_manifest(result, script, mysql_engine.dialect.name, {})
        assert receipt['dialect'] == dialect_name(mysql_engine.dialect)
        wrong_family = dict(receipt, dialect='mysql' if receipt['dialect'] == 'mariadb' else 'mariadb')
        from stuntdb.core import IntegrityError
        with pytest.raises(IntegrityError):
            load_export(mysql_engine, script.encode(), wrong_family)
        with mysql_engine.begin() as c:
            c.exec_driver_sql('SET FOREIGN_KEY_CHECKS=0')
            for table in result.metadata.tables.values():
                c.execute(table.delete())
            c.exec_driver_sql('SET FOREIGN_KEY_CHECKS=1')
        load_export(mysql_engine, script.encode(), receipt)
        with consistent_source(mysql_engine) as c:
            restored = verify_database(c, receipt)
            row = restored.rows['server_types'][0]
            assert row['payload'] == JSONDocument('null')
            assert row['location'] == raw_location
            assert row['derived'] == 2
        with mysql_engine.begin() as c:
            c.exec_driver_sql('CREATE TABLE server_json (id INT PRIMARY KEY, payload JSON NOT NULL) ENGINE=InnoDB')
            c.exec_driver_sql("INSERT INTO server_json VALUES (1, '{\"canary\":\"secret\"}')")
        with consistent_source(mysql_engine) as c:
            subset = extract(c, 'server_json', 'id', '1')
            masked = mask_slice(subset, 'test-only-project-salt-1234567890')
            assert masked.rows['server_json'][0]['payload'] == JSONDocument('null')
    finally:
        with mysql_engine.begin() as c:
            c.exec_driver_sql('DROP TABLE IF EXISTS server_json')
            c.exec_driver_sql('DROP TABLE server_types')
