"""Compare CP-SAT with FIFO on supported OR-Library job-shop instances."""

import argparse
import json
from pathlib import Path
import re
from time import perf_counter

from app.scheduler import solve
from app.schemas import Scenario


def load_instance(source: str, name: str) -> dict:
    blocks = re.split(r"^\s*instance\s+(\w+)\s*$", source, flags=re.MULTILINE)
    instances = dict(zip(blocks[1::2], blocks[2::2]))
    if name not in instances:
        raise ValueError(f"Unknown OR-Library instance: {name}")
    lines = instances[name].splitlines()
    header = next(
        (index for index, line in enumerate(lines) if re.fullmatch(r"\s*\d+\s+\d+\s*", line)),
        None,
    )
    if header is None:
        raise ValueError(f"Missing dimensions for {name}")

    job_count, machine_count = map(int, lines[header].split())
    rows = [list(map(int, line.split())) for line in lines[header + 1:header + 1 + job_count]]
    if len(rows) != job_count or any(len(row) != 2 * machine_count for row in rows):
        raise ValueError(f"Operation counts do not match the dimensions for {name}")

    # Classic job-shop benchmarks minimize makespan and have no delivery dates.
    due = sum(sum(row[1::2]) for row in rows)
    jobs = [
        {
            "id": f"J{job_number}",
            "due": due,
            "operations": [
                {"id": f"O{step}", "duration": duration, "machines": [f"M{machine}"]}
                for step, (machine, duration) in enumerate(zip(row[::2], row[1::2]), start=1)
            ],
        }
        for job_number, row in enumerate(rows, start=1)
    ]
    return Scenario.model_validate(
        {"machines": [f"M{machine}" for machine in range(machine_count)], "jobs": jobs}
    ).model_dump()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to OR-Library jobshop1.txt")
    parser.add_argument("instances", nargs="+", help="Instance identifiers, e.g. ft06 la01 la06")
    args = parser.parse_args()
    try:
        source = args.source.read_text(encoding="utf-8")
        scenarios = [(name, load_instance(source, name)) for name in args.instances]
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    for name, scenario in scenarios:
        before = perf_counter()
        result = solve(scenario)
        print(json.dumps({
            "instance": name,
            "status": result["status"],
            "cp_sat_makespan": result["metrics"]["makespan"] if result["metrics"] else None,
            "fifo_makespan": result["baseline"]["metrics"]["makespan"],
            "seconds": round(perf_counter() - before, 3),
        }))


if __name__ == "__main__":
    main()
