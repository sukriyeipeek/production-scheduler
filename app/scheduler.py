"""Constraint-based production scheduling and a deterministic FIFO benchmark."""

from collections import defaultdict

from ortools.sat.python import cp_model


def _plan_metrics(scenario, operations):
    completed = {
        (row["job_id"], row["operation_id"]): row["end"]
        for row in operations
    }
    return {
        "makespan": max(row["end"] for row in operations),
        "total_tardiness": sum(
            max(0, completed[job["id"], job["operations"][-1]["id"]] - job["due"])
            for job in scenario["jobs"]
        ),
    }


def _sorted_rows(rows):
    return sorted(
        rows,
        key=lambda row: (row["start"], row["machine_id"], row["job_id"], row["operation_id"]),
    )


def _row(job_id, operation_id, machine_id, start, end):
    return {
        "job_id": job_id,
        "operation_id": operation_id,
        "machine_id": machine_id,
        "start": start,
        "end": end,
    }


def _fifo(scenario, floor, frozen, outage, interrupted):
    """Dispatch available operations by release time, then input job order."""
    ready_at = {machine: floor for machine in scenario["machines"]}
    if outage:
        ready_at[outage["machine_id"]] = outage["end"]
    for row in frozen.values():
        machine = row["machine_id"]
        ready_at[machine] = max(ready_at[machine], row["end"])

    rows = list(frozen.values())
    positions = [0] * len(scenario["jobs"])
    job_ready = [floor] * len(scenario["jobs"])

    while True:
        candidates = []
        for index, job in enumerate(scenario["jobs"]):
            while positions[index] < len(job["operations"]):
                operation = job["operations"][positions[index]]
                key = (job["id"], operation["id"])
                if key in frozen:
                    job_ready[index] = max(job_ready[index], frozen[key]["end"])
                    positions[index] += 1
                    continue
                release = max(job_ready[index], interrupted.get(key, floor))
                start, machine = min(
                    (max(release, ready_at[choice]), choice) for choice in operation["machines"]
                )
                candidates.append((start, release, index, machine, operation))
                break

        if not candidates:
            break
        start, _, index, machine, operation = min(candidates)
        end = start + operation["duration"]
        rows.append(_row(scenario["jobs"][index]["id"], operation["id"], machine, start, end))
        ready_at[machine] = end
        job_ready[index] = end
        positions[index] += 1

    return {"operations": _sorted_rows(rows), "metrics": _plan_metrics(scenario, rows)}


def _optimize(scenario, floor, frozen, outage, interrupted, baseline):
    model = cp_model.CpModel()
    last_fixed_end = max((row["end"] for row in frozen.values()), default=floor)
    last_release = max(last_fixed_end, outage["end"] if outage else floor)
    remaining_duration = sum(
        operation["duration"]
        for job in scenario["jobs"]
        for operation in job["operations"]
        if (job["id"], operation["id"]) not in frozen
    )
    # All remaining work can run serially by this bound.
    horizon = last_release + remaining_duration
    baseline_rows = {
        (row["job_id"], row["operation_id"]): row for row in baseline["operations"]
    }
    machine_intervals = defaultdict(list)
    assignments = []
    job_ends = []
    tardiness = []

    if outage:
        blocked_start = outage["start"]
        blocked_length = outage["end"] - blocked_start
        if blocked_length > 0:
            machine_intervals[outage["machine_id"]].append(
                model.new_fixed_size_interval_var(blocked_start, blocked_length, "outage")
            )

    for job in scenario["jobs"]:
        previous_end = None
        for operation in job["operations"]:
            key = (job["id"], operation["id"])
            duration = operation["duration"]
            name = f"{job['id']}/{operation['id']}"
            fixed = frozen.get(key)
            if fixed:
                start = model.new_constant(fixed["start"])
                end = model.new_constant(fixed["end"])
                machine_intervals[fixed["machine_id"]].append(
                    model.new_fixed_size_interval_var(start, duration, name)
                )
            else:
                release = interrupted.get(key, floor)
                start = model.new_int_var(release, horizon - duration, f"{name}/start")
                end = model.new_int_var(release + duration, horizon, f"{name}/end")
                model.add(end == start + duration)
                choices = {}
                for machine in operation["machines"]:
                    present = model.new_bool_var(f"{name}/{machine}")
                    interval = model.new_optional_fixed_size_interval_var(
                        start, duration, present, f"{name}/{machine}"
                    )
                    machine_intervals[machine].append(interval)
                    choices[machine] = present
                model.add_exactly_one(choices.values())
                assignments.append((key, start, end, choices))
                baseline_row = baseline_rows[key]
                model.add_hint(start, baseline_row["start"])
                model.add_hint(end, baseline_row["end"])
                for machine, present in choices.items():
                    model.add_hint(present, int(machine == baseline_row["machine_id"]))

            if previous_end is not None:
                model.add(start >= previous_end)
            previous_end = end

        job_ends.append(previous_end)
        delay = model.new_int_var(0, horizon, f"{job['id']}/delay")
        model.add_max_equality(delay, (0, previous_end - job["due"]))
        tardiness.append(delay)

    for intervals in machine_intervals.values():
        model.add_no_overlap(intervals)

    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, job_ends)
    # A unit of tardiness costs more than any possible makespan change.
    model.minimize(sum(tardiness) * (horizon + 1) + makespan)

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.max_deterministic_time = 2.0
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status == cp_model.MODEL_INVALID:
            raise RuntimeError("CP-SAT rejected the scheduling model")
        return ("infeasible" if status == cp_model.INFEASIBLE else "unknown"), []

    rows = list(frozen.values())
    for (job, operation), start, end, choices in assignments:
        machine = next(machine for machine, chosen in choices.items() if solver.value(chosen))
        rows.append(_row(job, operation, machine, solver.value(start), solver.value(end)))
    return ("optimal" if status == cp_model.OPTIMAL else "feasible"), _sorted_rows(rows)


def solve(scenario: dict, outage: dict | None = None) -> dict:
    """Plan a validated scenario with optional half-open machine downtime.

    An affected operation in progress is restarted after the outage. The two
    plans are compared from the same optimized history at the outage boundary.
    """
    frozen = {}
    interrupted = {}
    interruptions = []
    floor = 0
    if outage:
        initial = solve(scenario)
        if not initial["operations"]:
            return {
                "status": initial["status"],
                "operations": [],
                "metrics": None,
                "baseline": None,
                "interruptions": [],
            }

        floor = outage["start"]
        for row in initial["operations"]:
            if row["start"] >= floor:
                continue
            key = (row["job_id"], row["operation_id"])
            if row["machine_id"] == outage["machine_id"] and row["end"] > floor:
                interrupted[key] = outage["end"]
                interruptions.append({**row, "interrupted_at": floor})
            else:
                frozen[key] = row

    baseline = _fifo(scenario, floor, frozen, outage, interrupted)
    status, operations = _optimize(scenario, floor, frozen, outage, interrupted, baseline)
    return {
        "status": status,
        "operations": operations,
        "metrics": _plan_metrics(scenario, operations) if operations else None,
        "baseline": baseline,
        "interruptions": interruptions,
    }
