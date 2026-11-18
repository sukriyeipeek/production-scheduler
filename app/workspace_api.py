from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import ValidationError

from app.scheduler import replan, solve
from app.schemas import Scenario, WorkspaceCreate, WorkspaceEvent, WorkspaceSnapshot, WorkspaceVersion
from app.store import PlanStore, VersionConflict


router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def get_store(request: Request) -> PlanStore:
    return PlanStore(request.app.state.database_path)


Store = Annotated[PlanStore, Depends(get_store)]
VersionPath = Annotated[int, Path(ge=1, le=2**63 - 1)]


def get_snapshot(store: PlanStore, workspace_id: str, version: int | None = None) -> dict:
    snapshot = store.get(workspace_id, version)
    if snapshot is None:
        raise HTTPException(404, "Workspace or plan version not found.")
    return snapshot


def require_plan(result: dict) -> None:
    if result["status"] not in {"optimal", "feasible"}:
        raise HTTPException(422, f"No feasible plan was produced ({result['status']}).")


def version_conflict(current_version: int | None) -> HTTPException:
    if current_version is None:
        return HTTPException(404, "Workspace not found.")
    return HTTPException(409, f"Plan version changed. Current version is {current_version}.")


@router.post("", status_code=201, response_model=WorkspaceSnapshot)
def create_workspace(request: WorkspaceCreate, store: Store) -> dict:
    """Solve the initial plan and persist it as version 1."""
    scenario = request.scenario.model_dump()
    result = solve(scenario)
    require_plan(result)
    workspace_id = uuid4().hex
    snapshot = {
        "workspace_id": workspace_id,
        "version": 1,
        "event_type": "initial",
        "event_time": 0,
        "scenario": scenario,
        "outage": None,
        "result": result,
    }
    store.create(workspace_id, snapshot)
    return snapshot


@router.get("/{workspace_id}", response_model=WorkspaceSnapshot)
def workspace_head(workspace_id: str, store: Store) -> dict:
    return get_snapshot(store, workspace_id)


@router.get("/{workspace_id}/versions", response_model=list[WorkspaceVersion])
def workspace_history(workspace_id: str, store: Store) -> list[dict]:
    versions = store.list_versions(workspace_id)
    if not versions:
        raise HTTPException(404, "Workspace not found.")
    return versions


@router.get("/{workspace_id}/versions/{version}", response_model=WorkspaceSnapshot)
def workspace_version(workspace_id: str, version: VersionPath, store: Store) -> dict:
    return get_snapshot(store, workspace_id, version)


@router.post("/{workspace_id}/events", status_code=201, response_model=WorkspaceSnapshot)
def append_event(workspace_id: str, event: WorkspaceEvent, store: Store) -> dict:
    """Replan from the current version and publish only if that version is still current."""
    current = get_snapshot(store, workspace_id)
    if event.expected_version != current["version"]:
        raise version_conflict(current["version"])
    if event.event_time < current["event_time"]:
        raise HTTPException(409, "An event cannot precede the latest accepted event.")

    scenario = current["scenario"]
    outage = current["outage"]
    if event.outage is not None:
        if outage is not None:
            raise HTTPException(409, "This workspace already contains an outage.")
        if event.outage.machine_id not in scenario["machines"]:
            raise HTTPException(422, "The outage must refer to a machine in the scenario.")
        outage = event.outage.model_dump()
    elif event.job is not None:
        try:
            scenario = Scenario.model_validate({
                **scenario, "jobs": [*scenario["jobs"], event.job.model_dump()]
            }).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_context=False, include_input=False)) from exc

    result = replan(
        scenario, current["result"], event.event_time, outage=outage, new_outage=event.outage is not None
    )
    require_plan(result)
    snapshot = {
        "workspace_id": workspace_id,
        "version": event.expected_version + 1,
        "event_type": event.event_type,
        "event_time": event.event_time,
        "scenario": scenario,
        "outage": outage,
        "result": result,
    }
    try:
        store.append(workspace_id, event.expected_version, snapshot)
    except VersionConflict as exc:
        raise version_conflict(exc.current_version) from exc
    return snapshot
