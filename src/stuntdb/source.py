"""Source adapters and consistent read transactions."""
from contextlib import contextmanager
import re

import sqlalchemy as sa


def engine_for(source: str):
    url = sa.engine.make_url(source)
    if url.drivername in {"mysql", "mysql+pymysql", "mariadb", "mariadb+pymysql"}:
        url = url.set(drivername="mysql+pymysql")
    elif url.drivername != "sqlite":
        raise ValueError("Supported sources: MySQL 8, MariaDB 10.11/11.4 and SQLite fixtures")
    return sa.create_engine(url)


def validate_server(version):
    """Limit compatibility claims to the server series covered by CI."""
    if 'mariadb' in version.lower():
        match = re.search(r'(\d+)\.(\d+)\.\d+-MariaDB', version, re.IGNORECASE)
        if not match or tuple(map(int, match.groups())) not in {(10, 11), (11, 4)}:
            raise ValueError('Supported MariaDB series: 10.11 and 11.4')
        return 'mariadb'
    if version.split('.')[0] != '8':
        raise ValueError('MySQL 8 required')
    return 'mysql'


def dialect_name(dialect):
    return 'mariadb' if dialect.name == 'mysql' and getattr(dialect, 'is_mariadb', False) else dialect.name


@contextmanager
def consistent_source(engine):
    with engine.connect() as connection:
        try:
            if engine.dialect.name == "mysql":
                version = connection.exec_driver_sql("SELECT VERSION()").scalar_one()
                validate_server(version)
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
