from pathlib import Path

import pytest

from stuntdb.keystore import temporary_keys


def test_store_deduplicates_table_and_composite_keys():
    with temporary_keys() as store:
        assert store.add("a", (1, b"key"))
        assert not store.add("a", (1, b"key"))
        assert store.add("b", (1, b"key"))
        assert store.add("a", (1, b"other"))
        assert store.shallower("a", (1,), 3)
        assert not store.shallower("a", (1,), 3)
        assert not store.shallower("a", (1,), 4)
        assert store.shallower("a", (1,), 1)


@pytest.mark.parametrize("fail", [False, True])
def test_temporary_keys_are_removed_on_exit(fail):
    directory = None
    try:
        with temporary_keys() as store:
            filename = store.connection.execute("PRAGMA database_list").fetchone()[2]
            directory = Path(filename).parent
            assert directory.exists()
            if fail:
                raise ValueError("fixture failure")
    except ValueError:
        assert fail
    assert directory is not None and not directory.exists()
