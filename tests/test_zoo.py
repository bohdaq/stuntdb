"""Full sample databases and synthetic framework fixtures, on MySQL only."""
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

from stuntdb.core import extract, reflect, sql_export, verify
from stuntdb.config import generated_config, validate_schema
from stuntdb.masking import mask_slice
from stuntdb.literals import select_rows, JSONDocument
from stuntdb.schema import cycles
from stuntdb.source import consistent_source, engine_for

pytestmark = [pytest.mark.mysql, pytest.mark.zoo]


@pytest.fixture()
def zoo_engine():
    source = os.environ.get("STUNTDB_ZOO_URL")
    if not source:
        pytest.skip("STUNTDB_ZOO_URL is not set")
    engine = engine_for(source)
    if engine.url.database != "stuntdb_test":
        pytest.fail("Zoo tests require the disposable stuntdb_test database")
    yield engine
    engine.dispose()


def roundtrip(engine, result):
    verify(result)
    script = sql_export(result, engine.dialect)
    # Save base-table DDL, then rebuild the disposable schema without triggers or views.
    # This models the documented empty, matching target schema requirement.
    with engine.connect() as c:
        quote = engine.dialect.identifier_preparer.quote
        ddl = [c.exec_driver_sql(f"SHOW CREATE TABLE {quote(name)}").one()[1]
               for name in result.metadata.tables]
        c.exec_driver_sql("DROP DATABASE stuntdb_test")
        c.exec_driver_sql("CREATE DATABASE stuntdb_test CHARACTER SET utf8mb4")
        c.exec_driver_sql("USE stuntdb_test")
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        for statement in ddl:
            c.exec_driver_sql(statement)
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
        with c.connection.driver_connection.cursor() as cursor:
            for line in script.splitlines():
                if not line.startswith("--"):
                    cursor.execute(line)
        # Compare every restored row, including non-key types and empty tables.
        for name, expected in result.rows.items():
            table = result.metadata.tables[name]
            actual = [dict(r) for r in c.execute(select_rows(table)).mappings()]
            for row in actual:
                for column in table.c:
                    if isinstance(column.type, sa.JSON) and row[column.name] is not None:
                        row[column.name] = JSONDocument(row[column.name])
            key = lambda row: tuple(row[col.name] for col in table.primary_key)
            assert sorted(actual, key=key) == sorted(expected, key=key), name
        # Independent database anti-join check for each declared foreign key.
        for table in result.metadata.tables.values():
            for fk in table.foreign_key_constraints:
                elements = list(fk.elements)
                parent = fk.referred_table.alias()
                join = sa.and_(*(e.parent == parent.c[e.column.name] for e in elements))
                required = sa.and_(*(e.parent.is_not(None) for e in elements))
                missing = ~sa.exists(sa.select(1).select_from(parent).where(join))
                assert c.execute(sa.select(sa.func.count()).select_from(table).where(required, missing)).scalar_one() == 0
        assert c.exec_driver_sql("SELECT @@FOREIGN_KEY_CHECKS").scalar_one() == 1


def test_sample_database(zoo_engine):
    sample = os.environ.get("STUNTDB_ZOO_SAMPLE")
    if sample not in {"sakila", "employees"}:
        pytest.skip("Full sample dataset not selected")
    with consistent_source(zoo_engine) as c:
        if sample == "sakila":
            assert c.exec_driver_sql("SELECT COUNT(*) FROM rental").scalar_one() > 10000
            result = extract(c, "rental", "rental_id", "1", children=1)
            assert any(set(group) == {"staff", "store"} for group in cycles(result.metadata))
            assert result.rows["address"] and result.rows["film"] and result.rows["payment"]
        else:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM employees").scalar_one() >= 300000
            result = extract(c, "employees", "emp_no", "10001", children=1)
            assert result.rows["salaries"] and result.rows["titles"] and result.rows["departments"]
            assert len(result.rows["employees"]) == 1
    # These are explicit sample-test decisions, not suggested production rules.
    # Enum/SET/spatial and birth dates are retained, so this is not a fully
    # anonymized dataset. The test proves configured masking and restoration.
    settings = generated_config(result.metadata)
    reviewed = {name: ('keep' if action == 'review' else action)
                for name, action in settings['rules'].items()}
    settings['rules'] = reviewed
    validate_schema(settings, result.metadata)
    masked = mask_slice(result, 'test-only-project-salt-1234567890', reviewed)
    assert masked.masked
    changed = 'customer' if sample == 'sakila' else 'employees'
    assert masked.rows[changed] != result.rows[changed]
    roundtrip(zoo_engine, result)
    roundtrip(zoo_engine, masked)


def test_framework_shapes(zoo_engine):
    if os.environ.get("STUNTDB_ZOO_SAMPLE") != "frameworks":
        pytest.skip("Framework fixture not selected")
    with zoo_engine.connect() as c:
        c.exec_driver_sql("DROP DATABASE stuntdb_test")
        c.exec_driver_sql("CREATE DATABASE stuntdb_test CHARACTER SET utf8mb4")
        c.exec_driver_sql("USE stuntdb_test")
        script = (Path(__file__).parent / "zoo" / "frameworks.sql").read_text()
        script = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("--"))
        for statement in script.split(";"):
            if statement.strip():
                c.exec_driver_sql(statement)
        c.commit()
    with consistent_source(zoo_engine) as c:
        django = extract(c, "django_user", "id", "1", children=1)
        assert len(django.rows["django_membership"]) == 1
        assert len(django.rows["django_group"]) == 1
        # Generic object_id is NOT a database FK; its group 2 is not followed.
        assert django.rows["django_group"][0]["id"] == 1
        wordpress = extract(c, "wp_posts", "ID", "1", children=2)
        assert wordpress.rows["wp_users"] == []
        assert wordpress.rows["wp_postmeta"] == []
    # The two extracts are independent; retain both for a single restore.
    for name, rows in wordpress.rows.items():
        if rows:
            django.rows[name] = rows
    roundtrip(zoo_engine, django)
