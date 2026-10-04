"""Schema summaries without row data."""
import sqlalchemy as sa


def cycles(metadata: sa.MetaData) -> list[list[str]]:
    graph = {name: {fk.referred_table.name for fk in table.foreign_key_constraints}
             for name, table in metadata.tables.items()}
    # Iterative reachability avoids recursion limits on large schemas.
    def reachable(start):
        found, pending = set(), [start]
        while pending:
            node = pending.pop()
            if node not in found:
                found.add(node)
                pending.extend(graph[node] - found)
        return found
    reach = {name: reachable(name) for name in graph}
    remaining, groups = set(graph), []
    while remaining:
        first = min(remaining)
        group = sorted(n for n in remaining if n in reach[first] and first in reach[n])
        remaining.difference_update(group)
        if len(group) > 1 or first in graph[first]:
            groups.append(group)
    return groups


def describe(metadata: sa.MetaData) -> dict:
    return {"tables": {name: {
        "columns": list(table.c.keys()),
        "primary_key": [c.name for c in table.primary_key],
        "references": [{"from": [e.parent.name for e in fk.elements],
                        "table": fk.referred_table.name,
                        "to": [e.column.name for e in fk.elements]}
                       for fk in sorted(table.foreign_key_constraints,
                                        key=lambda f: tuple(e.parent.name for e in f.elements))],
    } for name, table in metadata.tables.items()}, "cycles": cycles(metadata)}
