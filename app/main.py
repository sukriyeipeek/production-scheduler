from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.scheduler import solve
from app.schemas import Scenario, ScheduleRequest, ScheduleResult


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
EXAMPLE = Scenario.model_validate_json((APP_DIR / "scenario.json").read_bytes())

app = FastAPI(title="Production Scheduler", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scenario")
def example_scenario() -> Scenario:
    """Return the three-machine example with 20 jobs."""
    return EXAMPLE


@app.post("/api/schedule", response_model=ScheduleResult)
def schedule(request: ScheduleRequest) -> dict:
    """Solve a scenario with an optional half-open outage interval [start, end)."""
    scenario = request.scenario.model_dump()
    outage = request.outage.model_dump() if request.outage is not None else None
    return solve(scenario, outage)
