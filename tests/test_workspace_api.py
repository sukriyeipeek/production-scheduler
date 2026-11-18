from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient

from app import workspace_api
from app.main import create_app


def job(job_id, machine_id="M1", duration=4):
    return {
        "id": job_id,
        "due": 10,
        "operations": [{"id": "O1", "duration": duration, "machines": [machine_id]}],
    }


def initial_workspace(client):
    scenario = {"machines": ["M1", "M2"], "jobs": [job("J1"), job("J2", "M2", 3)]}
    response = client.post("/api/workspaces", json={"scenario": scenario})
    assert response.status_code == 201, response.text
    return response.json()


def urgent_event(job_id, expected_version=1, event_time=2):
    return {
        "expected_version": expected_version,
        "event_type": "urgent_job",
        "event_time": event_time,
        "job": job(job_id),
    }


def test_outage_and_urgent_job_persist_versioned_history(tmp_path):
    path = tmp_path / "plans.sqlite3"
    client = TestClient(create_app(path))
    initial = initial_workspace(client)
    url = f"/api/workspaces/{initial['workspace_id']}"
    outage = {"machine_id": "M1", "start": 1, "end": 6}
    event = {"expected_version": 1, "event_type": "outage", "event_time": 1, "outage": outage}

    incident = client.post(f"{url}/events", json=event)
    assert incident.status_code == 201, incident.text
    assert incident.json()["result"]["interruptions"][0]["interrupted_at"] == 1

    urgent = client.post(f"{url}/events", json=urgent_event("J3", expected_version=2))
    assert urgent.status_code == 201, urgent.text
    head = urgent.json()
    assert head["version"] == 3
    assert head["outage"] == outage
    assert len(head["result"]["interruptions"]) == 1
    assert next(row for row in head["result"]["operations"] if row["job_id"] == "J3")["start"] >= 6

    restarted_client = TestClient(create_app(path))
    assert restarted_client.get(url).json() == head
    assert restarted_client.get(f"{url}/versions/1").json() == initial
    assert restarted_client.get(f"{url}/versions/2").json() == incident.json()
    assert restarted_client.get(f"{url}/versions/42").status_code == 404
    assert restarted_client.get(f"{url}/versions").json() == [
        {"version": 3, "event_type": "urgent_job", "event_time": 2},
        {"version": 2, "event_type": "outage", "event_time": 1},
        {"version": 1, "event_type": "initial", "event_time": 0},
    ]
    older_event = urgent_event("J4", expected_version=3, event_time=1)
    assert restarted_client.post(f"{url}/events", json=older_event).status_code == 409
    second_outage = {
        **event,
        "expected_version": 3,
        "event_time": 9,
        "outage": {"machine_id": "M2", "start": 9, "end": 12},
    }
    assert restarted_client.post(f"{url}/events", json=second_outage).status_code == 409
    assert restarted_client.get(url).json() == head


def test_invalid_events_leave_head_unchanged(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path / "plans.sqlite3"))
    initial = initial_workspace(client)
    url = f"/api/workspaces/{initial['workspace_id']}"

    assert client.post(f"{url}/events", json=urgent_event("J1")).status_code == 422
    assert client.post(f"{url}/events", json=urgent_event("J3", expected_version=2)).status_code == 409
    event = {
        "expected_version": 1,
        "event_type": "outage",
        "event_time": 2,
        "outage": {"machine_id": "M1", "start": 1, "end": 6},
    }
    assert client.post(f"{url}/events", json=event).status_code == 422

    monkeypatch.setattr(workspace_api, "replan", lambda *args, **kwargs: {"status": "unknown"})
    result = client.post(f"{url}/events", json=urgent_event("J3"))
    assert result.status_code == 422
    assert client.get(url).json() == initial
    assert len(client.get(f"{url}/versions").json()) == 1


def test_simultaneous_events_cannot_both_publish(tmp_path, monkeypatch):
    path = tmp_path / "plans.sqlite3"
    client = TestClient(create_app(path))
    initial = initial_workspace(client)
    url = f"/api/workspaces/{initial['workspace_id']}"

    barrier = Barrier(2, timeout=10)
    real_replan = workspace_api.replan

    def synchronized_replan(*args, **kwargs):
        barrier.wait()
        return real_replan(*args, **kwargs)

    monkeypatch.setattr(workspace_api, "replan", synchronized_replan)

    def submit(job_id):
        return TestClient(create_app(path)).post(f"{url}/events", json=urgent_event(job_id))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["J3", "J4"]))

    assert sorted(response.status_code for response in results) == [201, 409]
    assert client.get(url).json()["version"] == 2
    assert len(client.get(f"{url}/versions").json()) == 2
