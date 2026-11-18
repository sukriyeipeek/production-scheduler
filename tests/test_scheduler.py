from app.scheduler import solve


def test_optimizer_prioritizes_due_date_ahead_of_fifo():
    scenario = {
        "machines": ["cut"],
        "jobs": [
            {"id": "flexible", "due": 20, "operations": [
                {"id": "cut", "machines": ["cut"], "duration": 3}
            ]},
            {"id": "urgent", "due": 2, "operations": [
                {"id": "cut", "machines": ["cut"], "duration": 2}
            ]},
        ],
    }

    result = solve(scenario)

    assert result["status"] == "optimal"
    assert [row["job_id"] for row in result["operations"]] == ["urgent", "flexible"]
    assert result["metrics"] == {"makespan": 5, "total_tardiness": 0}
    assert result["baseline"]["metrics"] == {"makespan": 5, "total_tardiness": 3}


def test_outage_restarts_interrupted_work_and_preserves_shared_history():
    scenario = {
        "machines": ["cut", "finish"],
        "jobs": [
            {"id": "part", "due": 7, "operations": [
                {"id": "cut", "machines": ["cut"], "duration": 5},
                {"id": "finish", "machines": ["finish"], "duration": 2},
            ]},
            {"id": "other", "due": 6, "operations": [
                {"id": "finish", "machines": ["finish"], "duration": 6}
            ]},
        ],
    }
    before = solve(scenario)
    outage = {"machine_id": "cut", "start": 2, "end": 4}

    result = solve(scenario, outage)

    assert result["status"] == "optimal"
    interrupted = next(row for row in before["operations"] if row["operation_id"] == "cut")
    assert result["interruptions"] == [{**interrupted, "interrupted_at": 2}]
    fixed = next(row for row in before["operations"] if row["job_id"] == "other")
    for plan in (result, result["baseline"]):
        assert fixed in plan["operations"]
        cut = next(row for row in plan["operations"] if row["operation_id"] == "cut")
        finish = next(
            row for row in plan["operations"]
            if row["job_id"] == "part" and row["operation_id"] == "finish"
        )
        assert cut["start"] >= 4
        assert cut["end"] - cut["start"] == 5
        assert finish["start"] >= cut["end"]
        assert finish["start"] >= fixed["end"]
        assert sum(row["start"] < 2 for row in plan["operations"]) == 1
    assert result["metrics"] == {"makespan": 11, "total_tardiness": 4}


def test_operation_completed_at_outage_start_needs_no_restart():
    scenario = {
        "machines": ["cut"],
        "jobs": [{"id": "part", "due": 4, "operations": [
            {"id": "first", "machines": ["cut"], "duration": 2},
            {"id": "second", "machines": ["cut"], "duration": 2},
        ]}],
    }

    result = solve(scenario, {"machine_id": "cut", "start": 2, "end": 5})

    assert result["interruptions"] == []
    assert [(row["start"], row["end"]) for row in result["operations"]] == [(0, 2), (5, 7)]


def test_tardiness_can_take_priority_over_makespan():
    scenario = {
        "machines": ["cut", "finish"],
        "jobs": [
            {"id": "flexible", "due": 20, "operations": [
                {"id": "cut", "machines": ["cut"], "duration": 2},
                {"id": "finish", "machines": ["finish"], "duration": 5},
            ]},
            {"id": "urgent", "due": 5, "operations": [
                {"id": "cut", "machines": ["cut"], "duration": 5},
            ]},
        ],
    }

    result = solve(scenario)

    assert result["status"] == "optimal"
    assert result["metrics"] == {"makespan": 12, "total_tardiness": 0}
    assert result["baseline"]["metrics"] == {"makespan": 7, "total_tardiness": 2}
