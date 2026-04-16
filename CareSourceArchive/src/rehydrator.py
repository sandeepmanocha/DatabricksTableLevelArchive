import json
import logging

from src.exceptions import ArchiveError, ArchiveOperationError
from src.utils import build_archive_path, create_schema_if_not_exists

LOGGER = logging.getLogger("caresource_archive.rehydrator")


class RehydrationEngine:
    def __init__(self, ctx, audit, spark):
        self._ctx = ctx
        self._audit = audit
        self._spark = spark

    def _source_base_name(self, source_table):
        parts = source_table.strip().split(".")
        return parts[-1] if parts else source_table.strip()

    def _create_external_table(self, source_table, year, fq_table, loc_path):
        safe_loc_path = loc_path.replace("'", "''")
        loc_sql = (
            f"CREATE TABLE IF NOT EXISTS {fq_table} USING DELTA LOCATION '{safe_loc_path}'"
        )
        try:
            self._spark.sql(loc_sql)
        except Exception as exc:
            msg = ArchiveError.diagnostic_message(
                "FAILED",
                "operation_failure",
                table=source_table,
                year=year,
                operation="create_external_table",
                error=str(exc),
            )
            raise ArchiveOperationError(
                msg,
                table=source_table,
                year=year,
                operation="create_external_table",
                reason="location_create_failed",
            ) from exc

    def run(self, params):
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

            base_name = self._source_base_name(source_table)
            prefixed_base = f"{table_prefix}{base_name}"
            view_name = (
                f"{target_catalog}.{target_schema}.{prefixed_base}_unified"
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
                self._create_external_table(source_table, year, ext_fq, loc_path)
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
                select_parts = [f"SELECT * FROM {source_table}"]
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
