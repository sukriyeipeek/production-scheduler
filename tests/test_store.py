from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.store import PlanStore, VersionConflict


def snapshot(version):
    return {
        "workspace_id": "factory",
        "version": version,
        "event_type": "initial" if version == 1 else "urgent_job",
        "event_time": version - 1,
        "scenario": {"machines": ["Kesim"]},
    }


def test_history_survives_reopening(tmp_path):
    path = tmp_path / "plans.sqlite3"
    store = PlanStore(path)
    store.create("factory", snapshot(1))
    store.append("factory", 1, snapshot(2))

    reopened = PlanStore(path)
    assert reopened.get("factory") == snapshot(2)
    assert reopened.get("factory", 1) == snapshot(1)
    assert reopened.get("missing") is None
    assert [row["version"] for row in reopened.list_versions("factory")] == [2, 1]

    with pytest.raises(VersionConflict) as conflict:
        reopened.append("factory", 1, snapshot(2))
    assert conflict.value.current_version == 2


def test_only_one_writer_can_advance_same_version(tmp_path):
    store = PlanStore(tmp_path / "plans.sqlite3")
    store.create("factory", snapshot(1))
    ready = Barrier(2, timeout=5)

    def append(candidate):
        ready.wait()
        try:
            store.append("factory", 1, candidate)
        except VersionConflict as conflict:
            assert conflict.current_version == 2
            return None
        return candidate

    candidates = [{**snapshot(2), "event_time": minute} for minute in (8, 10)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, candidates))

    committed = [result for result in results if result is not None]
    assert len(committed) == 1
    assert store.get("factory") == committed[0]
    assert store.get("factory", 1) == snapshot(1)
