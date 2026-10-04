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
    assert "353025" in script  # UTF-8 encoding of 50%, without SQL-mode escaping
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


@pytest.fixture()
def family():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE customer (id INTEGER PRIMARY KEY)")
        c.exec_driver_sql("CREATE TABLE product (id INTEGER PRIMARY KEY)")
        c.exec_driver_sql("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customer(id))")
        c.exec_driver_sql("CREATE TABLE item (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES product(id))")
        c.exec_driver_sql("INSERT INTO customer VALUES (1), (2)")
        c.exec_driver_sql("INSERT INTO product VALUES (10)")
        c.exec_driver_sql("INSERT INTO orders VALUES (3,1), (4,2)")
        c.exec_driver_sql("INSERT INTO item VALUES (5,3,10), (6,4,10)")
    return engine


def test_bounded_children_and_parent_closure(family):
    with family.connect() as c:
        zero = extract(c, "customer", "id", "1")
        assert zero.rows["orders"] == []
        one = extract(c, "customer", "id", "1", children=1)
        assert one.rows["orders"] == [{"id": 3, "customer_id": 1}]
        assert one.rows["item"] == []
        two = extract(c, "customer", "id", "1", children=2)
        assert len(two.rows["item"]) == 1
        assert two.rows["product"] == [{"id": 10}]
        assert len(two.rows["customer"]) == 1
        assert len(two.rows["orders"]) == 1


def test_child_cycles_terminate_and_ceiling_applies():
    engine = fixture()
    with engine.connect() as c:
        result = extract(c, "a", "id", "1", children=100)
        assert sum(map(len, result.rows.values())) == 2
        with pytest.raises(ValueError, match="ceiling"):
            extract(c, "a", "id", "1", max_rows=1, children=2)
        with pytest.raises(ValueError, match="nonnegative"):
            extract(c, "a", "id", "1", children=-1)


def test_child_ceiling_preserves_existing_output(family, tmp_path):
    database = tmp_path / "family.db"
    destination = sa.create_engine(f"sqlite:///{database}")
    with family.connect() as c, destination.connect() as target:
        c.connection.driver_connection.backup(target.connection.driver_connection)
    output = tmp_path / "slice.sql"
    output.write_text("previous")
    result = CliRunner().invoke(app, ["snapshot", f"sqlite:///{database}", "--seed", "customer.id=1",
        "--allow-unmasked", "--children", "2", "--max-rows", "2", "-o", str(output)])
    assert result.exit_code == 1
    assert output.read_text() == "previous"


def test_json_binary_and_unicode_roundtrip():
    from stuntdb.core import Slice
    metadata = sa.MetaData()
    table = sa.Table("values_table", metadata,
        sa.Column("id", sa.Integer, primary_key=True), sa.Column("data", sa.JSON),
        sa.Column("blob", sa.LargeBinary), sa.Column("note", sa.Text))
    expected = {"id": 1, "data": {"nested": ["雪", None, True]},
                "blob": b"\x00\xff'\\", "note": "雪\x00\n50% O'Reilly \\ path"}
    source = sa.create_engine("sqlite://")
    target = sa.create_engine("sqlite://")
    metadata.create_all(source)
    metadata.create_all(target)
    with source.begin() as c:
        c.execute(table.insert().values(**expected))
        result = extract(c, "values_table", "id", "1")
    with target.connect() as c:
        c.connection.driver_connection.executescript(sql_export(result, target.dialect))
        assert dict(c.execute(sa.select(table)).mappings().one()) == expected


def test_json_null_is_distinct_from_sql_null():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    table = sa.Table("jsons", metadata, sa.Column("id", sa.Integer, primary_key=True),
                     sa.Column("value", sa.JSON))
    metadata.create_all(engine)
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO jsons VALUES (1, 'null'), (2, NULL)")
        result = extract(c, "jsons", "id", "1")
        null_result = extract(c, "jsons", "id", "2")
    target = sa.create_engine("sqlite://")
    metadata.create_all(target)
    with target.connect() as c:
        raw = c.connection.driver_connection
        raw.executescript(sql_export(result, target.dialect))
        raw.executescript(sql_export(null_result, target.dialect))
        assert raw.execute("SELECT id, value IS NULL FROM jsons ORDER BY id").fetchall() == [(1, 0), (2, 1)]


def test_mysql_set_time_and_spatial_literals():
    from datetime import timedelta
    from sqlalchemy.dialects import mysql
    from stuntdb.literals import export_value
    dialect = mysql.dialect(paramstyle="named")
    column = sa.Column("features", mysql.SET("Commentaries", "Trailers"))
    value = export_value(column, {"Trailers", "Commentaries"}, True)
    assert "Commentaries,Trailers".encode().hex() in str(value.compile(dialect=dialect))
    column = sa.Column("duration", mysql.TIME())
    value = export_value(column, timedelta(hours=-27, microseconds=-123), True)
    assert "-27:00:00.000123".encode().hex() in str(value.compile(dialect=dialect))
    column = sa.Column("location", sa.LargeBinary(), info={"mysql_spatial": True})
    value = export_value(column, (4326).to_bytes(4, "little") + b"\x01\x02", True)
    assert str(value.compile(dialect=dialect)) == "ST_GeomFromWKB(X'0102', 4326)"


def test_mysql_year_export():
    from sqlalchemy.dialects import mysql
    from stuntdb.core import Slice
    metadata = sa.MetaData()
    sa.Table("films", metadata, sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("release_year", mysql.YEAR()))
    result = Slice(metadata, {"films": [{"id": 1, "release_year": 2006}]})
    assert "2006" in sql_export(result, mysql.dialect())
