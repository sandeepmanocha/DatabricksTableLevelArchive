import json
import logging
import uuid
import datetime as datetime_module
from typing import Any, Mapping, Optional

from src.exceptions import ArchiveConfigError
from src.utils import (
    build_insert_values_sql,
    ensure_table_with_setup_message,
    row_value,
    sql_date_or_null,
    sql_expr,
    sql_int,
    sql_int_or_null,
    sql_quote,
    sql_str_or_null,
)

logger = logging.getLogger("caresource_archive.audit")


def _utc_now() -> datetime_module.datetime:
    return datetime_module.datetime.now(datetime_module.timezone.utc)

ALLOWED_ARCHIVE_STATUSES = frozenset(
    {
        "STARTED",
        "DRY_RUN",
        "ARCHIVED",
        "ARCHIVED_AND_DELETED",
        "FAILED",
        "SKIPPED",
        "SKIPPED_CONCURRENT",
        "NO_DATA",
    }
)

ARCHIVE_AUDIT_COLUMNS = [
    "audit_id",
    "archive_run_id",
    "table_name",
    "year",
    "status",
    "record_count",
    "conditions_applied",
    "null_date_count",
    "error_message",
    "watermark_value",
    "source_year_count",
    "archive_mode",
    "archived_by",
    "workspace_id",
    "job_id",
    "job_run_id",
    "task_run_id",
    "created_at",
]

REHYDRATION_AUDIT_COLUMNS = [
    "audit_id",
    "archive_run_id",
    "archive_path",
    "source_table",
    "target_catalog",
    "target_schema",
    "years",
    "tables_created",
    "status",
    "error_message",
    "rehydrated_by",
    "workspace_id",
    "job_id",
    "job_run_id",
    "task_run_id",
    "created_at",
]


def _audit_table_fq(audit_catalog: str, audit_schema: str, name: str) -> str:
    return f"`{audit_catalog}`.`{audit_schema}`.`{name}`"


