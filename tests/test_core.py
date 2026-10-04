import sqlalchemy as sa
import pytest
from typer.testing import CliRunner
from stuntdb.cli import app
from stuntdb.core import extract, sql_export, IntegrityError


def fixture():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE a (id INTEGER PRIMARY KEY, b_id INTEGER REFERENCES b(id))")
        c.exec_driver_sql("CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES a(id))")
        c.exec_driver_sql("INSERT INTO a VALUES (1, 2), (9, NULL)")
        c.exec_driver_sql("INSERT INTO b VALUES (2, 1)")
    return engine


def test_cycle_and_restore():
    engine = fixture()
    with engine.connect() as c:
        result = extract(c, "a", "id", "1")
    assert result.rows == {"a": [{"id": 1, "b_id": 2}], "b": [{"id": 2, "a_id": 1}]}
    target = fixture()
    with target.connect() as c:
        c.exec_driver_sql("DELETE FROM a")
        c.exec_driver_sql("DELETE FROM b")
        c.commit()
        raw = c.connection.driver_connection
        raw.executescript(sql_export(result, engine.dialect))
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert raw.execute("SELECT COUNT(*) FROM a").fetchone() == (1,)


def test_row_ceiling_and_missing_parent():
    engine = fixture()
    with engine.connect() as c:
        with pytest.raises(ValueError, match="ceiling"):
            extract(c, "a", "id", "1", 1)
        c.exec_driver_sql("DELETE FROM b")
        with pytest.raises(IntegrityError):
            extract(c, "a", "id", "1")


def test_composite_and_nullable_reference():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE parent (x INTEGER, y INTEGER, PRIMARY KEY(x,y))")
        c.exec_driver_sql("CREATE TABLE child (id INTEGER PRIMARY KEY, x INTEGER, y INTEGER, FOREIGN KEY(x,y) REFERENCES parent(x,y))")
        c.exec_driver_sql("INSERT INTO parent VALUES (1,2), (1,3)")
        c.exec_driver_sql("INSERT INTO child VALUES (1,1,2), (2,1,NULL)")
        result = extract(c, "child", "id", "1")
        assert result.rows["parent"] == [{"x": 1, "y": 2}]
        assert extract(c, "child", "id", "2").rows["parent"] == []


def test_masking_gate(tmp_path):
    output = tmp_path / "data.sql"
    result = CliRunner().invoke(app, ["snapshot", "sqlite://", "--seed", "a.id=1", "-o", str(output)])
    assert result.exit_code == 1
    assert not output.exists()


def test_self_reference():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE node (id INTEGER PRIMARY KEY, parent INTEGER REFERENCES node(id))")
        c.exec_driver_sql("INSERT INTO node VALUES (1,1), (2,1)")
        assert extract(c, "node", "id", "1").rows["node"] == [{"id": 1, "parent": 1}]


def test_inspect_reports_cycles():
    from stuntdb.schema import describe
    engine = fixture()
    with engine.connect() as c:
        metadata = sa.MetaData()
        metadata.reflect(bind=c)
    assert describe(metadata)["cycles"] == [["a", "b"]]


def test_mysql_export_percent_and_generated_columns():
    from sqlalchemy.dialects.mysql import pymysql
    from stuntdb.core import Slice
    metadata = sa.MetaData()
    sa.Table("example", metadata, sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("note", sa.String(100)),
             sa.Column("derived", sa.Integer, sa.Computed("id + 1")))
    result = Slice(metadata, {"example": [{"id": 1, "note": "50%", "derived": 2}]})
    script = sql_export(result, pymysql.dialect())
    assert "50%" in script and "50%%" not in script
    assert "derived" not in script


def test_cli_atomic_failure_and_success(tmp_path):
    database = tmp_path / "fixture.db"
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE node (id INTEGER PRIMARY KEY, parent INTEGER REFERENCES node(id))")
        c.exec_driver_sql("INSERT INTO node VALUES (1,1)")
    output = tmp_path / "slice.sql"
    output.write_text("existing output")
    runner = CliRunner()
    args = ["snapshot", f"sqlite:///{database}", "--seed", "node.id=1", "--allow-unmasked", "-o", str(output)]
    assert runner.invoke(app, args + ["--max-rows", "0"]).exit_code == 1
    assert output.read_text() == "existing output"
    assert runner.invoke(app, args).exit_code == 0
    assert "INSERT INTO node" in output.read_text()
    assert len(list(tmp_path.iterdir())) == 2
