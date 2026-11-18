import os
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.scheduler import solve
from app.schemas import Scenario, ScheduleRequest, ScheduleResult
from app.workspace_api import router as workspace_router


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
EXAMPLE = Scenario.model_validate_json((APP_DIR / "scenario.json").read_bytes())
DEFAULT_DATABASE = APP_DIR.parent / "data" / "plans.sqlite3"
router = APIRouter()


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/scenario")
def example_scenario() -> Scenario:
    """Return the three-machine example with 20 jobs."""
    return EXAMPLE


@router.post("/api/schedule", response_model=ScheduleResult)
def schedule(request: ScheduleRequest) -> dict:
    """Solve a scenario with an optional half-open outage interval [start, end)."""
    scenario = request.scenario.model_dump()
    outage = request.outage.model_dump() if request.outage is not None else None
    return solve(scenario, outage)


def create_app(database_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Production Scheduler", version="0.2.0")
    configured_database = os.environ.get("PRODUCTION_SCHEDULER_DB")
    app.state.database_path = Path(database_path or configured_database or DEFAULT_DATABASE).resolve()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    app.include_router(workspace_router)
    return app


app = create_app()
