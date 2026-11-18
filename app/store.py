"""Immutable plan snapshots with atomic compare-and-append."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3


class VersionConflict(Exception):
    def __init__(self, current_version: int | None):
        self.current_version = current_version
        super().__init__(f"Current version is {current_version}")


class PlanStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS plan_versions (
                    workspace_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_time INTEGER NOT NULL,
                    snapshot TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, version)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _insert(connection, workspace_id: str, version: int, snapshot: dict) -> None:
        connection.execute(
            """INSERT INTO plan_versions
                (workspace_id, version, event_type, event_time, snapshot)
                VALUES (?, ?, ?, ?, ?)""",
            (
                workspace_id,
                version,
                snapshot["event_type"],
                snapshot["event_time"],
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def create(self, workspace_id: str, snapshot: dict) -> None:
        with closing(self._connect()) as connection, connection:
            self._insert(connection, workspace_id, 1, snapshot)

    def get(self, workspace_id: str, version: int | None = None) -> dict | None:
        with closing(self._connect()) as connection:
            if version is None:
                row = connection.execute(
                    """SELECT snapshot FROM plan_versions WHERE workspace_id = ?
                        ORDER BY version DESC LIMIT 1""",
                    (workspace_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT snapshot FROM plan_versions WHERE workspace_id = ? AND version = ?",
                    (workspace_id, version),
                ).fetchone()
            return json.loads(row["snapshot"]) if row is not None else None

    def list_versions(self, workspace_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT version, event_type, event_time FROM plan_versions
                    WHERE workspace_id = ? ORDER BY version DESC""",
                (workspace_id,),
            )
            return [dict(row) for row in rows]

    def append(self, workspace_id: str, expected_version: int, snapshot: dict) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT MAX(version) AS version FROM plan_versions WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            current = row["version"]
            if current != expected_version:
                raise VersionConflict(current)
            self._insert(connection, workspace_id, expected_version + 1, snapshot)
