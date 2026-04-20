import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

from src.conditions import (
    build_exclusion_clause,
    build_individual_condition_sql,
    get_condition_names,
    normalize_condition,
)
from src.config import merge_settings
from src.exceptions import ArchiveError, ArchiveOperationError, ArchiveVerificationError
from src.utils import (
    archive_folder_exists,
    archive_path_from_config,
    source_fq_from_config,
    spark_count,
    sql_date_or_null,
    sql_quote,
)

LOGGER = logging.getLogger("caresource_archive.archiver")


def _extract_year(row: Any, key: str = "yr") -> int:
    if isinstance(row, dict):
        return int(row[key])
    v = getattr(row, key, None)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    return int(row[key])


def _parse_conditions(raw: Any) -> list:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return []
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list):
        return [normalize_condition(c) for c in raw]
    return []


def _resolve_exclusion(exclusion_clause: str, strip_alias: bool = False) -> str:
    if not exclusion_clause:
        return "1=1"
    if strip_alias:
        return exclusion_clause.replace("src.", "")
    return exclusion_clause


class ArchiveEngine:
    def __init__(self, ctx, audit, spark):
        self._ctx = ctx
        self._audit = audit
        self._spark = spark

    def _set_timezone(self, merged: Mapping[str, Any]) -> None:
        self._spark.conf.set("spark.sql.session.timeZone", merged["timezone"])

    def _calculate_eligible_years(
        self,
        table_config: Mapping[str, Any],
        retention_years: int,
    ) -> list[int]:
        fq = source_fq_from_config(table_config)
        wm_col = table_config["watermark_column"]
        cy_row = self._spark.sql("SELECT YEAR(current_date()) AS y").first()
        cy = _extract_year(cy_row, "y")
        cutoff = cy - int(retention_years)
        sql = (
            f"SELECT DISTINCT YEAR({wm_col}) AS yr FROM {fq} "
            f"WHERE {wm_col} IS NOT NULL ORDER BY yr"
        )
        rows = self._spark.sql(sql).collect()
        years = []
        for r in rows:
            try:
                yv = _extract_year(r, "yr")
            except Exception:
                continue
            years.append(yv)
        return [y for y in years if y <= cutoff]

    def _year_where_sql(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
        alias: str,
        after_watermark: Optional[date] = None,
    ) -> str:
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
        fq = source_fq_from_config(table_config)
        wm_col = table_config["watermark_column"]
        q = (
            f"SELECT COUNT(*) AS count FROM {fq} "
            f"WHERE YEAR({wm_col}) = {int(year)} AND {wm_col} IS NOT NULL"
        )
        return spark_count(self._spark, q)

    def _get_watermark_value(self, table_config: Mapping[str, Any], year: int) -> Optional[date]:
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
        fq = source_fq_from_config(table_config)
        wm_col = table_config["watermark_column"]
        exc = _resolve_exclusion(exclusion_clause)
        q = (
            f"SELECT COUNT(*) AS count FROM {fq} src "
            f"WHERE YEAR(src.{wm_col}) = {int(year)} "
            f"AND src.{wm_col} > {sql_date_or_null(last_watermark)} "
            f"AND src.{wm_col} IS NOT NULL AND ({exc})"
        )
        return spark_count(self._spark, q)

    def _archive_year(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
    ) -> int:
        fq = source_fq_from_config(table_config)
        path = archive_path_from_config(table_config, year)
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
        return self._count_archive_delta(table_config, year, exclusion_clause)

    def _count_archive_delta(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
    ) -> int:
        path = archive_path_from_config(table_config, year)
        wm_col = table_config["watermark_column"]
        dex = _resolve_exclusion(exclusion_clause, strip_alias=True)
        q = (
            f"SELECT COUNT(*) AS count FROM delta.`{path}` "
            f"WHERE YEAR({wm_col}) = {int(year)} "
            f"AND {wm_col} IS NOT NULL AND ({dex})"
        )
        return spark_count(self._spark, q)

    def _verify_archive(
        self,
        table_config: Mapping[str, Any],
        year: int,
        expected_count: int,
    ) -> None:
        actual = self._count_archive_delta(
            table_config,
            year,
            self._exclusion_clause_for_config(table_config),
        )
        if actual != expected_count:
            tid = table_config.get("table_id", source_fq_from_config(table_config))
            msg = ArchiveError.diagnostic_message(
                "FAILED", "count_mismatch",
                table=tid, year=year, expected=expected_count, actual=actual,
                path=archive_path_from_config(table_config, year),
            )
            raise ArchiveVerificationError(msg, table=tid, year=year, expected=expected_count, actual=actual, reason="count_mismatch")

    def _exclusion_clause_for_config(self, table_config: Mapping[str, Any]) -> str:
        conds = _parse_conditions(table_config.get("exclusion_conditions"))
        return build_exclusion_clause(
            conds,
            table_config["source_catalog"],
            table_config["source_schema"],
            "src",
        )

    def _delete_archived(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
    ) -> None:
        fq = source_fq_from_config(table_config)
        wm_col = table_config["watermark_column"]
        dex = _resolve_exclusion(exclusion_clause, strip_alias=True)
        sql = (
            f"DELETE FROM {fq} WHERE YEAR({wm_col}) = {int(year)} "
            f"AND {wm_col} IS NOT NULL AND ({dex})"
        )
        try:
            self._spark.sql(sql)
        except Exception as exc:
            tid = table_config.get("table_id", fq)
            msg = ArchiveError.diagnostic_message(
                "FAILED", "operation_failure",
                table=tid, year=year, operation="delete", error=str(exc),
            )
            raise ArchiveOperationError(msg, table=tid, year=year, operation="delete", reason="operation_failure") from exc

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
        fq = source_fq_from_config(table_config)
        wm_col = table_config["watermark_column"]
        q = (
            f"SELECT COUNT(*) AS count FROM {fq} "
            f"WHERE YEAR({wm_col}) = {int(year)} AND {wm_col} IS NULL"
        )
        return spark_count(self._spark, q)

    def _resolve_year_action(
        self,
        table_config: Mapping[str, Any],
        year: int,
        exclusion_clause: str,
        folder_exists: bool,
    ) -> dict[str, Any]:
        """Determine what action the archiver would take for this table+year.

        Centralized decision logic (read-only queries; no writes, no dbutils).
        Both dry-run and live-run paths call this to avoid duplicating the
        mode-determination logic.
        """
        tid = table_config["table_id"]
        source_year_count = self._count_source_by_year(table_config, year)
        last_status, last_wm, last_count = self._audit.get_last_run_state(tid, year)

        result: dict[str, Any] = {
            "source_year_count": source_year_count,
            "folder_exists": folder_exists,
            "last_status": last_status,
            "last_watermark": last_wm,
            "last_count": last_count,
            "would_archive": 0,
            "error_message": None,
            "error_reason": None,
        }

        if not folder_exists and last_status == "ARCHIVED_AND_DELETED":
            result["action"] = "ERROR"
            result["error_message"] = ArchiveError.diagnostic_message(
                "FAILED", "missing_folder_after_delete",
                table=tid, year=year,
                path=archive_path_from_config(table_config, year),
            )
            result["error_reason"] = "missing_folder_after_delete"
            return result

        if folder_exists and last_status is None:
            result["action"] = "ERROR"
            result["error_message"] = ArchiveError.diagnostic_message(
                "FAILED", "orphan_folder",
                table=tid, year=year,
                path=archive_path_from_config(table_config, year),
            )
            result["error_reason"] = "orphan_folder"
            return result

        delete_after = bool(table_config.get("delete_after_archive"))

        if last_status == "ARCHIVED" and delete_after:
            result["action"] = "RESUME_DELETE"
            return result

        if not folder_exists:
            fq = source_fq_from_config(table_config)
            wm_col = table_config["watermark_column"]
            exc = _resolve_exclusion(exclusion_clause)
            q = (
                f"SELECT COUNT(*) AS count FROM {fq} src "
                f"WHERE YEAR(src.{wm_col}) = {int(year)} "
                f"AND src.{wm_col} IS NOT NULL AND ({exc})"
            )
            result["action"] = "CREATE"
            result["would_archive"] = spark_count(self._spark, q)
            return result

        effective_wm = last_wm
        if not effective_wm:
            effective_wm = self._get_watermark_value(table_config, year)
        result["last_watermark"] = effective_wm

        new_count = (
            self._count_new_records(table_config, year, effective_wm, exclusion_clause)
            if effective_wm
            else 0
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
        folder_exists: bool = False,
    ) -> dict[str, Any]:
        action_result = self._resolve_year_action(
            table_config, year, exclusion_clause, folder_exists,
        )
        null_date_count = self._count_nulls(table_config, year)

        per_condition_counts: dict[str, int] = {}
        if action_result["action"] in ("CREATE", "APPEND") and conditions:
            fq = source_fq_from_config(table_config)
            wm_col = table_config["watermark_column"]
            cat = table_config["source_catalog"]
            sch = table_config["source_schema"]
            base_where = f"YEAR({wm_col}) = {int(year)} AND {wm_col} IS NOT NULL"
            for c in conditions:
                name = c["name"]
                ind = build_individual_condition_sql(c, cat, sch, "src")
                cq = (
                    f"SELECT COUNT(*) AS count FROM {fq} src WHERE {base_where} "
                    f"AND ({ind})"
                )
                per_condition_counts[name] = spark_count(self._spark, cq)

        return {
            "action": action_result["action"],
            "total_eligible": action_result["source_year_count"],
            "would_archive": action_result["would_archive"],
            "per_condition_counts": per_condition_counts,
            "null_date_count": null_date_count,
            "folder_exists": action_result["folder_exists"],
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
        self._audit.log_archive(
            table=tid,
            year=year,
            status="STARTED",
            record_count=0,
            null_date_count=null_date_count if null_date_count else None,
        )
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
            folder = (
                archive_folder_exists(
                    dbutils, merged["archive_base_path"], merged["source_table"], year
                )
                if dbutils is not None
                else False
            )
            yr_action = self._resolve_year_action(
                merged, year, exclusion_clause, folder,
            )
            source_year_count = yr_action["source_year_count"]
            last_wm = yr_action["last_watermark"]
            last_count = yr_action["last_count"]
            action = yr_action["action"]

            if last_count is not None and source_year_count < last_count:
                LOGGER.warning(
                    "%s year %s: source count dropped from %s to %s",
                    tid,
                    year,
                    last_count,
                    source_year_count,
                )

            if action == "ERROR":
                raise ArchiveOperationError(
                    yr_action["error_message"],
                    table=tid,
                    year=year,
                    operation="archive",
                    reason=yr_action["error_reason"],
                )

            delete_after = bool(merged.get("delete_after_archive"))

            if action == "RESUME_DELETE":
                cnt = self._count_archive_delta(merged, year, exclusion_clause)
                self._verify_archive(merged, year, cnt)
                if not self._audit.is_archived_by_run(tid, year, self._ctx.archive_run_id):
                    msg = ArchiveError.diagnostic_message(
                        "FAILED", "ownership",
                        table=tid, year=year,
                        archive_run_id=self._ctx.archive_run_id,
                        audit_table=self._audit._archive_table(),
                    )
                    raise ArchiveOperationError(msg, table=tid, year=year, operation="delete", reason="ownership")
                self._delete_archived(merged, year, exclusion_clause)
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

            if action == "APPEND":
                fq = source_fq_from_config(merged)
                path = archive_path_from_config(merged, year)
                where_sql = self._year_where_sql(
                    merged, year, exclusion_clause, "src", after_watermark=effective_wm
                )
                write_sql = f"INSERT INTO delta.`{path}` SELECT * FROM {fq} src WHERE {where_sql}"
                self._spark.sql(write_sql)
                archived = self._count_archive_delta(merged, year, exclusion_clause)
            else:
                archived = self._archive_year(
                    merged, year, exclusion_clause
                )

            self._verify_archive(merged, year, archived)
            watermark_value = self._get_watermark_value(merged, year)
            archive_mode = action
            self._audit.log_archive(
                table=tid,
                year=year,
                status="ARCHIVED",
                record_count=archived,
                conditions_applied=json.dumps(get_condition_names(conditions)),
                null_date_count=null_date_count if null_date_count else None,
                watermark_value=watermark_value,
                source_year_count=source_year_count,
                archive_mode=archive_mode,
            )
            if delete_after:
                if not self._audit.is_archived_by_run(tid, year, self._ctx.archive_run_id):
                    msg = ArchiveError.diagnostic_message(
                        "FAILED", "ownership",
                        table=tid, year=year,
                        archive_run_id=self._ctx.archive_run_id,
                        audit_table=self._audit._archive_table(),
                    )
                    raise ArchiveOperationError(msg, table=tid, year=year, operation="delete", reason="ownership")
                self._delete_archived(merged, year, exclusion_clause)
                self._audit.log_archive(
                    table=tid,
                    year=year,
                    status="ARCHIVED_AND_DELETED",
                    record_count=archived,
                    watermark_value=watermark_value,
                    source_year_count=source_year_count,
                    archive_mode=archive_mode,
                )
            meta_mode = archive_mode.lower()
            if delete_after:
                meta_mode = f"{meta_mode}_deleted"
            self._write_metadata(
                merged,
                year,
                archived,
                conditions,
                meta_mode,
                dbutils,
                watermark_value=watermark_value,
                source_year_count=source_year_count,
            )
            final_status = "ARCHIVED_AND_DELETED" if delete_after else "ARCHIVED"
            return {"status": final_status, "record_count": archived, "mode": archive_mode}
        except Exception as exc:
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

    def run(
        self,
        table_config: Mapping[str, Any],
        dry_run: bool = True,
        dbutils=None,
    ) -> dict[str, Any]:
        merged = merge_settings(self._ctx.settings, dict(table_config))
        conditions = _parse_conditions(merged.get("exclusion_conditions"))
        exclusion_clause = build_exclusion_clause(
            conditions,
            merged["source_catalog"],
            merged["source_schema"],
            "src",
        )
        self._set_timezone(merged)
        self._audit.ensure_archive_audit_table()
        tid = merged["table_id"]
        years = self._calculate_eligible_years(
            merged,
            int(merged["retention_years"]),
        )
        if dry_run:
            report: dict[str, Any] = {"tables": {}}
            report["tables"][tid] = {"years": {}}
            for y in years:
                try:
                    folder = (
                        archive_folder_exists(
                            dbutils, merged["archive_base_path"], merged["source_table"], y
                        )
                        if dbutils is not None
                        else False
                    )
                except Exception as exc:
                    self._audit.log_archive(
                        table=tid, year=y, status="FAILED",
                        record_count=0, error_message=str(exc),
                    )
                    raise
                stats = self._dry_run_year(
                    merged, y, conditions, exclusion_clause, folder_exists=folder,
                )
                if stats["null_date_count"] > 0:
                    LOGGER.error(
                        "%s year %s: %s NULL dates",
                        tid,
                        y,
                        stats["null_date_count"],
                    )
                self._audit.log_dry_run(
                    table=tid,
                    year=y,
                    total_eligible=stats["total_eligible"],
                    would_archive=stats["would_archive"],
                    per_condition_counts=stats["per_condition_counts"],
                    null_date_count=stats["null_date_count"] or None,
                    action=stats["action"],
                )
                report["tables"][tid]["years"][y] = stats
            return report
        year_results: dict[int, dict] = {}
        for y in years:
            year_results[y] = self._archive_table_year(
                merged,
                y,
                exclusion_clause,
                conditions,
                dbutils,
            )
        return {"tables": {tid: {"years": year_results}}}

    def validate_archives(
        self,
        table_configs: list,
        dbutils,
    ) -> dict[str, Any]:
        missing = []
        for tc in table_configs:
            merged = merge_settings(self._ctx.settings, dict(tc))
            years = self._calculate_eligible_years(
                merged,
                int(merged["retention_years"]),
            )
            for y in years:
                if not archive_folder_exists(
                    dbutils, merged["archive_base_path"], merged["source_table"], y
                ):
                    missing.append(
                        {
                            "table_id": merged["table_id"],
                            "year": y,
                            "path": archive_path_from_config(merged, y),
                        }
                    )
        return {"valid": len(missing) == 0, "missing": missing}
