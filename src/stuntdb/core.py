"""Fixed-point parent closure; source rows are never modified."""
from collections import deque
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from stuntdb.keystore import temporary_keys
from stuntdb.literals import JSONDocument, export_value, select_rows


class IntegrityError(ValueError):
    pass


@dataclass
class Slice:
    metadata: sa.MetaData
    rows: dict[str, list[dict[str, Any]]]
    masked: bool = False


def reflect(connection: sa.Connection) -> sa.MetaData:
    metadata = sa.MetaData()
    metadata.reflect(bind=connection)
    if connection.dialect.name == "mysql":
        spatial = connection.execute(sa.text(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND data_type IN "
            "('geometry','point','linestring','polygon','multipoint',"
            "'multilinestring','multipolygon','geometrycollection')"))
        for table_name, column_name in spatial:
            if table_name in metadata.tables:
                metadata.tables[table_name].c[column_name].info["mysql_spatial"] = True
    return metadata


def extract(connection: sa.Connection, table_name: str, column: str, value: str,
            max_rows: int = 10000, children: int = 0) -> Slice:
    with temporary_keys() as keys:
        return _extract(connection, table_name, column, value, max_rows, children, keys)


def _extract(connection, table_name, column, value, max_rows, children, keys):
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    if children < 0:
        raise ValueError("children must be nonnegative")
    metadata = reflect(connection)
    if table_name not in metadata.tables:
        raise ValueError("Unknown seed table")
    table = metadata.tables[table_name]
    if column not in table.c:
        raise ValueError("Unknown seed column")
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in metadata.tables}
    work = deque()
    count = 0
    child_work = deque()
    incoming: dict[str, list] = {name: [] for name in metadata.tables}
    for current in metadata.tables.values():
        for fk in current.foreign_key_constraints:
            incoming[fk.referred_table.name].append(fk)

    def schedule_children(target, row, depth):
        key = tuple(row[c.name] for c in target.primary_key.columns)
        if depth < children and keys.shallower(target.name, key, depth):
            child_work.append((target, row, depth))

    def add(target, records, depth=None):
        nonlocal count
        primary_columns = list(target.primary_key.columns)
        if not primary_columns:
            raise ValueError(f"Table {target.name} requires a primary key")
        for record in records:
            row = dict(record)
            for column in target.c:
                if isinstance(column.type, sa.JSON) and row[column.name] is not None:
                    row[column.name] = JSONDocument(row[column.name])
            key = tuple(row[c.name] for c in primary_columns)
            if depth is not None:
                schedule_children(target, row, depth)
            if not keys.add(target.name, key):
                continue
            count += 1
            if count > max_rows:
                raise ValueError("Row ceiling exceeded; no output written")
            rows[target.name].append(row)
            work.append((target, row))

    add(table, connection.execute(select_rows(table).where(table.c[column] == value)).mappings(), depth=0)
    while work or child_work:
        if not work:
            current, row, depth = child_work.popleft()
            for fk in incoming[current.name]:
                elements = list(fk.elements)
                values = [row[e.column.name] for e in elements]
                if any(v is None for v in values):
                    continue
                child = elements[0].parent.table
                condition = sa.and_(*(e.parent == v for e, v in zip(elements, values)))
                add(child, connection.execute(select_rows(child).where(condition)).mappings(),
                    depth=depth + 1)
            continue
        current, row = work.popleft()
        for constraint in current.foreign_key_constraints:
            elements = list(constraint.elements)
            values = [row[e.parent.name] for e in elements]
            # SQL MATCH SIMPLE: any NULL exempts the entire composite reference.
            if any(v is None for v in values):
                continue
            parent = elements[0].column.table
            condition = sa.and_(*(e.column == v for e, v in zip(elements, values)))
            add(parent, connection.execute(select_rows(parent).where(condition)).mappings())
    result = Slice(metadata, rows)
    verify(result)
    return result


def verify(result: Slice) -> None:
    for name, records in result.rows.items():
        for fk in result.metadata.tables[name].foreign_key_constraints:
            elements = list(fk.elements)
            parent = elements[0].column.table.name
            available = {tuple(r[e.column.name] for e in elements) for r in result.rows[parent]}
            for row in records:
                key = tuple(row[e.parent.name] for e in elements)
                if all(v is not None for v in key) and key not in available:
                    raise IntegrityError(f"Unresolved foreign key in {name}")


def sql_export(result: Slice, dialect) -> str:
    """Export data for a pre-existing schema, validating before serialization."""
    verify(result)
    mysql = dialect.name == "mysql"
    if dialect.name not in {"mysql", "sqlite"}:
        raise ValueError("Unsupported output dialect")
    if mysql:
        from sqlalchemy.dialects.mysql import dialect as mysql_dialect
        dialect = mysql_dialect(paramstyle="named")
    status = "MASKED" if result.masked else "UNMASKED"
    lines = [f"-- stuntdb: {status} development data; requires an existing schema"]
    if mysql:
        lines += ["SET @stuntdb_old_fk_checks = @@FOREIGN_KEY_CHECKS;",
                  "SET FOREIGN_KEY_CHECKS = 0;", "START TRANSACTION;"]
    else:
        lines += ["PRAGMA foreign_keys = OFF;", "BEGIN TRANSACTION;"]
    for name, records in result.rows.items():
        table = result.metadata.tables[name]
        for row in records:
            lines.append(str(table.insert().values(**{k: export_value(table.c[k], v, mysql)
                                                  for k, v in row.items()
                                                  if table.c[k].computed is None}).compile(
                dialect=dialect, compile_kwargs={"literal_binds": True})) + ";")
    lines.append("COMMIT;")
    if mysql:
        lines.append("SET FOREIGN_KEY_CHECKS = @stuntdb_old_fk_checks;")
    else:
        lines.append("PRAGMA foreign_keys = ON;")
    return "\n".join(lines) + "\n"
