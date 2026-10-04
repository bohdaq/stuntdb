"""Private, temporary disk-backed traversal keys; no source rows are stored."""
import pickle
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


class KeyStore:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE seen (table_name TEXT, key BLOB, PRIMARY KEY(table_name, key)) WITHOUT ROWID")
        self.connection.execute("CREATE TABLE depths (table_name TEXT, key BLOB, depth INTEGER, PRIMARY KEY(table_name, key)) WITHOUT ROWID")

    def add(self, table: str, key: tuple) -> bool:
        # Serialization is only used for equality; this store never unpickles data.
        cursor = self.connection.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)",
                                         (table, pickle.dumps(key, protocol=4)))
        return cursor.rowcount == 1

    def shallower(self, table: str, key: tuple, depth: int) -> bool:
        cursor = self.connection.execute(
            "INSERT INTO depths VALUES (?, ?, ?) ON CONFLICT(table_name,key) "
            "DO UPDATE SET depth=excluded.depth WHERE excluded.depth < depths.depth",
            (table, pickle.dumps(key, protocol=4), depth))
        return cursor.rowcount == 1

    def close(self):
        self.connection.close()


@contextmanager
def temporary_keys():
    with TemporaryDirectory(prefix="stuntdb-keys-") as directory:
        store = KeyStore(Path(directory) / "keys.sqlite")
        try:
            yield store
        finally:
            store.close()
