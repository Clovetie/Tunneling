"""Offline structural checks for caller-supplied onboarding milestone plans."""


def audit_onboarding_plan(milestones, cursor=0, limit=50):
    if not isinstance(milestones, list) or not 1 <= len(milestones) <= 100:
        raise ValueError("milestones must contain 1 to 100 entries")
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ValueError("cursor must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")

    ids = {}
    for index, item in enumerate(milestones):
        if not isinstance(item, dict):
            raise ValueError(f"milestone {index} must be an object")
        ident = item.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident != ident.strip() or len(ident) > 100:
            raise ValueError(f"milestone {index} id must be a non-empty, trimmed string of at most 100 characters")
        ids.setdefault(ident, []).append(index)
        for field in ("objective", "completionEvidence", "recovery"):
            value = item.get(field, "")
            if not isinstance(value, str) or len(value) > 1000:
                raise ValueError(f"milestone {index} {field} must be a string of at most 1000 characters")
        dependencies = item.get("prerequisites", [])
        if not isinstance(dependencies, list) or len(dependencies) > 20:
            raise ValueError(f"milestone {index} prerequisites must be an array of at most 20 IDs")
        if any(not isinstance(dep, str) or not dep.strip() or dep != dep.strip() or len(dep) > 100 for dep in dependencies):
            raise ValueError(f"milestone {index} prerequisites must contain non-empty, trimmed IDs of at most 100 characters")

    issues, total = [], 0
    def emit(issue):
        nonlocal total
        if cursor <= total < cursor + limit:
            issues.append(issue)
        total += 1

    # IDs are case-sensitive and exact. Ambiguous IDs are excluded from the
    # graph, so a duplicate cannot silently be resolved to the wrong node.
    graph = {ident: [] for ident, positions in ids.items() if len(positions) == 1}
    for index, item in enumerate(milestones):
        ident = item["id"]
        if index != ids[ident][0]:
            emit({"code": "duplicate_id", "index": index, "id": ident, "firstIndex": ids[ident][0]})
        for field in ("objective", "completionEvidence", "recovery"):
            if not item.get(field, "").strip():
                emit({"code": "missing_field", "index": index, "id": ident, "field": field})
        seen = set()
        for dependency in item.get("prerequisites", []):
            if dependency in seen:
                emit({"code": "duplicate_prerequisite", "index": index, "id": ident, "prerequisite": dependency})
                continue
            seen.add(dependency)
            if dependency not in ids:
                emit({"code": "unknown_prerequisite", "index": index, "id": ident, "prerequisite": dependency})
            elif len(ids[dependency]) > 1:
                emit({"code": "ambiguous_prerequisite", "index": index, "id": ident, "prerequisite": dependency})
            elif ident in graph:
                graph[ident].append(dependency)

    # Tarjan SCC: one deterministic issue per cyclic component, including self-loops.
    numbers, low, stack, active, components = {}, {}, [], set(), []
    def visit(node):
        numbers[node] = low[node] = len(numbers)
        stack.append(node)
        active.add(node)
        for neighbor in graph[node]:
            if neighbor not in numbers:
                visit(neighbor)
                low[node] = min(low[node], low[neighbor])
            elif neighbor in active:
                low[node] = min(low[node], numbers[neighbor])
        if low[node] == numbers[node]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph[node]:
                components.append(sorted(component, key=lambda member: ids[member][0]))
    for node in graph:
        if node not in numbers:
            visit(node)
    for component in sorted(components, key=lambda row: ids[row[0]][0]):
        emit({"code": "prerequisite_cycle", "ids": component})
    return {"ok": total == 0, "milestonesChecked": len(milestones), "totalIssues": total,
            "issues": issues, "cursor": cursor, "nextCursor": cursor + len(issues) if cursor + len(issues) < total else None,
            "warnings": ["Structural checks only; does not verify gameplay, player comprehension, server authority, persistence, or runtime behavior."]}
