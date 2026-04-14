import json
import logging

from src.exceptions import ArchiveError, ArchiveOperationError
from src.utils import archive_folder_exists, build_archive_path, create_schema_if_not_exists

LOGGER = logging.getLogger("caresource_archive.rehydrator")


class RehydrationEngine:
    def __init__(self, ctx, audit, spark):
        self._ctx = ctx
        self._audit = audit
        self._spark = spark

    def _source_base_name(self, source_table):
        parts = source_table.strip().split(".")
        return parts[-1] if parts else source_table.strip()

    def _create_external_table(self, fq_table, loc_path):
        loc_sql = (
            f"CREATE TABLE IF NOT EXISTS {fq_table} USING DELTA LOCATION '{loc_path}'"
        )
        try:
            self._spark.sql(loc_sql)
        except Exception:
            clone_sql = (
                f"CREATE TABLE IF NOT EXISTS {fq_table} "
                f"SHALLOW CLONE delta.`{loc_path}`"
            )
            self._spark.sql(clone_sql)

    def run(self, params):
        archive_base_path = params["archive_base_path"]
        source_table = params["source_table"]
        target_catalog = params["target_catalog"]
        target_schema = params["target_schema"]
        years = params["years"]
        dbutils = params["dbutils"]

        base_name = self._source_base_name(source_table)
        view_name = f"{target_catalog}.{target_schema}.{base_name}_unified"
        tables_created = 0
        created_years = []

        try:
            create_schema_if_not_exists(self._spark, target_catalog, target_schema)
            self._audit.ensure_rehydration_audit_table()

            for year in years:
                if not archive_folder_exists(
                    dbutils, archive_base_path, base_name, year
                ):
                    LOGGER.warning(
                        "archive folder missing for year %s under %s, skipping",
                        year,
                        archive_base_path,
                    )
                    continue
                loc_path = build_archive_path(archive_base_path, base_name, year)
                ext_fq = f"{target_catalog}.{target_schema}.{base_name}_year_{year}"
                self._create_external_table(ext_fq, loc_path)
                tables_created += 1
                created_years.append(year)

            select_parts = [f"SELECT * FROM {source_table}"]
            for y in created_years:
                ext_fq = f"{target_catalog}.{target_schema}.{base_name}_year_{y}"
                select_parts.append(f"SELECT * FROM {ext_fq}")
            union_body = " UNION ALL ".join(select_parts)
            view_sql = f"CREATE OR REPLACE VIEW {view_name} AS {union_body}"
            self._spark.sql(view_sql)

            self._audit.log_rehydrate(
                archive_path=archive_base_path,
                source=source_table,
                target_catalog=target_catalog,
                target_schema=target_schema,
                years=json.dumps(years),
                tables_created=tables_created,
                status="COMPLETED",
                error_message=None,
            )
            return {"tables_created": tables_created, "view_name": view_name}
        except Exception as exc:
            self._audit.log_rehydrate(
                archive_path=archive_base_path,
                source=source_table,
                target_catalog=target_catalog,
                target_schema=target_schema,
                years=json.dumps(years),
                tables_created=tables_created,
                status="FAILED",
                error_message=str(exc),
            )
            msg = ArchiveError.diagnostic_message(
                "FAILED", "operation_failure",
                table=source_table, year="all", operation="rehydrate", error=str(exc),
            )
            raise ArchiveOperationError(
                msg, table=source_table, year="all",
                operation="rehydrate", reason="operation_failure",
            ) from exc
