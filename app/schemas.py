from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


MAX_MACHINES = 12
MAX_JOBS = 30
MAX_OPERATIONS_PER_JOB = 10
MAX_OPERATIONS = 80
MAX_TIME = 10_080
MAX_DURATION = 1_440

Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)
]
Time = Annotated[int, Field(ge=0, le=MAX_TIME)]
Duration = Annotated[int, Field(ge=1, le=MAX_DURATION)]
MachineList = Annotated[list[Identifier], Field(min_length=1, max_length=MAX_MACHINES)]
Version = Annotated[int, Field(ge=1, le=2**63 - 1)]


class RequestModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Operation(RequestModel):
    id: Identifier
    duration: Duration
    machines: MachineList

    @model_validator(mode="after")
    def validate_machines(self) -> Self:
        if len(set(self.machines)) != len(self.machines):
            raise ValueError("Eligible machine IDs must be unique within an operation.")
        return self


class Job(RequestModel):
    id: Identifier
    due: Time
    operations: Annotated[
        list[Operation], Field(min_length=1, max_length=MAX_OPERATIONS_PER_JOB)
    ]

    @model_validator(mode="after")
    def validate_operations(self) -> Self:
        if len({operation.id for operation in self.operations}) != len(self.operations):
            raise ValueError("Operation IDs must be unique within a job.")
        return self


class Scenario(RequestModel):
    machines: MachineList
    jobs: Annotated[list[Job], Field(min_length=1, max_length=MAX_JOBS)]

    @model_validator(mode="after")
    def validate_scenario(self) -> Self:
        machine_ids = set(self.machines)
        if len(machine_ids) != len(self.machines):
            raise ValueError("Machine IDs must be unique in the scenario.")
        if len({job.id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Job IDs must be unique in the scenario.")
        if sum(len(job.operations) for job in self.jobs) > MAX_OPERATIONS:
            raise ValueError(f"The scenario allows at most {MAX_OPERATIONS} operations.")
        for job in self.jobs:
            for operation in job.operations:
                unknown = set(operation.machines) - machine_ids
                if unknown:
                    names = ", ".join(sorted(unknown))
                    raise ValueError(f"Operation {job.id}/{operation.id} has unknown machines: {names}.")
        return self


class Outage(RequestModel):
    machine_id: Identifier
    start: Time
    end: Time

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("The outage must end after it starts.")
        return self


class ScheduleRequest(RequestModel):
    scenario: Scenario
    outage: Outage | None = None

    @model_validator(mode="after")
    def validate_outage(self) -> Self:
        if self.outage and self.outage.machine_id not in self.scenario.machines:
            raise ValueError("The outage must refer to a machine in the scenario.")
        return self


class ScheduleOperation(BaseModel):
    job_id: str
    operation_id: str
    machine_id: str
    start: int
    end: int


class Interruption(ScheduleOperation):
    interrupted_at: int


class ScheduleMetrics(BaseModel):
    makespan: int
    total_tardiness: int


class Baseline(BaseModel):
    operations: list[ScheduleOperation]
    metrics: ScheduleMetrics


class ScheduleResult(BaseModel):
    status: Literal["optimal", "feasible", "infeasible", "unknown"]
    operations: list[ScheduleOperation]
    metrics: ScheduleMetrics | None
    baseline: Baseline | None
    interruptions: list[Interruption]


class WorkspaceCreate(RequestModel):
    scenario: Scenario


class WorkspaceEvent(RequestModel):
    expected_version: Version
    event_type: Literal["outage", "urgent_job"]
    event_time: Time
    outage: Outage | None = None
    job: Job | None = None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.event_type == "outage":
            if self.outage is None or self.job is not None:
                raise ValueError("An outage event requires an outage and accepts no job.")
            if self.outage.start != self.event_time:
                raise ValueError("The outage must start at event_time.")
        elif self.job is None or self.outage is not None:
            raise ValueError("An urgent job event requires a job and accepts no outage.")
        return self


class WorkspaceVersion(BaseModel):
    version: Version
    event_type: Literal["initial", "outage", "urgent_job"]
    event_time: Time


class WorkspaceSnapshot(WorkspaceVersion):
    workspace_id: str
    scenario: Scenario
    outage: Outage | None
    result: ScheduleResult