class AuditLogger:
    def __init__(self, ctx, spark):
        settings = ctx.settings
        cat = settings.get("audit_catalog")
        sch = settings.get("audit_schema")
        if not cat:
            raise ArchiveConfigError(
                msg="Config error: audit_catalog is required in settings",
            )
        if not sch:
            raise ArchiveConfigError(
                msg="Config error: audit_schema is required in settings",
            )
        self.audit_catalog = cat
        self.audit_schema = sch
        self._spark = spark
        self._ctx = ctx

    def _archive_table(self) -> str:
        return _audit_table_fq(self.audit_catalog, self.audit_schema, "archive_audit_log")

    def _rehydration_table(self) -> str:
        return _audit_table_fq(
            self.audit_catalog,
            self.audit_schema,
            "rehydration_audit_log",
        )

    def _job_context_values(self) -> list[str]:
        jc = self._ctx.job_context
        return [
            sql_expr("current_user()"),
            sql_str_or_null(jc.get("workspace_id")),
            sql_str_or_null(jc.get("job_id")),
            sql_str_or_null(jc.get("job_run_id")),
            sql_str_or_null(jc.get("task_run_id")),
            sql_expr("current_timestamp()"),
        ]

    def ensure_archive_audit_table(self) -> None:
        ensure_table_with_setup_message(
            self._spark, self._archive_table(), label="Archive audit table"
        )

    def ensure_rehydration_audit_table(self) -> None:
        ensure_table_with_setup_message(
            self._spark, self._rehydration_table(), label="Rehydration audit table"
        )

    def log_archive(
        self,
        table: str,
        year: int,
        status: str,
        record_count: int,
        conditions_applied: Optional[str] = None,
        null_date_count: Optional[int] = None,
        error_message: Optional[str] = None,
        watermark_value: Optional[datetime_module.date] = None,
        source_year_count: Optional[int] = None,
        archive_mode: Optional[str] = None,
    ) -> None:
        if status not in ALLOWED_ARCHIVE_STATUSES:
            raise ArchiveConfigError(msg=f"Invalid archive audit status: {status!r}")
        audit_id = str(uuid.uuid4())
        values = [
            sql_quote(audit_id),
            sql_quote(self._ctx.archive_run_id),
            sql_quote(table),
            sql_int(year),
            sql_quote(status),
            sql_int(record_count),
            sql_str_or_null(conditions_applied),
            sql_int_or_null(null_date_count),
            sql_str_or_null(error_message),
            sql_date_or_null(watermark_value),
            sql_int_or_null(source_year_count),
            sql_str_or_null(archive_mode),
            *self._job_context_values(),
        ]
        sql = build_insert_values_sql(self._archive_table(), ARCHIVE_AUDIT_COLUMNS, values)
        self._spark.sql(sql)

    def log_rehydrate(
        self,
        archive_path: str,
        source: str,
        target_catalog: str,
        target_schema: str,
        years: str,
        tables_created: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        audit_id = str(uuid.uuid4())
        values = [
            sql_quote(audit_id),
            sql_quote(self._ctx.archive_run_id),
            sql_quote(archive_path),
            sql_quote(source),
            sql_quote(target_catalog),
            sql_quote(target_schema),
            sql_quote(years),
            sql_int(tables_created),
            sql_quote(status),
            sql_str_or_null(error_message),
            *self._job_context_values(),
        ]
        sql = build_insert_values_sql(
            self._rehydration_table(), REHYDRATION_AUDIT_COLUMNS, values
        )
        self._spark.sql(sql)

    def log_dry_run(
        self,
        table: str,
        year: int,
        total_eligible: int,
        would_archive: int,
        per_condition_counts: Mapping[str, Any],
        null_date_count: Optional[int] = None,
        action: Optional[str] = None,
    ) -> None:
        payload = {
            "action": action,
            "per_condition_counts": dict(per_condition_counts),
            "total_eligible": int(total_eligible),
            "would_archive": int(would_archive),
        }
        conditions_applied = json.dumps(payload, sort_keys=True)
        self.log_archive(
            table=table,
            year=year,
            status="DRY_RUN",
            record_count=int(would_archive),
            conditions_applied=conditions_applied,
            null_date_count=null_date_count,
            error_message=None,
        )

    def check_resume_state(self, table_config: Mapping[str, Any], year: int) -> Optional[str]:
        result = self.get_latest_status(table_config["table_id"], year)
        return result[0] if result else None

    def is_archived_by_run(self, table: str, year: int, archive_run_id: str) -> bool:
        """Check if the given run owns an ARCHIVED entry for this table+year."""
        sql = (
            f"SELECT 1 AS n FROM {self._archive_table()} "
            f"WHERE table_name = {sql_quote(table)} "
            f"AND year = {int(year)} AND status = 'ARCHIVED' "
            f"AND archive_run_id = {sql_quote(archive_run_id)} "
            f"LIMIT 1"
        )
        rows = self._spark.sql(sql).collect()
        return len(rows) > 0

    def get_last_run_state(self, table: str, year: int) -> tuple:
        sql = (
            f"SELECT status, watermark_value, source_year_count "
            f"FROM {self._archive_table()} "
            f"WHERE table_name = {sql_quote(table)} AND year = {int(year)} "
            f"AND status IN ('ARCHIVED', 'ARCHIVED_AND_DELETED') "
            f"ORDER BY created_at DESC LIMIT 1"
        )
        rows = self._spark.sql(sql).collect()
        if not rows:
            return (None, None, None)
        r = rows[0]
        status = r["status"]
        wm = r["watermark_value"]
        sc = r["source_year_count"]
        return (status, wm, int(sc) if sc is not None else None)

    def get_latest_status(self, table: str, year: int) -> Optional[tuple]:
        sql = (
            f"SELECT status, archive_run_id "
            f"FROM {self._archive_table()} "
            f"WHERE table_name = {sql_quote(table)} AND year = {int(year)} "
            f"ORDER BY created_at DESC LIMIT 1"
        )
        rows = self._spark.sql(sql).collect()
        if not rows:
            return None
        r = rows[0]
        return (row_value(r, "status"), row_value(r, "archive_run_id"))

    def check_concurrent(
        self,
        table: str,
        year: int,
        archive_run_id: str,
        stale_threshold_hours: float = 4,
    ) -> tuple:
        audit_tbl = self._archive_table()
        sql = f"""SELECT s.archive_run_id, s.created_at FROM {audit_tbl} s
WHERE s.table_name = {sql_quote(table)}
  AND s.year = {int(year)}
  AND s.status = 'STARTED'
  AND s.archive_run_id <> {sql_quote(archive_run_id)}
  AND NOT EXISTS (
    SELECT 1 FROM {audit_tbl} t
    WHERE t.table_name = s.table_name
      AND t.year = s.year
      AND t.archive_run_id = s.archive_run_id
      AND t.status IN ('ARCHIVED','ARCHIVED_AND_DELETED','FAILED','SKIPPED','SKIPPED_CONCURRENT','DRY_RUN','NO_DATA')
  )
ORDER BY s.created_at DESC
LIMIT 1"""
        rows = self._spark.sql(sql).collect()
        if not rows:
            return (False, False, None, None)
        r = rows[0]
        foreign_run_id = row_value(r, "archive_run_id")
        created_at = row_value(r, "created_at")
        now = _utc_now()
        if isinstance(created_at, datetime_module.datetime):
            ct = created_at
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=datetime_module.timezone.utc)
            else:
                ct = ct.astimezone(datetime_module.timezone.utc)
            age_hours = max(0.0, (now - ct).total_seconds() / 3600.0)
        else:
            logger.warning(
                "check_concurrent: created_at for run %s is %s, not datetime. "
                "Treating as stale.",
                foreign_run_id,
                type(created_at).__name__,
            )
            age_hours = float(stale_threshold_hours)
        if age_hours < stale_threshold_hours:
            return (True, False, foreign_run_id, age_hours)
        return (False, True, foreign_run_id, age_hours)
