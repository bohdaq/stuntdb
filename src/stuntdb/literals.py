"""SQL-file values independent of driver interpolation and MySQL SQL modes."""
import json
from dataclasses import dataclass
from datetime import timedelta

import sqlalchemy as sa


@dataclass(frozen=True)
class JSONDocument:
    text: str


def select_rows(table):
    # Read raw JSON text so JSON null remains distinct from SQL NULL.
    return sa.select(*(sa.cast(c, sa.String()).label(c.name)
                       if isinstance(c.type, sa.JSON) else c for c in table.c))


def export_value(column, value, mysql: bool):
    if value is None:
        return None
    if isinstance(column.type, sa.JSON):
        value = value.text if isinstance(value, JSONDocument) else json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if isinstance(value, (set, frozenset)):
        value = ",".join(sorted(value))
    if isinstance(value, timedelta):
        microseconds = (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
        sign = "-" if microseconds < 0 else ""
        microseconds = abs(microseconds)
        hours, remaining = divmod(microseconds, 3600000000)
        minutes, remaining = divmod(remaining, 60000000)
        seconds, micros = divmod(remaining, 1000000)
        value = f"{sign}{hours:02}:{minutes:02}:{seconds:02}.{micros:06}"
    if mysql and column.info.get("mysql_spatial"):
        if not isinstance(value, bytes) or len(value) < 5:
            raise ValueError("Unsupported spatial value")
        srid = int.from_bytes(value[:4], "little")
        return sa.literal_column(f"ST_GeomFromWKB(X'{value[4:].hex()}', {srid})")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return sa.literal_column("X'" + bytes(value).hex() + "'")
    if isinstance(value, str):
        if mysql:
            # UTF-8 hex literals preserve quotes, percent, backslashes, newlines and
            # NUL bytes even when NO_BACKSLASH_ESCAPES is enabled on the target.
            return sa.literal_column("CONVERT(X'" + value.encode("utf-8").hex() + "' USING utf8mb4)")
        return sa.literal_column("CAST(X'" + value.encode("utf-8").hex() + "' AS TEXT)")
    if isinstance(value, int) and not isinstance(value, bool):
        return sa.literal(value, type_=sa.BigInteger())
    return value
