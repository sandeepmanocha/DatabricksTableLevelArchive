"""
Rehydration engine — restores archived data back to queryable form.

Reads previously archived year-partitioned Delta folders and materializes
them as tables (or views) in a target schema so users can query historical
data on demand. Validates schema compatibility against the current source
and records each rehydration in the audit log.
"""

import json
import logging

from src.exceptions import ArchiveError, ArchiveOperationError
from src.utils import build_archive_path, create_schema_if_not_exists

LOGGER = logging.getLogger("caresource_archive.rehydrator")


def _describe_columns(spark, fq) -> list[str]:
    """Return ordered column names from DESCRIBE TABLE, stopping at metadata sections."""
    rows = spark.sql(f"DESCRIBE TABLE {fq}").collect()
    names = []
    for row in rows:
        col_name = row["col_name"]
        if col_name is None or col_name == "" or str(col_name).startswith("#"):
            break
        names.append(col_name)
    return names


class RehydrationEngine:
    def __init__(self, ctx, audit, spark) -> None:
        """
        Description: Store runtime context, audit helper, and Spark session on the engine.
        Parameters: ctx: runtime context; audit: audit logger; spark: Spark session
        Return: None
        """
        self._ctx = ctx
        self._audit = audit
        self._spark = spark

    def _source_base_name(self, source_table) -> str:
        """
        Description: Return the last dot-separated segment of the source table identifier.
        Parameters: source_table: table name string, possibly catalog.schema.table
        Return: Base table name without leading catalog/schema segments.
        """
        parts = source_table.strip().split(".")
        return parts[-1] if parts else source_table.strip()

    def _create_archive_view(self, source_table, year, fq_view, loc_path) -> None:
        """
        Description: Create or replace a view over one year of archived Delta data.
        Parameters: source_table: source table for diagnostics; year: archive year; fq_view: fully qualified view name; loc_path: Delta storage path
        Return: None
        """
        view_sql = (
            f"CREATE OR REPLACE VIEW {fq_view} AS SELECT * FROM delta.`{loc_path}`"
        )
        try:
            self._spark.sql(view_sql)
        except Exception as exc:
            msg = ArchiveError.diagnostic_message(
                "FAILED",
                "operation_failure",
                table=source_table,
                year=year,
                operation="create_archive_view",
                error=str(exc),
            )
            raise ArchiveOperationError(
                msg,
                table=source_table,
                year=year,
                operation="create_archive_view",
                reason="view_create_failed",
            ) from exc

    def run(self, params) -> dict:
        """
        Description: Restore requested archive years into per-year views, optional unified view, and audit log.
        Parameters: params: dict of paths, table names, years, and rehydration flags
        Return: Dict with tables_created, view_name, status, restored_years, and skipped_years
        """
        archive_base_path = ""
        source_table = ""
        target_catalog = ""
        target_schema = ""
        years = []
        view_name = None
        tables_created = 0
        created_years = []
        skipped_years = []

        try:
            required_keys = (
                "archive_base_path",
                "source_table",
                "target_catalog",
                "target_schema",
                "years",
                "available_archive_years",
            )
            missing_keys = [key for key in required_keys if key not in params]
            if missing_keys:
                raise ArchiveOperationError(
                    f"Missing required rehydrate params: {', '.join(missing_keys)}. "
                    "Notebook must resolve archive folder availability and pass "
                    "'available_archive_years'.",
                    table=params.get("source_table"),
                    year="all",
                    operation="rehydrate",
                    reason="invalid_params",
                )
            archive_base_path = params["archive_base_path"]
            source_table = params["source_table"]
            target_catalog = params["target_catalog"]
            target_schema = params["target_schema"]
            years = [int(year) for year in params["years"]]
            available_archive_years = {
                int(year) for year in params["available_archive_years"]
            }
            table_prefix = params.get("table_prefix", "")
            create_unified_view = params.get("create_unified_view", True)
            include_live_data = params.get("include_live_data", False)
            unified_view_suffix = params.get("unified_view_suffix", "_unified")

            base_name = self._source_base_name(source_table)
            prefixed_base = f"{table_prefix}{base_name}"
            view_name = (
                f"{target_catalog}.{target_schema}.{prefixed_base}{unified_view_suffix}"
                if create_unified_view
                else None
            )
            create_schema_if_not_exists(self._spark, target_catalog, target_schema)
            self._audit.ensure_rehydration_audit_table()

            for year in years:
                if year not in available_archive_years:
                    LOGGER.warning(
                        "archive folder missing for year %s under %s, skipping",
                        year,
                        archive_base_path,
                    )
                    skipped_years.append(year)
                    continue
                loc_path = build_archive_path(archive_base_path, base_name, year)
                ext_fq = f"{target_catalog}.{target_schema}.{prefixed_base}_year_{year}"
                self._create_archive_view(source_table, year, ext_fq, loc_path)
                tables_created += 1
                created_years.append(year)

            if not created_years:
                raise ArchiveOperationError(
                    ArchiveError.diagnostic_message(
                        "FAILED",
                        "operation_failure",
                        table=source_table,
                        year="all",
                        operation="rehydrate",
                        error="no requested years restored",
                    ),
                    table=source_table,
                    year="all",
                    operation="rehydrate",
                    reason="no_years_restored",
                )

            if create_unified_view:
                participant_fqs = []
                if include_live_data:
                    participant_fqs.append(source_table)
                for y in created_years:
                    participant_fqs.append(
                        f"{target_catalog}.{target_schema}.{prefixed_base}_year_{y}"
                    )
                if len(participant_fqs) > 1:
                    first_fq = participant_fqs[0]
                    expected = _describe_columns(self._spark, first_fq)
                    for fq in participant_fqs[1:]:
                        actual = _describe_columns(self._spark, fq)
                        if actual != expected:
                            raise ArchiveOperationError(
                                msg=(
                                    f"rehydrate schema mismatch: {fq} columns {actual} "
                                    f"differ from {first_fq} columns {expected}"
                                ),
                                table=fq,
                                operation="rehydrate_unified_view",
                                reason="schema_mismatch",
                            )
                select_parts = []
                if include_live_data:
                    select_parts.append(f"SELECT * FROM {source_table}")
                for y in created_years:
                    ext_fq = f"{target_catalog}.{target_schema}.{prefixed_base}_year_{y}"
                    select_parts.append(f"SELECT * FROM {ext_fq}")
                union_body = " UNION ALL ".join(select_parts)
                view_sql = f"CREATE OR REPLACE VIEW {view_name} AS {union_body}"
                try:
                    self._spark.sql(view_sql)
                except Exception as exc:
                    msg = ArchiveError.diagnostic_message(
                        "FAILED",
                        "operation_failure",
                        table=source_table,
                        year="all",
                        operation="create_unified_view",
                        error=f"{exc}; sql={view_sql[:500]}",
                    )
                    raise ArchiveOperationError(
                        msg,
                        table=source_table,
                        year="all",
                        operation="create_unified_view",
                        reason="view_create_failed",
                    ) from exc

            if tables_created == len(years):
                run_status = "COMPLETED"
            else:
                run_status = "PARTIAL_COMPLETED"

            self._audit.log_rehydrate(
                archive_path=archive_base_path,
                source=source_table,
                target_catalog=target_catalog,
                target_schema=target_schema,
                years=json.dumps(years),
                tables_created=tables_created,
                status=run_status,
                error_message=None,
            )
            return {
                "tables_created": tables_created,
                "view_name": view_name,
                "status": run_status,
                "restored_years": created_years,
                "skipped_years": skipped_years,
            }
        except Exception as exc:
            try:
                error_message = (
                    f"{exc}; created_years={created_years}; skipped_years={skipped_years}"
                )
                self._audit.log_rehydrate(
                    archive_path=archive_base_path,
                    source=source_table,
                    target_catalog=target_catalog,
                    target_schema=target_schema,
                    years=json.dumps(years),
                    tables_created=tables_created,
                    status="FAILED",
                    error_message=error_message,
                )
            except Exception as audit_exc:
                fallback_payload = {
                    "event": "rehydration_audit_write_failed",
                    "archive_path": archive_base_path,
                    "source_table": source_table,
                    "target_catalog": target_catalog,
                    "target_schema": target_schema,
                    "years": years,
                    "tables_created": tables_created,
                    "created_years": created_years,
                    "skipped_years": skipped_years,
                    "status": "FAILED",
                    "primary_error": str(exc),
                    "audit_error": str(audit_exc),
                }
                LOGGER.error(json.dumps(fallback_payload, sort_keys=True))
                msg = ArchiveError.diagnostic_message(
                    "FAILED",
                    "operation_failure",
                    table=source_table,
                    year="all",
                    operation="rehydrate",
                    error=f"{exc}; audit_write_error={audit_exc}",
                )
                raise ArchiveOperationError(
                    msg,
                    table=source_table,
                    year="all",
                    operation="rehydrate",
                    reason="operation_failure_and_audit_failure",
                ) from exc
            if isinstance(exc, ArchiveOperationError):
                raise
            msg = ArchiveError.diagnostic_message(
                "FAILED",
                "operation_failure",
                table=source_table,
                year="all",
                operation="rehydrate",
                error=str(exc),
            )
            raise ArchiveOperationError(
                msg,
                table=source_table,
                year="all",
                operation="rehydrate",
                reason="operation_failure",
            ) from exc
