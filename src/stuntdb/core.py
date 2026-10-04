"""Fixed-point parent closure; source rows are never modified."""
from collections import deque
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa


class IntegrityError(ValueError):
    pass


@dataclass
class Slice:
    metadata: sa.MetaData
    rows: dict[str, list[dict[str, Any]]]


def reflect(connection: sa.Connection) -> sa.MetaData:
    metadata = sa.MetaData()
    metadata.reflect(bind=connection)
    return metadata


def extract(connection: sa.Connection, table_name: str, column: str, value: str,
            max_rows: int = 10000) -> Slice:
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    metadata = reflect(connection)
    if table_name not in metadata.tables:
        raise ValueError("Unknown seed table")
    table = metadata.tables[table_name]
    if column not in table.c:
        raise ValueError("Unknown seed column")
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in metadata.tables}
    seen: dict[str, set[tuple]] = {name: set() for name in metadata.tables}
    work = deque()
    count = 0

    def add(target, records):
        nonlocal count
        keys = list(target.primary_key.columns)
        if not keys:
            raise ValueError(f"Table {target.name} requires a primary key")
        for record in records:
            row = dict(record)
            key = tuple(row[c.name] for c in keys)
            if key in seen[target.name]:
                continue
            count += 1
            if count > max_rows:
                raise ValueError("Row ceiling exceeded; no output written")
            seen[target.name].add(key)
            rows[target.name].append(row)
            work.append((target, row))

    add(table, connection.execute(sa.select(table).where(table.c[column] == value)).mappings())
    while work:
        current, row = work.popleft()
        for constraint in current.foreign_key_constraints:
            elements = list(constraint.elements)
            values = [row[e.parent.name] for e in elements]
            # SQL MATCH SIMPLE: any NULL exempts the entire composite reference.
            if any(v is None for v in values):
                continue
            parent = elements[0].column.table
            condition = sa.and_(*(e.column == v for e, v in zip(elements, values)))
            add(parent, connection.execute(sa.select(parent).where(condition)).mappings())
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
    lines = ["-- stuntdb: UNMASKED development data; requires an existing schema"]
    if mysql:
        lines += ["SET @stuntdb_old_fk_checks = @@FOREIGN_KEY_CHECKS;",
                  "SET FOREIGN_KEY_CHECKS = 0;", "START TRANSACTION;"]
    else:
        lines += ["PRAGMA foreign_keys = OFF;", "BEGIN TRANSACTION;"]
    for name, records in result.rows.items():
        table = result.metadata.tables[name]
        for row in records:
            lines.append(str(table.insert().values(**{k: v for k, v in row.items() if table.c[k].computed is None}).compile(
                dialect=dialect, compile_kwargs={"literal_binds": True})) + ";")
    lines.append("COMMIT;")
    if mysql:
        lines.append("SET FOREIGN_KEY_CHECKS = @stuntdb_old_fk_checks;")
    else:
        lines.append("PRAGMA foreign_keys = ON;")
    return "\n".join(lines) + "\n"
