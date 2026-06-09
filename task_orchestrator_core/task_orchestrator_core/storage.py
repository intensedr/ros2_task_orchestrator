"""Optional SQLite storage for task records and events."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from builtin_interfaces.msg import Time

from task_orchestrator_msgs.msg import TaskEventV1, TaskRecordV1, TaskResultV1, TaskStatusV1
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
        _sync_task_record_metadata(record)
        result = record.result
        created_sec, created_nanosec = _time_to_pair(result.created_at)
        started_sec, started_nanosec = _time_to_pair(result.started_at)
        finished_sec, finished_nanosec = _time_to_pair(result.finished_at)
        scheduled_sec, scheduled_nanosec = _time_to_pair(record.scheduled_at)
        deadline_sec, deadline_nanosec = _time_to_pair(record.deadline_at)
        self._connection.execute(
            """
            INSERT INTO task_records (
                task_id, api_version, task_name, source, priority, correlation_id,
                status, error_code, error_message, result_json,
                duration_sec, total_duration_sec, idempotency_key, metadata_json,
                robot_id, fleet_id, site_id, zone_id, operator_id, tenant_id,
                trace_id, task_data_json, tags_json, scheduled_sec,
                scheduled_nanosec, delay_sec, deadline_sec, deadline_nanosec,
                timeout_sec, queue_on_conflict, active, created_sec, created_nanosec,
                started_sec, started_nanosec, finished_sec, finished_nanosec,
                updated_at_ns
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(task_id) DO UPDATE SET
                api_version = excluded.api_version,
                task_name = excluded.task_name,
                source = excluded.source,
                priority = excluded.priority,
                correlation_id = excluded.correlation_id,
                status = excluded.status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                result_json = excluded.result_json,
                duration_sec = excluded.duration_sec,
                total_duration_sec = excluded.total_duration_sec,
                idempotency_key = excluded.idempotency_key,
                metadata_json = excluded.metadata_json,
                robot_id = excluded.robot_id,
                fleet_id = excluded.fleet_id,
                site_id = excluded.site_id,
                zone_id = excluded.zone_id,
                operator_id = excluded.operator_id,
                tenant_id = excluded.tenant_id,
                trace_id = excluded.trace_id,
                task_data_json = excluded.task_data_json,
                tags_json = excluded.tags_json,
                scheduled_sec = excluded.scheduled_sec,
                scheduled_nanosec = excluded.scheduled_nanosec,
                delay_sec = excluded.delay_sec,
                deadline_sec = excluded.deadline_sec,
                deadline_nanosec = excluded.deadline_nanosec,
                timeout_sec = excluded.timeout_sec,
                queue_on_conflict = excluded.queue_on_conflict,
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
                result.priority,
                result.correlation_id,
                result.status,
                result.error_code,
                result.error_message,
                result.result_json,
                result.duration_sec,
                result.total_duration_sec,
                record.idempotency_key,
                record.metadata_json,
                record.robot_id,
                record.fleet_id,
                record.site_id,
                record.zone_id,
                record.operator_id,
                record.tenant_id,
                record.trace_id,
                record.task_data_json,
                json.dumps(list(record.tags), sort_keys=True),
                scheduled_sec,
                scheduled_nanosec,
                record.delay_sec,
                deadline_sec,
                deadline_nanosec,
                record.timeout_sec,
                1 if record.queue_on_conflict else 0,
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

    def list_queued_task_records(self) -> list[TaskRecordV1]:
        rows = self._connection.execute(
            """
            SELECT * FROM task_records
            WHERE status = ?
            ORDER BY created_sec ASC, created_nanosec ASC, updated_at_ns ASC
            """,
            (TaskStatusV1.QUEUED,),
        ).fetchall()
        return [self._row_to_task_record(row) for row in rows]

    def get_task_record_by_idempotency_key(self, idempotency_key: str) -> TaskRecordV1 | None:
        if not idempotency_key:
            return None
        row = self._connection.execute(
            """
            SELECT * FROM task_records
            WHERE idempotency_key = ?
            ORDER BY updated_at_ns DESC
            LIMIT 1
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_task_record(row)

    def write_event(self, event: TaskEventV1) -> None:
        stamp_sec, stamp_nanosec = _time_to_pair(event.stamp)
        created_sec, created_nanosec = _time_to_pair(event.created_at)
        started_sec, started_nanosec = _time_to_pair(event.started_at)
        finished_sec, finished_nanosec = _time_to_pair(event.finished_at)
        self._connection.execute(
            """
            INSERT INTO task_events (
                event_id, api_version, event_type, task_id, task_name, source,
                priority, correlation_id, created_sec, created_nanosec,
                started_sec, started_nanosec, finished_sec, finished_nanosec,
                previous_status, status, error_code, error_message, result_json,
                duration_sec, total_duration_sec, idempotency_key, robot_id,
                fleet_id, site_id, zone_id, operator_id, tenant_id, trace_id,
                data_json, stamp_sec, stamp_nanosec,
                stored_at_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                api_version = excluded.api_version,
                event_type = excluded.event_type,
                task_id = excluded.task_id,
                task_name = excluded.task_name,
                source = excluded.source,
                priority = excluded.priority,
                correlation_id = excluded.correlation_id,
                created_sec = excluded.created_sec,
                created_nanosec = excluded.created_nanosec,
                started_sec = excluded.started_sec,
                started_nanosec = excluded.started_nanosec,
                finished_sec = excluded.finished_sec,
                finished_nanosec = excluded.finished_nanosec,
                previous_status = excluded.previous_status,
                status = excluded.status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                result_json = excluded.result_json,
                duration_sec = excluded.duration_sec,
                total_duration_sec = excluded.total_duration_sec,
                idempotency_key = excluded.idempotency_key,
                robot_id = excluded.robot_id,
                fleet_id = excluded.fleet_id,
                site_id = excluded.site_id,
                zone_id = excluded.zone_id,
                operator_id = excluded.operator_id,
                tenant_id = excluded.tenant_id,
                trace_id = excluded.trace_id,
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
                event.priority,
                event.correlation_id,
                created_sec,
                created_nanosec,
                started_sec,
                started_nanosec,
                finished_sec,
                finished_nanosec,
                event.previous_status,
                event.status,
                event.error_code,
                event.error_message,
                event.result_json,
                event.duration_sec,
                event.total_duration_sec,
                event.idempotency_key,
                event.robot_id,
                event.fleet_id,
                event.site_id,
                event.zone_id,
                event.operator_id,
                event.tenant_id,
                event.trace_id,
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
                priority INTEGER NOT NULL,
                correlation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_message TEXT NOT NULL,
                result_json TEXT NOT NULL,
                duration_sec REAL NOT NULL,
                total_duration_sec REAL NOT NULL,
                idempotency_key TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                robot_id TEXT NOT NULL,
                fleet_id TEXT NOT NULL,
                site_id TEXT NOT NULL,
                zone_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                task_data_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                scheduled_sec INTEGER NOT NULL,
                scheduled_nanosec INTEGER NOT NULL,
                delay_sec REAL NOT NULL,
                deadline_sec INTEGER NOT NULL,
                deadline_nanosec INTEGER NOT NULL,
                timeout_sec REAL NOT NULL,
                queue_on_conflict INTEGER NOT NULL,
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
                priority INTEGER NOT NULL,
                correlation_id TEXT NOT NULL,
                created_sec INTEGER NOT NULL,
                created_nanosec INTEGER NOT NULL,
                started_sec INTEGER NOT NULL,
                started_nanosec INTEGER NOT NULL,
                finished_sec INTEGER NOT NULL,
                finished_nanosec INTEGER NOT NULL,
                previous_status TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_message TEXT NOT NULL,
                result_json TEXT NOT NULL,
                duration_sec REAL NOT NULL,
                total_duration_sec REAL NOT NULL,
                idempotency_key TEXT NOT NULL,
                robot_id TEXT NOT NULL,
                fleet_id TEXT NOT NULL,
                site_id TEXT NOT NULL,
                zone_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                data_json TEXT NOT NULL,
                stamp_sec INTEGER NOT NULL,
                stamp_nanosec INTEGER NOT NULL,
                stored_at_ns INTEGER NOT NULL
            )
            """
        )
        self._connection.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        self._ensure_column("task_records", "priority", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("task_records", "status", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("task_records", "result_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("task_records", "duration_sec", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("task_records", "total_duration_sec", "REAL NOT NULL DEFAULT 0.0")
        for column_name in (
            "idempotency_key",
            "metadata_json",
            "robot_id",
            "fleet_id",
            "site_id",
            "zone_id",
            "operator_id",
            "tenant_id",
            "trace_id",
        ):
            default = "'{}'" if column_name == "metadata_json" else "''"
            self._ensure_column("task_records", column_name, f"TEXT NOT NULL DEFAULT {default}")
        for column_name in (
            "scheduled_sec",
            "scheduled_nanosec",
            "deadline_sec",
            "deadline_nanosec",
        ):
            self._ensure_column("task_records", column_name, "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("task_records", "delay_sec", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("task_records", "timeout_sec", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("task_records", "queue_on_conflict", "INTEGER NOT NULL DEFAULT 0")
        record_columns = self._table_columns("task_records")
        if "task_status" in record_columns:
            self._connection.execute("UPDATE task_records SET status = task_status WHERE status = ''")
        if "task_result_json" in record_columns:
            self._connection.execute(
                "UPDATE task_records SET result_json = task_result_json WHERE result_json = '{}'"
            )

        for column_name in (
            "priority",
            "created_sec",
            "created_nanosec",
            "started_sec",
            "started_nanosec",
            "finished_sec",
            "finished_nanosec",
        ):
            self._ensure_column("task_events", column_name, "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("task_events", "status", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("task_events", "result_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("task_events", "duration_sec", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("task_events", "total_duration_sec", "REAL NOT NULL DEFAULT 0.0")
        for column_name in (
            "idempotency_key",
            "robot_id",
            "fleet_id",
            "site_id",
            "zone_id",
            "operator_id",
            "tenant_id",
            "trace_id",
        ):
            self._ensure_column("task_events", column_name, "TEXT NOT NULL DEFAULT ''")
        event_columns = self._table_columns("task_events")
        if "current_status" in event_columns:
            self._connection.execute("UPDATE task_events SET status = current_status WHERE status = ''")
        self._connection.commit()

    def _ensure_column(self, table_name: str, column_name: str, column_sql: str) -> None:
        if column_name in self._table_columns(table_name):
            return
        self._connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def _table_columns(self, table_name: str) -> set[str]:
        rows = self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

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
        if request.status:
            filters.append("status = ?")
            values.append(request.status)
        if request.source:
            filters.append("source = ?")
            values.append(request.source)
        if request.correlation_id:
            filters.append("correlation_id = ?")
            values.append(request.correlation_id)
        for field_name in (
            "robot_id",
            "fleet_id",
            "site_id",
            "zone_id",
            "operator_id",
            "tenant_id",
            "trace_id",
            "idempotency_key",
        ):
            value = getattr(request, field_name)
            if value:
                filters.append(f"{field_name} = ?")
                values.append(value)
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
        if request.status:
            filters.append("status = ?")
            values.append(request.status)
        if request.source:
            filters.append("source = ?")
            values.append(request.source)
        if request.correlation_id:
            filters.append("correlation_id = ?")
            values.append(request.correlation_id)
        for field_name in (
            "robot_id",
            "fleet_id",
            "site_id",
            "zone_id",
            "operator_id",
            "tenant_id",
            "trace_id",
            "idempotency_key",
        ):
            value = getattr(request, field_name)
            if value:
                filters.append(f"{field_name} = ?")
                values.append(value)
        return _where_clause(filters), values

    def _row_to_task_record(self, row: sqlite3.Row) -> TaskRecordV1:
        result = TaskResultV1()
        result.api_version = row["api_version"]
        result.task_id = row["task_id"]
        result.task_name = row["task_name"]
        result.source = row["source"]
        result.priority = row["priority"]
        result.correlation_id = row["correlation_id"]
        result.status = row["status"]
        result.error_code = row["error_code"]
        result.error_message = row["error_message"]
        result.result_json = row["result_json"]
        result.duration_sec = row["duration_sec"]
        result.total_duration_sec = row["total_duration_sec"]
        result.idempotency_key = row["idempotency_key"]
        result.metadata_json = row["metadata_json"]
        result.robot_id = row["robot_id"]
        result.fleet_id = row["fleet_id"]
        result.site_id = row["site_id"]
        result.zone_id = row["zone_id"]
        result.operator_id = row["operator_id"]
        result.tenant_id = row["tenant_id"]
        result.trace_id = row["trace_id"]
        result.created_at = _pair_to_time(row["created_sec"], row["created_nanosec"])
        result.started_at = _pair_to_time(row["started_sec"], row["started_nanosec"])
        result.finished_at = _pair_to_time(row["finished_sec"], row["finished_nanosec"])

        record = TaskRecordV1()
        record.result = result
        record.active = bool(row["active"])
        record.task_data_json = row["task_data_json"]
        record.tags = list(json.loads(row["tags_json"]))
        record.scheduled_at = _pair_to_time(row["scheduled_sec"], row["scheduled_nanosec"])
        record.delay_sec = row["delay_sec"]
        record.deadline_at = _pair_to_time(row["deadline_sec"], row["deadline_nanosec"])
        record.timeout_sec = row["timeout_sec"]
        record.queue_on_conflict = bool(row["queue_on_conflict"])
        record.idempotency_key = row["idempotency_key"]
        record.metadata_json = row["metadata_json"]
        record.robot_id = row["robot_id"]
        record.fleet_id = row["fleet_id"]
        record.site_id = row["site_id"]
        record.zone_id = row["zone_id"]
        record.operator_id = row["operator_id"]
        record.tenant_id = row["tenant_id"]
        record.trace_id = row["trace_id"]
        _sync_task_record_metadata(record)
        return record

    def _row_to_task_event(self, row: sqlite3.Row) -> TaskEventV1:
        event = TaskEventV1()
        event.api_version = row["api_version"]
        event.event_id = row["event_id"]
        event.event_type = row["event_type"]
        event.task_id = row["task_id"]
        event.task_name = row["task_name"]
        event.source = row["source"]
        event.priority = row["priority"]
        event.correlation_id = row["correlation_id"]
        event.created_at = _pair_to_time(row["created_sec"], row["created_nanosec"])
        event.started_at = _pair_to_time(row["started_sec"], row["started_nanosec"])
        event.finished_at = _pair_to_time(row["finished_sec"], row["finished_nanosec"])
        event.previous_status = row["previous_status"]
        event.status = row["status"]
        event.error_code = row["error_code"]
        event.error_message = row["error_message"]
        event.result_json = row["result_json"]
        event.duration_sec = row["duration_sec"]
        event.total_duration_sec = row["total_duration_sec"]
        event.idempotency_key = row["idempotency_key"]
        event.robot_id = row["robot_id"]
        event.fleet_id = row["fleet_id"]
        event.site_id = row["site_id"]
        event.zone_id = row["zone_id"]
        event.operator_id = row["operator_id"]
        event.tenant_id = row["tenant_id"]
        event.trace_id = row["trace_id"]
        event.data_json = row["data_json"]
        event.stamp = _pair_to_time(row["stamp_sec"], row["stamp_nanosec"])
        return event


def _where_clause(filters: list[str]) -> str:
    return " WHERE " + " AND ".join(filters) if filters else ""


def _sync_task_record_metadata(record: TaskRecordV1) -> None:
    result = record.result
    record.api_version = result.api_version
    record.task_id = result.task_id
    record.task_name = result.task_name
    record.source = result.source
    record.priority = result.priority
    record.correlation_id = result.correlation_id
    record.created_at = result.created_at
    record.started_at = result.started_at
    record.finished_at = result.finished_at
    record.status = result.status
    record.error_code = result.error_code
    record.error_message = result.error_message
    record.result_json = result.result_json
    record.duration_sec = result.duration_sec
    record.total_duration_sec = result.total_duration_sec
    record.idempotency_key = result.idempotency_key
    record.metadata_json = result.metadata_json
    record.robot_id = result.robot_id
    record.fleet_id = result.fleet_id
    record.site_id = result.site_id
    record.zone_id = result.zone_id
    record.operator_id = result.operator_id
    record.tenant_id = result.tenant_id
    record.trace_id = result.trace_id


def _time_to_pair(value: Time) -> tuple[int, int]:
    return int(getattr(value, "sec", 0)), int(getattr(value, "nanosec", 0))


def _pair_to_time(sec: int, nanosec: int) -> Time:
    value = Time()
    value.sec = int(sec or 0)
    value.nanosec = int(nanosec or 0)
    return value
