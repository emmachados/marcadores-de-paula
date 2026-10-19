"""Detached review snapshots sharing unchanged subtrees between undo steps."""
import copy


def snapshot(value, previous=None):
    """Copy the value, reusing unchanged objects from the previous snapshot only."""
    if type(value) is type(previous) and value == previous:
        return previous
    if isinstance(value, dict):
        older = previous if isinstance(previous, dict) else {}
        return {key: snapshot(item, older.get(key)) for key, item in value.items()}
    if isinstance(value, list):
        older = previous if isinstance(previous, list) else []
        return [snapshot(item, older[index] if index < len(older) else None)
                for index, item in enumerate(value)]
    return copy.deepcopy(value)
