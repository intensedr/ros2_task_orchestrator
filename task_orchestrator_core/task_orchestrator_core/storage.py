"""Optional SQLite storage for task records and events."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from builtin_interfaces.msg import Time

from task_orchestrator_msgs.msg import TaskEventV1, TaskRecordV1, TaskResultV1
from task_orchestrator_msgs.srv import ListEventsV1, ListTaskRecordsV1


class SQLiteStorageError(RuntimeError):
    """Raised when optional SQLite storage cannot be initialized."""


class SQLiteTaskStorage:
    """Small SQLite-backed store for task records and events."""

    def __init__(self, sqlite_path: str, retention_days: int = 0) -> None:
        if not sqlite_path:
            raise SQLiteStorageError("storage.sqlite_path must be set when storage.enabled is true")

        self._sqlite_path = sqlite_path
        self._retention_days = max(0, retention_days)
        if sqlite_path != ":memory:":
            Path(sqlite_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            sqlite_path = str(Path(sqlite_path).expanduser())

        self._connection = sqlite3.connect(sqlite_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()
        self._enforce_retention()

    def close(self) -> None:
        self._connection.close()

    def write_task_record(self, record: TaskRecordV1) -> None:
        result = record.result
        created_sec, created_nanosec = _time_to_pair(result.created_at)
        started_sec, started_nanosec = _time_to_pair(result.started_at)
        finished_sec, finished_nanosec = _time_to_pair(result.finished_at)
        self._connection.execute(
            """
            INSERT INTO task_records (
                task_id, api_version, task_name, source, correlation_id,
                task_status, error_code, error_message, task_result_json,
                task_data_json, tags_json, active, created_sec, created_nanosec,
                started_sec, started_nanosec, finished_sec, finished_nanosec,
                updated_at_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                api_version = excluded.api_version,
                task_name = excluded.task_name,
                source = excluded.source,
                correlation_id = excluded.correlation_id,
                task_status = excluded.task_status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                task_result_json = excluded.task_result_json,
                task_data_json = excluded.task_data_json,
                tags_json = excluded.tags_json,
                active = excluded.active,
                created_sec = excluded.created_sec,
                created_nanosec = excluded.created_nanosec,
                started_sec = excluded.started_sec,
                started_nanosec = excluded.started_nanosec,
                finished_sec = excluded.finished_sec,
                finished_nanosec = excluded.finished_nanosec,
                updated_at_ns = excluded.updated_at_ns
            """,
            (
                result.task_id,
                result.api_version,
                result.task_name,
                result.source,
                result.correlation_id,
                result.task_status,
                result.error_code,
                result.error_message,
                result.task_result_json,
                record.task_data_json,
                json.dumps(list(record.tags), sort_keys=True),
                1 if record.active else 0,
                created_sec,
                created_nanosec,
                started_sec,
                started_nanosec,
                finished_sec,
                finished_nanosec,
                time.time_ns(),
            ),
        )
        self._connection.commit()
        self._enforce_retention()

    def get_task_record(self, task_id: str) -> TaskRecordV1 | None:
        row = self._connection.execute(
            "SELECT * FROM task_records WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_task_record(row)

    def list_task_records(self, request: ListTaskRecordsV1.Request) -> list[TaskRecordV1]:
        where, values = self._task_record_filters(request)
        limit_sql = " LIMIT ?" if request.limit > 0 else ""
        if request.limit > 0:
            values.append(int(request.limit))
        rows = self._connection.execute(
            f"SELECT * FROM task_records{where} ORDER BY updated_at_ns DESC{limit_sql}",
            values,
        ).fetchall()
        return [self._row_to_task_record(row) for row in rows]

    def write_event(self, event: TaskEventV1) -> None:
        stamp_sec, stamp_nanosec = _time_to_pair(event.stamp)
        self._connection.execute(
            """
            INSERT INTO task_events (
                event_id, api_version, event_type, task_id, task_name, source,
                correlation_id, previous_status, current_status, error_code,
                error_message, data_json, stamp_sec, stamp_nanosec,
                stored_at_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                api_version = excluded.api_version,
                event_type = excluded.event_type,
                task_id = excluded.task_id,
                task_name = excluded.task_name,
                source = excluded.source,
                correlation_id = excluded.correlation_id,
                previous_status = excluded.previous_status,
                current_status = excluded.current_status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                data_json = excluded.data_json,
                stamp_sec = excluded.stamp_sec,
                stamp_nanosec = excluded.stamp_nanosec,
                stored_at_ns = excluded.stored_at_ns
            """,
            (
                event.event_id,
                event.api_version,
                event.event_type,
                event.task_id,
                event.task_name,
                event.source,
                event.correlation_id,
                event.previous_status,
                event.current_status,
                event.error_code,
                event.error_message,
                event.data_json,
                stamp_sec,
                stamp_nanosec,
                time.time_ns(),
            ),
        )
        self._connection.commit()
        self._enforce_retention()

    def list_events(self, request: ListEventsV1.Request) -> list[TaskEventV1]:
        where, values = self._event_filters(request)
        limit_sql = " LIMIT ?" if request.limit > 0 else ""
        if request.limit > 0:
            values.append(int(request.limit))
        rows = self._connection.execute(
            f"SELECT * FROM task_events{where} ORDER BY sequence DESC{limit_sql}",
            values,
        ).fetchall()
        return [self._row_to_task_event(row) for row in rows]

    def _initialize_schema(self) -> None:
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_records (
                task_id TEXT PRIMARY KEY,
                api_version TEXT NOT NULL,
                task_name TEXT NOT NULL,
                source TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                task_status TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_message TEXT NOT NULL,
                task_result_json TEXT NOT NULL,
                task_data_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                active INTEGER NOT NULL,
                created_sec INTEGER NOT NULL,
                created_nanosec INTEGER NOT NULL,
                started_sec INTEGER NOT NULL,
                started_nanosec INTEGER NOT NULL,
                finished_sec INTEGER NOT NULL,
                finished_nanosec INTEGER NOT NULL,
                updated_at_ns INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                api_version TEXT NOT NULL,
                event_type TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                source TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                previous_status TEXT NOT NULL,
                current_status TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_message TEXT NOT NULL,
                data_json TEXT NOT NULL,
                stamp_sec INTEGER NOT NULL,
                stamp_nanosec INTEGER NOT NULL,
                stored_at_ns INTEGER NOT NULL
            )
            """
        )
        self._connection.commit()

    def _enforce_retention(self) -> None:
        if self._retention_days <= 0:
            return
        cutoff_ns = time.time_ns() - int(self._retention_days * 24 * 60 * 60 * 1_000_000_000)
        self._connection.execute("DELETE FROM task_records WHERE updated_at_ns < ?", (cutoff_ns,))
        self._connection.execute("DELETE FROM task_events WHERE stored_at_ns < ?", (cutoff_ns,))
        self._connection.commit()

    def _task_record_filters(self, request: ListTaskRecordsV1.Request) -> tuple[str, list[Any]]:
        filters: list[str] = []
        values: list[Any] = []
        if request.task_name:
            filters.append("task_name = ?")
            values.append(request.task_name)
        if request.task_status:
            filters.append("task_status = ?")
            values.append(request.task_status)
        if request.source:
            filters.append("source = ?")
            values.append(request.source)
        if request.correlation_id:
            filters.append("correlation_id = ?")
            values.append(request.correlation_id)
        return _where_clause(filters), values

    def _event_filters(self, request: ListEventsV1.Request) -> tuple[str, list[Any]]:
        filters: list[str] = []
        values: list[Any] = []
        if request.task_id:
            filters.append("task_id = ?")
            values.append(request.task_id)
        if request.task_name:
            filters.append("task_name = ?")
            values.append(request.task_name)
        if request.event_type:
            filters.append("event_type = ?")
            values.append(request.event_type)
        if request.current_status:
            filters.append("current_status = ?")
            values.append(request.current_status)
        if request.source:
            filters.append("source = ?")
            values.append(request.source)
        if request.correlation_id:
            filters.append("correlation_id = ?")
            values.append(request.correlation_id)
        return _where_clause(filters), values

    def _row_to_task_record(self, row: sqlite3.Row) -> TaskRecordV1:
        result = TaskResultV1()
        result.api_version = row["api_version"]
        result.task_id = row["task_id"]
        result.task_name = row["task_name"]
        result.source = row["source"]
        result.correlation_id = row["correlation_id"]
        result.task_status = row["task_status"]
        result.error_code = row["error_code"]
        result.error_message = row["error_message"]
        result.task_result_json = row["task_result_json"]
        result.created_at = _pair_to_time(row["created_sec"], row["created_nanosec"])
        result.started_at = _pair_to_time(row["started_sec"], row["started_nanosec"])
        result.finished_at = _pair_to_time(row["finished_sec"], row["finished_nanosec"])

        record = TaskRecordV1()
        record.result = result
        record.active = bool(row["active"])
        record.task_data_json = row["task_data_json"]
        record.tags = list(json.loads(row["tags_json"]))
        return record

    def _row_to_task_event(self, row: sqlite3.Row) -> TaskEventV1:
        event = TaskEventV1()
        event.api_version = row["api_version"]
        event.event_id = row["event_id"]
        event.event_type = row["event_type"]
        event.task_id = row["task_id"]
        event.task_name = row["task_name"]
        event.source = row["source"]
        event.correlation_id = row["correlation_id"]
        event.previous_status = row["previous_status"]
        event.current_status = row["current_status"]
        event.error_code = row["error_code"]
        event.error_message = row["error_message"]
        event.data_json = row["data_json"]
        event.stamp = _pair_to_time(row["stamp_sec"], row["stamp_nanosec"])
        return event


def _where_clause(filters: list[str]) -> str:
    return " WHERE " + " AND ".join(filters) if filters else ""


def _time_to_pair(value: Time) -> tuple[int, int]:
    return int(getattr(value, "sec", 0)), int(getattr(value, "nanosec", 0))


def _pair_to_time(sec: int, nanosec: int) -> Time:
    value = Time()
    value.sec = int(sec or 0)
    value.nanosec = int(nanosec or 0)
    return value
