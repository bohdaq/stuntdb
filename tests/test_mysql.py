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
        for name in ["child", "parent", "b", "a"]:
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
        for line in script.splitlines():
            if not line.startswith("--"):
                c.exec_driver_sql(line)
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
