"""Compare fixed-point extraction against an independent graph traversal."""
from hypothesis import given, settings, strategies as st
import sqlalchemy as sa

from stuntdb.core import extract


@st.composite
def graphs(draw):
    size = draw(st.integers(min_value=1, max_value=20))
    parents = draw(st.lists(st.tuples(
        st.one_of(st.none(), st.integers(0, size - 1)),
        st.one_of(st.none(), st.integers(0, size - 1))), min_size=size, max_size=size))
    seed = draw(st.integers(0, size - 1))
    return parents, seed


@given(graphs())
@settings(max_examples=75, deadline=None)
def test_random_graph_parent_closure(case):
    parents, seed = case
    expected, pending = set(), [seed]
    while pending:
        node = pending.pop()
        if node not in expected:
            expected.add(node)
            pending.extend(p for p in parents[node] if p is not None)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as c:
            c.exec_driver_sql("CREATE TABLE node (id INTEGER PRIMARY KEY, p INTEGER REFERENCES node(id), q INTEGER REFERENCES node(id))")
            c.exec_driver_sql("INSERT INTO node VALUES (?, ?, ?)", [(i, p, q) for i, (p, q) in enumerate(parents)])
            result = extract(c, "node", "id", str(seed))
            assert {row["id"] for row in result.rows["node"]} == expected
            assert c.exec_driver_sql("SELECT COUNT(*) FROM node").scalar_one() == len(parents)
    finally:
        engine.dispose()
