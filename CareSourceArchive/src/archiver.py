"""
Core archiving engine.

Moves eligible rows from source tables into year-partitioned Delta archive
folders, verifies counts before deleting from the source, and records each
step in the audit log. Handles retention policies, resume/retry logic,
concurrency guards, and safe rollback when verification fails.
"""

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Literal, Mapping, Optional

from src.conditions import (
    build_exclusion_clause,
    build_individual_condition_sql,
    get_condition_names,
    normalize_condition,
)
from src.config import merge_settings
from src.exceptions import (
    ArchiveConfigError,
    ArchiveError,
    ArchiveOperationError,
    ArchiveVerificationError,
)
from src.utils import (
    archive_path_from_config,
    archive_row_count,
    calculate_eligible_years,
    archive_state_and_count,
    get_archive_delta_version,
    source_fq_from_config,
    spark_count,
    sql_date_or_null,
    sql_quote,
    validate_identifier,
)

ArchiveState = Literal["VALID", "MISSING", "ORPHAN"]

_ARCHIVE_TO_DRY_RUN_ACTION = {
    "CREATE": "WOULD_ARCHIVE",
    "APPEND": "WOULD_APPEND",
    "RESUME_DELETE": "WOULD_DELETE",
}

LOGGER = logging.getLogger("caresource_archive.archiver")

def _parse_conditions(raw: Any) -> list:
    """
    Description: Parses exclusion conditions from a JSON string, list, or empty input. Anything else raises so half-migrated configs fail loud.
    Parameters: raw: raw conditions value or None
    Return: list of normalized conditions
    """
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return []
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list):
        return [normalize_condition(c) for c in raw]
    raise ArchiveConfigError(
        field="exclusion_conditions",
        source="archiver",
    )

def _resolve_exclusion(exclusion_clause: str) -> str:
    """
    Description: Returns an exclusion predicate safe to AND into a WHERE; empty input becomes ``1=1``. Always returns the clause aliased — archive-folder reads that previously needed an unaliased form now read unfiltered (see 2026-04-27 alias-fix design).
    Parameters: exclusion_clause: SQL fragment
    Return: SQL string for WHERE clauses
    """
    if not exclusion_clause:
        return "1=1"
    return exclusion_clause


