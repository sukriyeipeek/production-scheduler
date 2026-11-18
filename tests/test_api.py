from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_demo_and_baseline():
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    scenario = client.get("/api/scenario").json()
    assert len(scenario["machines"]) == 3
    assert len(scenario["jobs"]) == 20

    original = client.post("/api/schedule", json={"scenario": scenario})
    assert original.status_code == 200
    result = original.json()
    assert result["status"] in {"optimal", "feasible"}
    assert len(result["operations"]) == sum(len(job["operations"]) for job in scenario["jobs"])
    assert result["baseline"] is not None

    active = next(row for row in result["operations"] if row["end"] - row["start"] > 1)
    outage = {"machine_id": active["machine_id"], "start": active["start"] + 1, "end": active["start"] + 30}
    replanned = client.post("/api/schedule", json={"scenario": scenario, "outage": outage})
    assert replanned.status_code == 200
    result = replanned.json()
    assert result["status"] in {"optimal", "feasible"}
    assert result["interruptions"]
    for schedule in (result, result["baseline"]):
        assert all(
            row["machine_id"] != outage["machine_id"]
            or row["end"] <= outage["start"]
            or row["start"] >= outage["end"]
            for row in schedule["operations"]
        )


def test_unknown_machine_and_boolean_durations_are_rejected():
    scenario = client.get("/api/scenario").json()
    outage = {"machine_id": "unknown", "start": 2, "end": 4}
    assert client.post("/api/schedule", json={"scenario": scenario, "outage": outage}).status_code == 422
    scenario["jobs"][0]["operations"][0]["duration"] = True
    assert client.post("/api/schedule", json={"scenario": scenario}).status_code == 422
