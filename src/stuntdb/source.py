"""Source adapters and consistent read transactions."""
from contextlib import contextmanager

import sqlalchemy as sa


def engine_for(source: str):
    url = sa.engine.make_url(source)
    if url.drivername in {"mysql", "mysql+pymysql"}:
        url = url.set(drivername="mysql+pymysql")
    elif url.drivername != "sqlite":
        raise ValueError("Supported sources: MySQL 8 and SQLite development fixtures")
    return sa.create_engine(url)


@contextmanager
def consistent_source(engine):
    with engine.connect() as connection:
        try:
            if engine.dialect.name == "mysql":
                version = connection.exec_driver_sql("SELECT VERSION()").scalar_one()
                if "mariadb" in version.lower() or version.split(".")[0] != "8":
                    raise ValueError("MySQL 8 required; MariaDB support is planned for v0.3")
                unsupported = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
                    "AND engine <> 'InnoDB'").scalar_one()
                if unsupported:
                    raise ValueError("Consistent snapshots require InnoDB tables")
                connection.rollback()
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            else:
                connection.exec_driver_sql("BEGIN")
            yield connection
        finally:
            connection.rollback()