class ArchiveBase:
    """
    Description: Shared state and SQL primitives for archive and delete-job classes. Sibling subclasses (`ArchiveEngine`, `DeleteJob`) extend this base; nothing instantiates `ArchiveBase` directly.
    Parameters: ctx: run context; audit: audit interface; spark: Spark session
    Return: None
    """

    def __init__(self, ctx, audit, spark) -> None:
        """
        Description: Stores the run context, audit logger, and Spark session that every method below relies on.
        Parameters: ctx: run context; audit: audit interface; spark: Spark session
        Return: None
        """
        self._ctx = ctx
        self._audit = audit
        self._spark = spark

    def _set_timezone(self, merged: Mapping[str, Any]) -> None:
        """
        Description: Pins the Spark SQL session to the configured time zone so YEAR() and date math behave the same on every run.
        Parameters: merged: merged configuration mapping
        Return: None
        """
        self._spark.conf.set("spark.sql.session.timeZone", merged["timezone"])

    def _calculate_eligible_years(
        self,
        table_config: Mapping[str, Any],
        retention_years: int,
    ) -> list[int]:
        """
        Description: Returns source years whose watermark falls at or before the retention cutoff (the only years a run touches).
        Parameters: table_config: table config; retention_years: retention window in years
        Return: sorted eligible years
        """
        cy_row = self._spark.sql("SELECT YEAR(current_date()) AS y").first()
        cy = int(cy_row["y"])
        return calculate_eligible_years(
            self._spark,
            source_fq_from_config(table_config),
            table_config["watermark_column"],
            None,
            None,
            cy - int(retention_years),
        )

    def _source_year_count(
        self,
        table_config: Mapping[str, Any],
        year: int,
        *,
        wm_is_null: bool = False,
        after_watermark: Optional[date] = None,
        exclusion_clause: str = "",
    ) -> int:
        """
        Description: Counts source rows for one year using one of three SQL shapes so the historical predicates stay byte-identical.
        Parameters: table_config: table config; year: calendar year; wm_is_null: count rows with NULL watermark when True; after_watermark: optional lower bound on watermark (aliased form only); exclusion_clause: exclusion SQL (aliased form only)
        Return: row count

        Shapes (preserved to match historical SQL exactly):
          - wm_is_null=True: unaliased, wm IS NULL, no exclusion.
          - wm_is_null=False + no exclusion + no after_watermark: unaliased, wm IS NOT NULL.
          - Otherwise: aliased (`src`), wm IS NOT NULL, optional wm > after, AND (exclusion).
        """
        # Resolve the source table identifier and watermark column once for all shapes.
        fq = source_fq_from_config(table_config)
        wm_col = table_config["watermark_column"]

        # Shape 1: count NULL-watermark rows for the year. Unaliased, no exclusion.
        if wm_is_null:
            q = (
                f"SELECT COUNT(*) AS count FROM {fq} "
                f"WHERE YEAR({wm_col}) = {int(year)} AND {wm_col} IS NULL"
            )
            return spark_count(self._spark, q)

        # Shape 2: simple year + non-null watermark count. Unaliased, no exclusion, no after.
        if after_watermark is None and not exclusion_clause:
            q = (
                f"SELECT COUNT(*) AS count FROM {fq} "
                f"WHERE YEAR({wm_col}) = {int(year)} AND {wm_col} IS NOT NULL"
            )
            return spark_count(self._spark, q)

        # Shape 3: aliased (`src`) form with exclusion + optional after_watermark lower bound.
        exc = _resolve_exclusion(exclusion_clause)
        parts = [f"YEAR(src.{wm_col}) = {int(year)}"]
        if after_watermark is not None:
            parts.append(f"src.{wm_col} > {sql_date_or_null(after_watermark)}")
        parts.append(f"src.{wm_col} IS NOT NULL")
        parts.append(f"({exc})")
        q = f"SELECT COUNT(*) AS count FROM {fq} src WHERE {' AND '.join(parts)}"
        return spark_count(self._spark, q)

    def _run_watermark_window(
        self,
        table_config: Mapping[str, Any],
        year: int,
        after_watermark: Optional[date] = None,
    ) -> tuple[Optional[date], Optional[date]]:
        """
        Description: Returns [MIN, MAX] watermark dates for the rows this run wrote, so the source DELETE deletes only what this run added. Reads the archive folder unfiltered (the folder by construction only holds rows that survived exclusion).
        Parameters: table_config: table config; year: calendar year; after_watermark: prior high watermark to exclude (APPEND) or None for CREATE/RESUME_DELETE
        Return: (low, high); (None, None) when the slice is empty for this run
        """
        path = archive_path_from_config(table_config, year)
        wm_col = table_config["watermark_column"]

        parts = [
            f"YEAR(src.{wm_col}) = {int(year)}",
            f"src.{wm_col} IS NOT NULL",
        ]
        if after_watermark is not None:
            parts.append(f"src.{wm_col} > {sql_date_or_null(after_watermark)}")
        where_sql = " AND ".join(parts)

        q = (
            f"SELECT CAST(MIN(src.{wm_col}) AS DATE) AS lo, "
            f"CAST(MAX(src.{wm_col}) AS DATE) AS hi "
            f"FROM delta.`{path}` src WHERE {where_sql}"
        )
        row = self._spark.sql(q).first()
        if row is None:
            return (None, None)

        # Defensive: empty slice or missing fields -> (None, None) so callers skip the delete.
        try:
            lo = row["lo"]
            hi = row["hi"]
        except (KeyError, IndexError, TypeError):
            return (None, None)
        return (lo if lo else None, hi if hi else None)

    def _delete_archived(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
        run_wm_low: Optional[date],
        run_wm_high: Optional[date],
    ) -> None:
        """
        Description: Deletes source rows for the year within this run's inclusive watermark window. No-op on an empty window. Aliases the source as `src` so correlated `src.<col>` references in custom_sql exclusions bind to the outer DELETE row.
        Parameters: table_config: table config; year: calendar year; exclusion_clause: exclusion SQL (aliased to src.); run_wm_low: inclusive low bound on wm (this run only); run_wm_high: inclusive high bound on wm (this run only)
        Return: None
        """
        if run_wm_low is None or run_wm_high is None:
            return
        fq = source_fq_from_config(table_config)
        wm_col = table_config["watermark_column"]
        exc = _resolve_exclusion(exclusion_clause)
        sql = (
            f"DELETE FROM {fq} src WHERE YEAR(src.{wm_col}) = {int(year)} "
            f"AND src.{wm_col} >= {sql_date_or_null(run_wm_low)} "
            f"AND src.{wm_col} <= {sql_date_or_null(run_wm_high)} "
            f"AND src.{wm_col} IS NOT NULL AND ({exc})"
        )
        try:
            self._spark.sql(sql)
        except Exception as exc_e:
            tid = table_config.get("table_id", fq)
            msg = ArchiveError.diagnostic_message(
                "FAILED", "operation_failure",
                table=tid, year=year, operation="delete", error=str(exc_e),
            )
            raise ArchiveOperationError(msg, table=tid, year=year, operation="delete", reason="operation_failure") from exc_e

    def _prepare_run(
        self,
        table_config: Mapping[str, Any],
    ) -> tuple[dict, list, str, str]:
        """
        Description: Shared preamble for `ArchiveEngine.run()` and `DeleteJob.run()` — merges settings, validates the watermark column, parses exclusions, sets the session timezone, and ensures the audit table exists.
        Parameters: table_config: caller-supplied table config
        Return: (merged_config, conditions, exclusion_clause, table_id)
        """
        merged = merge_settings(self._ctx.settings, dict(table_config))
        validate_identifier(
            merged["watermark_column"],
            field="watermark_column",
            table_id=merged.get("table_id"),
        )
        conditions = _parse_conditions(merged.get("exclusion_conditions"))
        exclusion_clause = build_exclusion_clause(
            conditions,
            merged["source_catalog"],
            merged["source_schema"],
            "src",
        )
        self._set_timezone(merged)
        self._audit.ensure_archive_audit_table()
        return merged, conditions, exclusion_clause, merged["table_id"]


class ArchiveEngine(ArchiveBase):
    def _year_where_sql(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
        alias: str,
        after_watermark: Optional[date] = None,
    ) -> str:
        """
        Description: Builds the archive-write WHERE clause: year filter, non-null watermark, exclusion, plus optional `> after_watermark` lower bound for APPEND.
        Parameters: table_config: table config; year: calendar year; exclusion_clause: exclusion SQL; alias: table alias; after_watermark: optional lower bound for incremental append
        Return: AND-joined WHERE clause
        """
        wm_col = table_config["watermark_column"]
        exc = _resolve_exclusion(exclusion_clause)
        parts = [
            f"YEAR({alias}.{wm_col}) = {int(year)}",
        ]
        if after_watermark:
            parts.append(f"{alias}.{wm_col} > {sql_date_or_null(after_watermark)}")
        parts.append(f"{alias}.{wm_col} IS NOT NULL")
        parts.append(f"({exc})")
        return " AND ".join(parts)

    def _count_source_by_year(self, table_config: Mapping[str, Any], year: int) -> int:
        """
        Description: Counts source rows for the year with non-null watermark and no exclusions; used as the year's headline total in audit and reports.
        Parameters: table_config: table config; year: calendar year
        Return: row count
        """
        return self._source_year_count(table_config, year)

    def _get_watermark_value(self, table_config: Mapping[str, Any], year: int) -> Optional[date]:
        """
        Description: Returns the highest watermark already in the year's archive Delta (lower bound for the next APPEND); None when the slice is empty.
        Parameters: table_config: table config; year: calendar year
        Return: max watermark date or None
        """
        path = archive_path_from_config(table_config, year)
        wm_col = table_config["watermark_column"]
        q = f"SELECT CAST(MAX({wm_col}) AS DATE) AS wm FROM delta.`{path}`"
        row = self._spark.sql(q).first()
        return row["wm"] if row and row["wm"] else None

    def _count_new_records(
        self,
        table_config: Mapping[str, Any],
        year: int,
        last_watermark: date,
        exclusion_clause: str,
    ) -> int:
        """
        Description: Counts source rows for the year above `last_watermark` with exclusions applied — the APPEND candidate count.
        Parameters: table_config: table config; year: calendar year; last_watermark: prior high watermark; exclusion_clause: exclusion SQL
        Return: row count
        """
        return self._source_year_count(
            table_config,
            year,
            after_watermark=last_watermark,
            exclusion_clause=exclusion_clause,
        )

    def _append_year(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
        *,
        last_wm: date,
    ) -> int:
        """
        Description: Appends source rows with `wm > last_wm` into the year's archive Delta and returns the post-write archive row count.
        Parameters: table_config: table config; year: calendar year; exclusion_clause: exclusion SQL; last_wm: exclusive lower bound on watermark
        Return: archive row count after append
        """
        fq = source_fq_from_config(table_config)
        path = archive_path_from_config(table_config, year)
        wm_col = table_config["watermark_column"]
        where_sql = self._year_where_sql(
            table_config, year, exclusion_clause, "src", after_watermark=last_wm,
        )
        self._spark.sql(
            f"INSERT INTO delta.`{path}` SELECT * FROM {fq} src WHERE {where_sql}"
        )
        count_sql = (
            f"SELECT COUNT(*) AS count FROM delta.`{path}` src "
            f"WHERE YEAR(src.{wm_col}) = {int(year)} "
            f"AND src.{wm_col} IS NOT NULL"
        )
        return spark_count(self._spark, count_sql)

    def _create_year(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
    ) -> int:
        """
        Description: Writes the year's source rows into a fresh Delta table at the year's archive path (CREATE branch) and returns the post-write archive row count, read unfiltered.
        Parameters: table_config: table config; year: calendar year; exclusion_clause: exclusion SQL
        Return: archive row count after create
        """
        fq = source_fq_from_config(table_config)
        path = archive_path_from_config(table_config, year)
        wm_col = table_config["watermark_column"]
        where_sql = self._year_where_sql(table_config, year, exclusion_clause, "src")
        write_sql = (
            f"CREATE TABLE delta.`{path}` USING DELTA AS "
            f"SELECT * FROM {fq} src WHERE {where_sql}"
        )
        try:
            self._spark.sql(write_sql)
        except Exception as exc:
            tid = table_config.get("table_id", fq)
            msg = ArchiveError.diagnostic_message(
                "FAILED", "operation_failure",
                table=tid, year=year, operation="archive", error=str(exc),
            )
            raise ArchiveOperationError(msg, table=tid, year=year, operation="archive", reason="operation_failure") from exc
        count_sql = (
            f"SELECT COUNT(*) AS count FROM delta.`{path}` src "
            f"WHERE YEAR(src.{wm_col}) = {int(year)} "
            f"AND src.{wm_col} IS NOT NULL"
        )
        return spark_count(self._spark, count_sql)

    def _verify_archive(
        self,
        table_config: Mapping[str, Any],
        year: int,
        expected_count: int,
        exclusion_clause: str = "",
        after_watermark: Optional[date] = None,
    ) -> None:
        """
        Description: Re-counts the source for the year using the write's WHERE clause and asserts it matches `expected_count`. Mismatch raises ArchiveVerificationError; live runs hard-stop and never delete on mismatch. Reads source, not archive (the archive folder by construction only holds rows that survived exclusion).
        Parameters: table_config: table config; year: calendar year; expected_count: rows this run expected to write (archive_total - committed_rows for live writes); exclusion_clause: exclusion SQL (empty => "1=1"); after_watermark: optional APPEND lower bound (must match write SQL)
        Return: None
        """
        fq = source_fq_from_config(table_config)
        where_sql = self._year_where_sql(
            table_config, year, exclusion_clause, "src", after_watermark=after_watermark
        )
        q = f"SELECT COUNT(*) AS count FROM {fq} src WHERE {where_sql}"
        actual = spark_count(self._spark, q)
        if actual != expected_count:
            tid = table_config.get("table_id", fq)
            msg = ArchiveError.diagnostic_message(
                "FAILED", "count_mismatch",
                table=tid, year=year, expected=expected_count, actual=actual,
                path=archive_path_from_config(table_config, year),
            )
            raise ArchiveVerificationError(msg, table=tid, year=year, expected=expected_count, actual=actual, reason="count_mismatch")

    def _write_metadata(
        self,
        table_config: Mapping[str, Any],
        year: int,
        record_count: int,
        conditions: list,
        mode: str,
        dbutils,
        watermark_value: Optional[date] = None,
        source_year_count: Optional[int] = None,
    ) -> None:
        """
        Description: Writes an `_archive_metadata.json` sidecar next to the year's Delta path so external tools can see counts, mode, watermark, and run id. Best-effort — silently skipped when dbutils is None.
        Parameters: table_config: table config; year: calendar year; record_count: archived rows; conditions: applied conditions; mode: metadata mode label; dbutils: dbutils or None; watermark_value: optional high watermark; source_year_count: optional source-year total
        Return: None
        """
        if dbutils is None:
            return
        path = archive_path_from_config(table_config, year).rstrip("/")
        meta_path = f"{path}/_archive_metadata.json"
        merged_ret = table_config.get("retention_years")
        payload = {
            "source_table": source_fq_from_config(table_config),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "record_count": int(record_count),
            "conditions_applied": conditions,
            "retention_years": merged_ret,
            "archive_run_id": self._ctx.archive_run_id,
            "mode": mode,
            "watermark_value": watermark_value.isoformat() if watermark_value is not None else None,
            "source_year_count": source_year_count,
        }
        body = json.dumps(payload, sort_keys=True)
        dbutils.fs.put(meta_path, body, True)

    def _count_nulls(self, table_config: Mapping[str, Any], year: int) -> int:
        """
        Description: Counts source rows for the year with a NULL watermark; those rows are skipped by the archive and logged as a warning for operator cleanup.
        Parameters: table_config: table config; year: calendar year
        Return: row count
        """
        return self._source_year_count(table_config, year, wm_is_null=True)

    def _resolve_year_action(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
        archive_state: ArchiveState,
        committed_rows: int,
    ) -> dict[str, Any]:
        """Decides what the archiver should do for one table+year, without writing anything.

        Single source of truth for action selection. Both the dry-run and live
        paths call this so they can never disagree on the next step. Read-only:
        no writes, no dbutils, no audit changes.

        Strict ordering (earlier rules win — no short-circuits on foreign-run ARCHIVED):
          A) MISSING + ARCHIVED_AND_DELETED -> ERROR missing_folder_after_delete
          B) ORPHAN (any last_status)       -> ERROR archive_folder_orphan
          C) VALID + last_status=None       -> ERROR archive_folder_orphan
          D) VALID + ARCHIVED_AND_DELETED   -> SKIP (historical no-data)
          E) VALID + ARCHIVED + same-run    -> RESUME_DELETE (delete_after only)
          F) MISSING + other                -> CREATE
          G) VALID + ARCHIVED other cases   -> APPEND or SKIP per watermark
        """
        # Inputs: source row count for the year and last audit state for this table+year.
        tid = table_config["table_id"]
        source_year_count = self._count_source_by_year(table_config, year)
        last_status, last_wm, last_count = self._audit.get_last_run_state(tid, year)

        # Result scaffold; each branch below sets "action" (and optionally error fields).
        result: dict[str, Any] = {
            "source_year_count": source_year_count,
            "archive_state": archive_state,
            "committed_rows": int(committed_rows),
            "last_status": last_status,
            "last_watermark": last_wm,
            "last_count": last_count,
            "would_archive": 0,
            "error_message": None,
            "error_reason": None,
        }

        # A) Folder gone after a successful delete: stuck, re-run won't fix it.
        if archive_state == "MISSING" and last_status == "ARCHIVED_AND_DELETED":
            result["action"] = "ERROR"
            result["error_message"] = ArchiveError.diagnostic_message(
                "FAILED", "missing_folder_after_delete",
                table=tid, year=year,
                path=archive_path_from_config(table_config, year),
            )
            result["error_reason"] = "missing_folder_after_delete"
            return result

        # B) Folder exists but isn't a valid Delta table for our slice -> orphan error.
        if archive_state == "ORPHAN":
            result["action"] = "ERROR"
            result["error_message"] = ArchiveError.diagnostic_message(
                "FAILED", "archive_folder_orphan",
                table=tid, year=year,
                path=archive_path_from_config(table_config, year),
            )
            result["error_reason"] = "archive_folder_orphan"
            return result

        # C) Valid archive folder but no audit history -> treat as orphan.
        if archive_state == "VALID" and last_status is None:
            result["action"] = "ERROR"
            result["error_message"] = ArchiveError.diagnostic_message(
                "FAILED", "archive_folder_orphan",
                table=tid, year=year,
                path=archive_path_from_config(table_config, year),
            )
            result["error_reason"] = "archive_folder_orphan"
            return result

        # D) Already archived and deleted, archive still valid -> nothing to do.
        if archive_state == "VALID" and last_status == "ARCHIVED_AND_DELETED":
            result["action"] = "SKIP"
            return result

        delete_after = bool(table_config.get("delete_after_archive"))

        # E) Same run wrote ARCHIVED but didn't get to delete -> resume the delete step.
        if (
            archive_state == "VALID"
            and last_status == "ARCHIVED"
            and delete_after
            and self._audit.is_archived_by_run(
                tid, year, self._ctx.archive_run_id
            )
        ):
            result["action"] = "RESUME_DELETE"
            return result

        # F) Folder missing and no prior delete -> first-time archive (CREATE).
        if archive_state == "MISSING":
            result["action"] = "CREATE"
            result["would_archive"] = self._source_year_count(
                table_config, year, exclusion_clause=exclusion_clause,
            )
            return result

        # G) Remaining cases: VALID + ARCHIVED. Need a watermark to plan an APPEND.
        effective_wm = last_wm
        if not effective_wm:
            effective_wm = self._get_watermark_value(table_config, year)
        result["last_watermark"] = effective_wm

        # No watermark means we can't tell what's already archived -> error.
        if effective_wm is None:
            result["action"] = "ERROR"
            result["error_message"] = ArchiveError.diagnostic_message(
                "FAILED",
                "cannot_determine_incremental_position",
                table=tid,
                year=year,
            )
            result["error_reason"] = "cannot_determine_incremental_position"
            return result

        # Count rows newer than the watermark; zero means SKIP, otherwise APPEND.
        new_count = self._count_new_records(
            table_config, year, effective_wm, exclusion_clause
        )

        if new_count == 0:
            result["action"] = "SKIP"
            return result

        result["action"] = "APPEND"
        result["would_archive"] = new_count
        return result

    def _dry_run_year(
        self,
        table_config: Mapping[str, Any],
        year: int,
        conditions: list,
        exclusion_clause: str,
        archive_state: ArchiveState,
        committed_rows: int,
    ) -> dict[str, Any]:
        """
        Description: Plans one table-year without writing — pulls the action from `_resolve_year_action` and adds the counts the dry-run report needs (NULL-watermark rows + per-condition exclusion totals).
        Parameters: table_config: table config; year: calendar year; conditions: parsed exclusion conditions; exclusion_clause: exclusion SQL; archive_state: VALID/MISSING/ORPHAN; committed_rows: rows already in the archive slice
        Return: dict with action, counts, and error fields
        """
        # Reuse the live decision logic so dry-run and live agree on action + counts.
        action_result = self._resolve_year_action(
            table_config, year, exclusion_clause,
            archive_state=archive_state,
            committed_rows=committed_rows,
        )

        # Surface NULL-watermark rows so operators see what the live run would silently skip.
        null_date_count = self._count_nulls(table_config, year)

        # Per-condition breakdown only matters when we'd actually write rows (CREATE/APPEND).
        per_condition_counts: dict[str, int] = {}
        if action_result["action"] in ("CREATE", "APPEND") and conditions:
            fq = source_fq_from_config(table_config)
            wm_col = table_config["watermark_column"]
            cat = table_config["source_catalog"]
            sch = table_config["source_schema"]
            # Same base predicates as the live archive, minus the AND of all conditions.
            base_where = f"YEAR({wm_col}) = {int(year)} AND {wm_col} IS NOT NULL"
            for c in conditions:
                # Count rows that this single condition would exclude (named, for the report).
                name = c["name"]
                ind = build_individual_condition_sql(c, cat, sch, "src")
                cq = (
                    f"SELECT COUNT(*) AS count FROM {fq} src WHERE {base_where} "
                    f"AND ({ind})"
                )
                per_condition_counts[name] = spark_count(self._spark, cq)

        # Stats payload consumed by the report and the DRY_RUN audit row.
        return {
            "action": action_result["action"],
            "total_eligible": action_result["source_year_count"],
            "would_archive": action_result["would_archive"],
            "per_condition_counts": per_condition_counts,
            "null_date_count": null_date_count,
            "archive_state": action_result["archive_state"],
            "committed_rows": int(committed_rows),
            "last_status": action_result["last_status"],
            "error_message": action_result.get("error_message"),
        }

    def _archive_table_year(
        self,
        merged: dict,
        year: int,
        exclusion_clause: str,
        conditions: list,
        dbutils,
    ) -> dict:
        """
        Description: Runs the full live flow for one table-year — STARTED audit row, action resolution, write (CREATE/APPEND) or RESUME_DELETE, count verify, optional source delete, sidecar metadata. Failures log FAILED and re-raise as ArchiveError.
        Parameters: merged: merged table settings; year: calendar year; exclusion_clause: exclusion SQL; conditions: parsed conditions; dbutils: dbutils or None
        Return: dict with status, record_count, and optional mode
        """
        # NULL watermark check: log loud warning, these rows will not be archived.
        tid = merged["table_id"]
        null_date_count = self._count_nulls(merged, year)
        if null_date_count > 0:
            LOGGER.error(
                "%s year %s: %s rows with NULL %s",
                tid,
                year,
                null_date_count,
                merged["watermark_column"],
            )

        # Surface prior run state so operators see retry / unclean-shutdown context.
        latest_any = self._audit.get_latest_status(tid, year)
        if latest_any and latest_any[0] == "FAILED":
            LOGGER.info(
                "%s year %s: Previous run FAILED (run_id=%s). Retrying.",
                tid,
                year,
                latest_any[1],
            )
        elif latest_any and latest_any[0] == "STARTED":
            LOGGER.warning(
                "%s year %s: Previous run has STARTED status (run_id=%s). "
                "Prior run may not have completed cleanly.",
                tid,
                year,
                latest_any[1],
            )

        # Stake our claim with a STARTED audit row before doing any work.
        self._audit.log_archive(
            table=tid,
            year=year,
            status="STARTED",
            record_count=0,
            null_date_count=null_date_count if null_date_count else None,
        )

        # Concurrency guard: another active STARTED on this slice -> skip; stale STARTED -> warn and proceed.
        stale_h = float(self._ctx.settings.get("stale_started_threshold_hours", 4))
        is_concurrent, is_stale, foreign_run_id, age_hours = (
            self._audit.check_concurrent(
                tid,
                year,
                self._ctx.archive_run_id,
                stale_threshold_hours=stale_h,
            )
        )
        if is_concurrent:
            msg = ArchiveError.diagnostic_message(
                "SKIPPED_CONCURRENT", "concurrent_skip",
                table=tid, year=year, foreign_run_id=foreign_run_id,
                age_hours=age_hours, stale_threshold_hours=stale_h,
            )
            LOGGER.warning(msg)
            self._audit.log_archive(
                table=tid,
                year=year,
                status="SKIPPED_CONCURRENT",
                record_count=0,
                error_message=msg,
            )
            return {"status": "SKIPPED_CONCURRENT", "record_count": 0}
        if is_stale:
            LOGGER.warning(
                "%s year %s: Found stale STARTED from run %s (%.1f hours ago). "
                "Treating as abandoned. Proceeding with caution.",
                tid,
                year,
                foreign_run_id,
                age_hours,
            )

        try:
            # Classify the archive folder and let _resolve_year_action pick the action.
            archive_state, committed_rows = archive_state_and_count(
                self._spark,
                merged["archive_base_path"],
                merged["source_table"],
                year,
            )
            yr_action = self._resolve_year_action(
                merged, year, exclusion_clause,
                archive_state=archive_state,
                committed_rows=committed_rows,
            )
            source_year_count = yr_action["source_year_count"]
            last_wm = yr_action["last_watermark"]
            last_count = yr_action["last_count"]
            action = yr_action["action"]

            # Sanity check: source rows shouldn't shrink between runs.
            if last_count is not None and source_year_count < last_count:
                LOGGER.warning(
                    "%s year %s: source count dropped from %s to %s",
                    tid,
                    year,
                    last_count,
                    source_year_count,
                )

            # ERROR action from the decision logic -> raise; handled by the except block below.
            if action == "ERROR":
                raise ArchiveOperationError(
                    yr_action["error_message"],
                    table=tid,
                    year=year,
                    operation="archive",
                    reason=yr_action["error_reason"],
                )

            delete_after = bool(merged.get("delete_after_archive"))

            # RESUME_DELETE: same run already archived; finish the delete step in a helper.
            # (Distinct from the delete_after branch below: RESUME_DELETE recovers a
            #  same-run partial; delete_after runs immediately after a fresh write.)
            if action == "RESUME_DELETE":
                return self._execute_resume_delete(
                    merged,
                    year,
                    exclusion_clause,
                    conditions,
                    dbutils,
                    last_wm=last_wm,
                    source_year_count=source_year_count,
                )

            # SKIP: nothing new to archive; record the skip and exit.
            if action == "SKIP":
                self._audit.log_archive(
                    table=tid,
                    year=year,
                    status="SKIPPED",
                    record_count=0,
                    source_year_count=source_year_count,
                    archive_mode="SKIP",
                )
                return {"status": "SKIPPED", "record_count": 0}

            effective_wm = yr_action["last_watermark"]

            # APPEND vs CREATE write: APPEND adds rows past the watermark, CREATE writes the whole year.
            if action == "APPEND":
                # Block APPEND if a prior run flagged drift; operator must reconcile first.
                if self._audit.has_verify_failed(tid, year):
                    raise ArchiveOperationError(
                        f"{tid} year {year}: previous run left VERIFY_FAILED status. "
                        f"See docs/runbooks/verify-failed.md.",
                        table=tid,
                        year=year,
                        operation="append",
                        reason="verify_failed_requires_reconcile",
                    )
                archived = self._append_year(
                    merged, year, exclusion_clause, last_wm=effective_wm,
                )
                self._verify_archive(
                    merged, year, archived - int(committed_rows),
                    exclusion_clause, after_watermark=effective_wm,
                )
            elif action == "CREATE":
                archived = self._create_year(
                    merged, year, exclusion_clause,
                )
                self._verify_archive(
                    merged, year, archived - int(committed_rows), exclusion_clause,
                )
            else:
                raise ArchiveOperationError(
                    f"Unexpected action '{action}' in write branch",
                    table=tid, year=year,
                    operation="archive", reason="invalid_action",
                )

            # Capture the Delta version we just wrote.
            archive_delta_version = get_archive_delta_version(
                self._spark,
                merged["archive_base_path"],
                merged["source_table"],
                year,
            )
            if archive_delta_version is None:
                msg = ArchiveError.diagnostic_message(
                    "FAILED", "version_capture_failed",
                    table=tid, year=year,
                    path=archive_path_from_config(merged, year),
                )
                raise ArchiveOperationError(
                    msg, table=tid, year=year,
                    operation="archive", reason="version_capture_failed",
                )

            # rows_this_run = rows written this run (delta on APPEND, full year on CREATE).
            # For ARCHIVED_AND_DELETED this is also the count deleted from source.
            rows_this_run = archived - int(committed_rows)
            watermark_value = self._get_watermark_value(merged, year)
            archive_mode = action

            # Write the ARCHIVED audit row (always written, even when delete_after follows).
            self._audit.log_archive(
                table=tid,
                year=year,
                status="ARCHIVED",
                record_count=rows_this_run,
                conditions_applied=json.dumps(get_condition_names(conditions)),
                null_date_count=null_date_count if null_date_count else None,
                watermark_value=watermark_value,
                source_year_count=source_year_count,
                archive_mode=archive_mode,
                archive_delta_version=archive_delta_version,
            )

            # Optional delete step: scope DELETE to this run's watermark window, then log ARCHIVED_AND_DELETED.
            if delete_after:
                # Ownership guard: only the run that wrote the ARCHIVED row may delete the source.
                if not self._audit.is_archived_by_run(tid, year, self._ctx.archive_run_id):
                    msg = ArchiveError.diagnostic_message(
                        "FAILED", "ownership",
                        table=tid, year=year,
                        archive_run_id=self._ctx.archive_run_id,
                        audit_table=self._audit._archive_table(),
                    )
                    raise ArchiveOperationError(msg, table=tid, year=year, operation="delete", reason="ownership")
                # Pick the lower bound: APPEND deletes only newly-archived rows; CREATE covers the whole year.
                if action == "APPEND":
                    after_watermark_for_delete = effective_wm
                elif action == "CREATE":
                    after_watermark_for_delete = None
                else:
                    raise ArchiveOperationError(
                        f"Unexpected action '{action}' in delete branch",
                        table=tid, year=year, operation="delete", reason="invalid_action",
                    )
                run_wm_low, run_wm_high = self._run_watermark_window(
                    merged, year, after_watermark=after_watermark_for_delete,
                )
                self._delete_archived(
                    merged, year, exclusion_clause, run_wm_low, run_wm_high
                )
                self._audit.log_archive(
                    table=tid,
                    year=year,
                    status="ARCHIVED_AND_DELETED",
                    record_count=rows_this_run,
                    watermark_value=watermark_value,
                    source_year_count=source_year_count,
                    archive_mode=archive_mode,
                    archive_delta_version=archive_delta_version,
                )

            # Sidecar metadata JSON next to the Delta path (best-effort, requires dbutils).
            meta_mode = archive_mode.lower()
            if delete_after:
                meta_mode = f"{meta_mode}_deleted"
            self._write_metadata(
                merged,
                year,
                rows_this_run,
                conditions,
                meta_mode,
                dbutils,
                watermark_value=watermark_value,
                source_year_count=source_year_count,
            )
            final_status = "ARCHIVED_AND_DELETED" if delete_after else "ARCHIVED"
            return {"status": final_status, "record_count": rows_this_run, "mode": archive_mode}
        except Exception as exc:
            # Failure path: log FAILED row (unless already logged), then re-raise as ArchiveError.
            already_logged = (
                isinstance(exc, ArchiveVerificationError)
                and getattr(exc, "already_logged", False)
            )
            if not already_logged:
                self._audit.log_archive(
                    table=tid,
                    year=year,
                    status="FAILED",
                    record_count=0,
                    error_message=str(exc),
                )
            if isinstance(exc, ArchiveError):
                raise
            msg = ArchiveError.diagnostic_message(
                "FAILED", "operation_failure",
                table=tid, year=year, operation="archive", error=str(exc),
            )
            raise ArchiveOperationError(msg, table=tid, year=year, operation="archive", reason="operation_failure") from exc

    def _execute_resume_delete(
        self,
        merged: dict,
        year: int,
        exclusion_clause: str,
        conditions: list,
        dbutils,
        *,
        last_wm: Optional[date],
        source_year_count: int,
    ) -> dict:
        """
        Description: Completes a same-run delete that earlier failed after a successful archive write — re-counts the archive, drift-checks source, verifies ownership, scoped DELETE, and logs ARCHIVED_AND_DELETED.
        Parameters: merged: merged settings; year: calendar year; exclusion_clause: exclusion SQL; conditions: parsed conditions; dbutils: dbutils or None; last_wm: last watermark (audit metadata); source_year_count: source-year total (audit metadata)
        Return: {"status": "ARCHIVED_AND_DELETED", "record_count": cnt}
        """
        # Re-count the archive slice unfiltered (folder by construction only holds
        # rows that survived exclusion). Drift vs. the source is checked below.
        tid = merged["table_id"]
        path = archive_path_from_config(merged, year)
        wm_col = merged["watermark_column"]
        cnt = spark_count(
            self._spark,
            f"SELECT COUNT(*) AS count FROM delta.`{path}` src "
            f"WHERE YEAR(src.{wm_col}) = {int(year)} "
            f"AND src.{wm_col} IS NOT NULL",
        )

        # Drift check: compare source against the freshly-counted archive. The
        # audit's record_count is a per-run delta (rows written *that* run),
        # not a cumulative slice size, so it is not usable here.
        archive_expected = cnt
        source_count = self._source_year_count(
            merged, year, exclusion_clause=exclusion_clause,
        )
        if source_count != archive_expected:
            # Source moved since archive was written -> log VERIFY_FAILED and bail.
            self._audit.log_verify_failed(
                tid,
                year,
                archive_expected,
                source_count,
            )
            raise ArchiveVerificationError(
                "RESUME_DELETE: source count diverged from archive",
                table=tid,
                year=year,
                expected=archive_expected,
                actual=source_count,
                reason="source_drift",
                already_logged=True,
            )

        # Ownership guard: only the run that wrote the ARCHIVED row may delete.
        # Resume-delete is by definition the same run, so this re-check passes — it
        # is kept as a defensive bail-out in case the ARCHIVED row was overwritten.
        if not self._audit.is_archived_by_run(tid, year, self._ctx.archive_run_id):
            msg = ArchiveError.diagnostic_message(
                "FAILED", "ownership",
                table=tid, year=year,
                archive_run_id=self._ctx.archive_run_id,
                audit_table=self._audit._archive_table(),
            )
            raise ArchiveOperationError(msg, table=tid, year=year, operation="delete", reason="ownership")

        # Live delete: scope by full year watermark window (no after_watermark on a resume).
        run_wm_low, run_wm_high = self._run_watermark_window(
            merged, year, after_watermark=None
        )
        self._delete_archived(
            merged, year, exclusion_clause, run_wm_low, run_wm_high
        )

        # Audit row + sidecar metadata: ARCHIVED_AND_DELETED with prior watermark/source count.
        self._audit.log_archive(
            table=tid,
            year=year,
            status="ARCHIVED_AND_DELETED",
            record_count=cnt,
            watermark_value=last_wm,
            source_year_count=source_year_count,
        )
        self._write_metadata(merged, year, cnt, conditions, "resume_delete", dbutils)
        return {"status": "ARCHIVED_AND_DELETED", "record_count": cnt}

    def run(
        self,
        table_config: Mapping[str, Any],
        dry_run: bool = True,
        dbutils=None,
    ) -> dict[str, Any]:
        """
        Description: Top-level archive entry point. `dry_run=True` plans every eligible year without writing; `dry_run=False` archives, verifies, and (if `delete_after_archive` is set) deletes from source.
        Parameters: table_config: table config; dry_run: plan only when True; dbutils: dbutils or None for filesystem checks
        Return: report dict with per-year stats (dry-run) or year results (live)
        """
        # Preamble: merge config, validate, parse conditions, set tz, ensure audit table.
        merged, conditions, exclusion_clause, tid = self._prepare_run(table_config)

        # Pick the years past the retention cutoff; everything below loops over these.
        years = self._calculate_eligible_years(
            merged,
            int(merged["retention_years"]),
        )

        if dry_run:
            # Dry-run branch: read-only planning, no writes to source or archive.
            report: dict[str, Any] = {"tables": {tid: {"years": {}}}}
            for y in years:
                # Classify the archive folder (VALID / MISSING / ORPHAN) and grab committed row count.
                try:
                    archive_state, committed_rows = archive_state_and_count(
                        self._spark,
                        merged["archive_base_path"],
                        merged["source_table"],
                        y,
                    )
                except Exception as exc:
                    self._audit.log_archive(
                        table=tid, year=y, status="FAILED",
                        record_count=0, error_message=str(exc),
                    )
                    raise

                # Plan the year: action, counts, per-condition breakdown, error fields.
                stats = self._dry_run_year(
                    merged, y, conditions, exclusion_clause,
                    archive_state=archive_state,
                    committed_rows=committed_rows,
                )

                # NULL watermark rows would be skipped by the live run; surface them.
                if stats["null_date_count"] > 0:
                    LOGGER.error(
                        "%s year %s: %s NULL dates",
                        tid, y, stats["null_date_count"],
                    )

                # Map the live action label to its dry-run counterpart (e.g. CREATE -> WOULD_ARCHIVE).
                audit_action = _ARCHIVE_TO_DRY_RUN_ACTION.get(
                    stats["action"], stats["action"]
                )

                # Write a DRY_RUN audit row and add this year to the report.
                self._audit.log_dry_run(
                    table=tid,
                    year=y,
                    total_eligible=stats["total_eligible"],
                    would_archive=stats["would_archive"],
                    per_condition_counts=stats["per_condition_counts"],
                    null_date_count=stats["null_date_count"] or None,
                    action=audit_action,
                )
                report["tables"][tid]["years"][y] = stats
            result = report
        else:
            # Live-run branch: archive each eligible year (write + verify + optional delete + audit).
            year_results: dict[int, dict] = {}
            for y in years:
                year_results[y] = self._archive_table_year(
                    merged, y, exclusion_clause, conditions, dbutils,
                )
            result = {"tables": {tid: {"years": year_results}}}
        return result

