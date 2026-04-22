"""
Schema scanner — discovers archivable tables.

Walks a source schema using the configured templates, matches each table's
watermark column, filters out tables that are too small to be worth
archiving, and publishes the resulting per-table configuration to the
`table_configs` Delta table that drives the archiver.
"""

import json
import logging
import re
import uuid

from src import config
from src.exceptions import ArchiveConfigError
from src.utils import (
    build_full_table_name,
    build_multi_insert_values_sql,
    collect_column,
    ensure_table_with_setup_message,
    generate_archive_run_id,
    row_to_dict,
    row_value,
    sql_bool,
    sql_expr,
    sql_int_or_null,
    sql_quote,
    sql_str_or_null,
    validate_identifier,
)

LOGGER = logging.getLogger("caresource_archive.scanner")

_GB = 1024**3


def scan_schema(spark, template) -> list[str]:
    """
    Description: Lists table names in the template's source catalog and schema.
    Parameters: spark: Spark session; template: dict with source_catalog and source_schema
    Return: List of table name strings
    """
    cat = template["source_catalog"]
    sch = template["source_schema"]
    q = f"SHOW TABLES IN {cat}.{sch}"
    return [str(n) for n in collect_column(spark, q, "tableName") if n is not None]


def match_watermark_column(table_columns, patterns) -> tuple[str | None, str | None, list[str], str]:
    """
    Description: Resolves a watermark column using exact names or regex patterns in order.
    Parameters: table_columns: list of column names; patterns: watermark patterns to try
    Return: Tuple of matched column or None, pattern, matched columns list, status string
    """
    for pattern in patterns:
        exact = [c for c in table_columns if c == pattern]
        if len(exact) == 1:
            return exact[0], pattern, list(exact), "matched"
        if len(exact) > 1:
            return None, pattern, exact, "ambiguous"
        regex_hits = []
        for col in table_columns:
            try:
                if re.fullmatch(pattern, col):
                    regex_hits.append(col)
            except re.error:
                continue
        if len(regex_hits) == 1:
            return regex_hits[0], pattern, list(regex_hits), "matched"
        if len(regex_hits) > 1:
            return None, pattern, regex_hits, "ambiguous"
    return None, None, [], "unmatched"


def _scanner_table_config(template, table_name, **overrides) -> dict:
    """
    Description: Builds a base per-table scanner config dict and applies optional overrides.
    Parameters: template: schema template dict; table_name: source table name; overrides: optional field overrides
    Return: Dict table configuration for staging or merge
    """
    cat = template["source_catalog"]
    sch = template["source_schema"]
    base = {
        "table_id": build_full_table_name(cat, sch, table_name),
        "source_catalog": cat,
        "source_schema": sch,
        "source_table": table_name,
        "watermark_column": None,
        "archive_base_path": template["archive_base_path"],
        "retention_years": template["default_retention_years"],
        "delete_after_archive": template["delete_after_archive"],
        "exclusion_conditions": None,
        "is_active": False,
        "reason": None,
        "modified_by": "scanner",
    }
    base.update(overrides)
    return base


def generate_table_config(template, table_name, matched_column) -> dict:
    """
    Description: Builds an active table config with the chosen watermark column.
    Parameters: template: schema template dict; table_name: source table name; matched_column: resolved watermark column
    Return: Dict table configuration marked active
    """
    return _scanner_table_config(
        template,
        table_name,
        watermark_column=matched_column,
        is_active=True,
    )


def flag_unmatched(template, table_name) -> dict:
    """
    Description: Builds an inactive table config when no watermark pattern matches any column.
    Parameters: template: schema template dict; table_name: source table name
    Return: Dict table configuration with unmatched reason
    """
    return _scanner_table_config(
        template,
        table_name,
        reason="no date column matched — no pattern matched any column",
    )


def flag_ambiguous(template, table_name, pattern, matched_columns) -> dict:
    """
    Description: Builds an inactive table config when a pattern matches multiple columns.
    Parameters: template: schema template dict; table_name: source table name; pattern: ambiguous pattern; matched_columns: columns that matched
    Return: Dict table configuration with ambiguity reason
    """
    joined = ", ".join(matched_columns)
    return _scanner_table_config(
        template,
        table_name,
        reason=f"ambiguous date column: pattern '{pattern}' matched [{joined}]",
    )


def get_table_size_gb(spark, catalog, schema, table) -> float | None:
    """
    Description: Returns physical table size in GB from DESCRIBE DETAIL, or None if unknown or a view.
    Parameters: spark: Spark session; catalog: catalog name; schema: schema name; table: table name
    Return: Float size in GB, or None
    """
    fq = build_full_table_name(catalog, schema, table)
    try:
        df = spark.sql(f"DESCRIBE DETAIL {fq}")
    except Exception as exc:
        if "EXPECT_TABLE_NOT_VIEW" in str(exc):
            LOGGER.info("Skipping DESCRIBE DETAIL for view %s", fq)
            return None
        raise
    rows = df.collect()
    if not rows:
        return None
    b = row_value(rows[0], "sizeInBytes")
    if b is None:
        return None
    try:
        return float(b) / _GB
    except (TypeError, ValueError):
        return None


def check_size_threshold(table_size_gb, min_table_size_gb) -> tuple[bool, str | None]:
    """
    Description: Returns whether the table meets the minimum size threshold for archiving.
    Parameters: table_size_gb: size in GB or None; min_table_size_gb: minimum required GB (0 disables check)
    Return: Tuple of pass flag and failure reason string or None
    """
    if min_table_size_gb == 0:
        return True, None
    if table_size_gb is None:
        return (
            False,
            "table size unknown — DESCRIBE DETAIL returned no size information",
        )
    if table_size_gb < min_table_size_gb:
        return (
            False,
            f"table size {table_size_gb:.1f} GB is below minimum threshold of {min_table_size_gb:.1f} GB",
        )
    return True, None


def write_staging(spark, configs, staging_table, scan_run_id) -> None:
    """
    Description: Inserts scan result configs into the staging Delta table with the scan run id.
    Parameters: spark: Spark session; configs: list of table config dicts; staging_table: staging table name; scan_run_id: scan identifier
    Return: None
    """
    if not configs:
        return
    columns = [
        "table_id",
        "source_catalog",
        "source_schema",
        "source_table",
        "watermark_column",
        "retention_years",
        "archive_base_path",
        "delete_after_archive",
        "is_active",
        "reason",
        "exclusion_conditions",
        "scan_run_id",
        "scan_timestamp",
    ]
    rows = []
    for c in configs:
        dc = c.get("watermark_column")
        if dc is not None:
            validate_identifier(
                dc, field="watermark_column", table_id=c.get("table_id")
            )
        dc_sql = sql_quote(dc) if dc is not None else sql_expr("CAST(NULL AS STRING)")
        ry = c.get("retention_years")
        ry_sql = sql_int_or_null(ry)
        reason_sql = sql_str_or_null(c.get("reason"))
        rows.append(
            [
                sql_quote(c["table_id"]),
                sql_quote(c["source_catalog"]),
                sql_quote(c["source_schema"]),
                sql_quote(c["source_table"]),
                dc_sql,
                ry_sql,
                sql_quote(c["archive_base_path"]),
                sql_bool(c["delete_after_archive"]),
                sql_bool(c["is_active"]),
                reason_sql,
                sql_expr("NULL"),
                sql_quote(scan_run_id),
                sql_expr("current_timestamp()"),
            ]
        )
    sql = build_multi_insert_values_sql(staging_table, columns, rows)
    spark.sql(sql)


def merge_staging_to_final(
    spark, staging_table, table_configs_table, scan_run_id, force=False
) -> None:
    """
    Description: MERGEs staging rows into table_configs and deactivates scanner rows missing from this scan.
    Parameters: spark: Spark session; staging_table: staging table name; table_configs_table: destination table; scan_run_id: scan identifier; force: update even when modified_by is not scanner
    Return: None
    """
    force_cond = "TRUE" if force else "FALSE"
    qid = sql_quote(scan_run_id)
    merge_sql = f"""
MERGE INTO {table_configs_table} AS target
USING (SELECT * FROM {staging_table} WHERE scan_run_id = {qid}) AS source
ON target.table_id = source.table_id
WHEN MATCHED AND (target.modified_by = 'scanner' OR {force_cond}) THEN
  UPDATE SET
    target.watermark_column = source.watermark_column,
    target.retention_years = source.retention_years,
    target.archive_base_path = source.archive_base_path,
    target.delete_after_archive = source.delete_after_archive,
    target.is_active = source.is_active,
    target.reason = source.reason,
    target.exclusion_conditions = source.exclusion_conditions,
    target.modified_at = current_timestamp(),
    target.change_reason = CONCAT('updated by scan ', {qid}),
    target.scan_run_id = source.scan_run_id
WHEN NOT MATCHED THEN
  INSERT (table_id, source_catalog, source_schema, source_table, watermark_column, retention_years,
          archive_base_path, delete_after_archive, is_active, reason, exclusion_conditions,
          modified_by, modified_at, change_reason, scan_run_id)
  VALUES (source.table_id, source.source_catalog, source.source_schema, source.source_table,
          source.watermark_column, source.retention_years, source.archive_base_path,
          source.delete_after_archive, source.is_active, source.reason, source.exclusion_conditions,
          'scanner', current_timestamp(), CONCAT('added by scan ', {qid}),
          source.scan_run_id)
""".strip()
    spark.sql(merge_sql)
    drop_sql = f"""
UPDATE {table_configs_table}
SET is_active = false,
    reason = CONCAT('dropped: not found in scan ', {qid}, ' at ', CAST(current_timestamp() AS STRING)),
    modified_at = current_timestamp(),
    change_reason = CONCAT('marked inactive by scan ', {qid}),
    scan_run_id = {qid}
WHERE modified_by = 'scanner'
  AND table_id NOT IN (SELECT table_id FROM {staging_table} WHERE scan_run_id = {qid})
""".strip()
    spark.sql(drop_sql)


def _validate_volume_is_external(spark, archive_base_path) -> None:
    """
    Description: Verifies a /Volumes path refers to an EXTERNAL Unity Catalog volume.
    Parameters: spark: Spark session; archive_base_path: volume path under /Volumes
    Return: None
    """
    parts = archive_base_path.rstrip("/").split("/")
    if len(parts) < 5:
        raise ArchiveConfigError(
            msg=(
                f"archive_base_path '{archive_base_path}' is not a valid volume path. "
                "Expected format: /Volumes/<catalog>/<schema>/<volume>[/subpath]"
            )
        )
    catalog, schema, volume = parts[2], parts[3], parts[4]
    fq = f"{catalog}.{schema}.{volume}"
    try:
        df = spark.sql(f"DESCRIBE VOLUME {fq}")
    except Exception as exc:
        raise ArchiveConfigError(
            msg=f"Cannot describe volume '{fq}': {exc}"
        ) from exc
    rows = df.collect()
    if not rows:
        raise ArchiveConfigError(
            msg=f"DESCRIBE VOLUME returned no rows for '{fq}'"
        )
    r = rows[0]
    vol_type = str(row_value(r, "volume_type") or row_value(r, "type") or "").upper()
    if vol_type != "EXTERNAL":
        raise ArchiveConfigError(
            msg=(
                f"Volume '{fq}' is {vol_type or 'UNKNOWN'}, not EXTERNAL. "
                "Archive paths must use an external volume backed by cloud storage."
            )
        )


def validate_archive_path(spark, archive_base_path) -> None:
    """
    Description: Ensures the archive path is a valid external volume or external location prefix.
    Parameters: spark: Spark session; archive_base_path: archive base path to validate
    Return: None
    """
    normalized = archive_base_path.rstrip("/")
    if normalized.startswith("/Volumes/"):
        _validate_volume_is_external(spark, archive_base_path)
        return
    df = spark.sql("SHOW EXTERNAL LOCATIONS")
    for row in df.collect():
        d = row_to_dict(row)
        url = d.get("url")
        if url is None:
            try:
                url = row["url"]
            except Exception:
                url = None
        if url is None:
            continue
        loc_url = str(url).rstrip("/")
        if normalized == loc_url or normalized.startswith(loc_url + "/"):
            return
    raise ArchiveConfigError(
        msg=(
            f"archive_base_path '{archive_base_path}' is not covered by any Unity Catalog "
            "external location. Register an external location that covers this path before "
            "running the scanner."
        )
    )


def _scanner_log_fq(settings) -> str:
    """
    Description: Returns the fully qualified scanner_log table name from audit settings.
    Parameters: settings: dict with audit_catalog and audit_schema
    Return: Three-part table name string
    """
    return build_full_table_name(settings["audit_catalog"], settings["audit_schema"], "scanner_log")


def ensure_scanner_log_table(spark, settings) -> None:
    """
    Description: Creates the scanner_log Delta table if it does not already exist.
    Parameters: spark: Spark session; settings: dict containing audit catalog and schema
    Return: None
    """
    ensure_table_with_setup_message(spark, _scanner_log_fq(settings), label="Scanner log table")


def write_scanner_log(spark, settings, scan_run_id, table_results, job_context=None) -> None:
    """
    Description: Inserts per-table scan audit rows into the scanner_log table.
    Parameters: spark: Spark session; settings: settings dict; scan_run_id: scan identifier; table_results: list of log row dicts; job_context: optional job metadata dict or None
    Return: None
    """
    if not table_results:
        return
    jc = job_context or {}
    fq = _scanner_log_fq(settings)
    columns = [
        "log_id",
        "scan_run_id",
        "table_id",
        "source_catalog",
        "source_schema",
        "source_table",
        "match_status",
        "matched_column",
        "matched_pattern",
        "all_matched_columns",
        "ambiguity_detail",
        "table_size_gb",
        "size_check_passed",
        "is_active",
        "inactive_reason",
        "merge_action",
        "workspace_id",
        "scanned_by",
        "created_at",
    ]
    rows = []
    for tr in table_results:
        all_matched = tr.get("all_matched_columns") or []
        amb = tr.get("ambiguity_detail")
        ts = tr.get("table_size_gb")
        ts_sql = "NULL" if ts is None else str(float(ts))
        rows.append(
            [
                sql_quote(str(tr["log_id"])),
                sql_quote(scan_run_id),
                sql_quote(tr["table_id"]),
                sql_quote(tr["source_catalog"]),
                sql_quote(tr["source_schema"]),
                sql_quote(tr["source_table"]),
                sql_quote(tr["match_status"]),
                sql_str_or_null(tr.get("matched_column")),
                sql_str_or_null(tr.get("matched_pattern")),
                sql_quote(json.dumps(all_matched)),
                sql_str_or_null(amb),
                ts_sql,
                sql_bool(tr.get("size_check_passed", False)),
                sql_bool(tr["is_active"]),
                sql_str_or_null(tr.get("inactive_reason")),
                sql_str_or_null(tr.get("merge_action")),
                sql_str_or_null(jc.get("workspace_id")),
                sql_expr("current_user()"),
                sql_expr("current_timestamp()"),
            ]
        )
    sql = build_multi_insert_values_sql(fq, columns, rows)
    spark.sql(sql)


def _staging_table_from_configs(table_configs_table) -> str:
    """
    Description: Derives the staging table name from the table_configs table name.
    Parameters: table_configs_table: fully qualified table_configs table name
    Return: Fully qualified staging table name string
    """
    parts = table_configs_table.split(".")
    if parts and parts[-1] == "table_configs":
        parts[-1] = "table_configs_staging"
        return ".".join(parts)
    return f"{table_configs_table}_staging"


def _list_table_columns(spark, catalog, schema, table) -> list[str]:
    """
    Description: Lists column names for a table from information_schema ordered by position.
    Parameters: spark: Spark session; catalog: catalog name; schema: schema name; table: table name
    Return: List of column name strings
    """
    q = (
        f"SELECT column_name FROM {catalog}.information_schema.columns "
        f"WHERE table_catalog = {sql_quote(catalog)} "
        f"AND table_schema = {sql_quote(schema)} "
        f"AND table_name = {sql_quote(table)} "
        f"ORDER BY ordinal_position"
    )
    return [str(n) for n in collect_column(spark, q, "column_name") if n is not None]


def _make_log_entry(tid, cat, sch, table_name, match_status, **overrides) -> dict:
    """
    Description: Builds a scanner_log row dict with defaults and optional field overrides.
    Parameters: tid: fully qualified table id; cat: source catalog; sch: source schema; table_name: table name; match_status: match outcome; overrides: optional log field overrides
    Return: Dict log row for scanner_log
    """
    entry = {
        "log_id": str(uuid.uuid4()),
        "table_id": tid,
        "source_catalog": cat,
        "source_schema": sch,
        "source_table": table_name,
        "match_status": match_status,
        "matched_column": None,
        "matched_pattern": None,
        "all_matched_columns": [],
        "ambiguity_detail": None,
        "table_size_gb": None,
        "size_check_passed": False,
        "is_active": False,
        "inactive_reason": None,
        "merge_action": None,
    }
    entry.update(overrides)
    return entry


def _empty_scan_summary() -> dict:
    """
    Description: Returns a new scan summary dict with all counters set to zero.
    Parameters: none
    Return: Dict of summary counter fields
    """
    return {
        "tables_matched": 0,
        "tables_ambiguous": 0,
        "tables_unmatched": 0,
        "tables_excluded": 0,
        "tables_below_size_threshold": 0,
        "tables_size_unknown": 0,
        "tables_preserved": 0,
    }


def run_scanner(spark, settings, force=False, schema_id=None, job_context=None) -> dict:
    """
    Description: Runs the full schema scan, staging merge, and scanner_log write for configured templates.
    Parameters: spark: Spark session; settings: application settings dict; force: merge force flag; schema_id: optional template filter; job_context: optional job metadata dict or None
    Return: Dict scan summary including scan_run_id and counters
    """
    scan_run_id = generate_archive_run_id()
    ensure_scanner_log_table(spark, settings)
    templates = config.load_schema_templates(
        spark, settings["schema_templates_table"], schema_id=schema_id
    )
    if not templates:
        s = _empty_scan_summary()
        s["scan_run_id"] = scan_run_id
        return s
    staging_table = _staging_table_from_configs(settings["table_configs_table"])
    table_configs_table = settings["table_configs_table"]
    existing = {
        r["table_id"]: r
        for r in config.load_table_configs(
            spark, table_configs_table, active_only=False
        )
    }
    configs = []
    table_results = []
    summary = _empty_scan_summary()
    for template in templates:
        cat = template["source_catalog"]
        sch = template["source_schema"]
        patterns = list(template.get("watermark_column_patterns") or [])
        validate_archive_path(spark, template["archive_base_path"])
        exclude = set(template.get("exclude_tables") or [])
        min_gb = float(template["min_table_size_gb"])
        for table_name in scan_schema(spark, template):
            tid = build_full_table_name(cat, sch, table_name)
            if table_name in exclude:
                summary["tables_excluded"] += 1
                table_results.append(_make_log_entry(
                    tid, cat, sch, table_name, "excluded",
                    inactive_reason="excluded by template exclude_tables",
                ))
                continue
            cols = _list_table_columns(spark, cat, sch, table_name)
            matched_col, pattern, all_matched, status = match_watermark_column(cols, patterns)
            size_gb = get_table_size_gb(spark, cat, sch, table_name)
            if status == "matched":
                cfg = generate_table_config(template, table_name, matched_col)
                active, sz_reason = check_size_threshold(size_gb, min_gb)
                if not active:
                    cfg["is_active"] = False
                    cfg["reason"] = sz_reason
                    if size_gb is None and min_gb > 0:
                        summary["tables_size_unknown"] += 1
                    elif size_gb is not None and min_gb > 0:
                        summary["tables_below_size_threshold"] += 1
                if cfg["is_active"]:
                    summary["tables_matched"] += 1
                inactive = cfg.get("reason")
                table_results.append(_make_log_entry(
                    tid, cat, sch, table_name, "matched",
                    matched_column=matched_col,
                    matched_pattern=pattern,
                    all_matched_columns=list(all_matched),
                    table_size_gb=size_gb,
                    size_check_passed=active,
                    is_active=cfg["is_active"],
                    inactive_reason=inactive if not cfg["is_active"] else None,
                ))
                configs.append(cfg)
            elif status == "ambiguous":
                summary["tables_ambiguous"] += 1
                cfg = flag_ambiguous(template, table_name, pattern, all_matched)
                table_results.append(_make_log_entry(
                    tid, cat, sch, table_name, "ambiguous",
                    matched_pattern=pattern,
                    all_matched_columns=list(all_matched),
                    ambiguity_detail=f"pattern '{pattern}' matched [{', '.join(all_matched)}]",
                    table_size_gb=size_gb,
                    inactive_reason=cfg["reason"],
                ))
                configs.append(cfg)
            else:
                summary["tables_unmatched"] += 1
                cfg = flag_unmatched(template, table_name)
                table_results.append(_make_log_entry(
                    tid, cat, sch, table_name, "unmatched",
                    table_size_gb=size_gb,
                    inactive_reason=cfg["reason"],
                ))
                configs.append(cfg)
    staging_ids = {c["table_id"] for c in configs}
    summary["tables_preserved"] = sum(
        1
        for tid in staging_ids
        if tid in existing and existing[tid].get("modified_by") != "scanner"
    )
    for tr in table_results:
        if tr["match_status"] == "excluded":
            continue
        tid = tr["table_id"]
        if tid not in existing:
            tr["merge_action"] = "added"
        elif existing[tid].get("modified_by") != "scanner":
            tr["merge_action"] = "preserved"
        else:
            tr["merge_action"] = "updated"
    write_staging(spark, configs, staging_table, scan_run_id)
    merge_staging_to_final(
        spark, staging_table, table_configs_table, scan_run_id, force=force
    )
    write_scanner_log(spark, settings, scan_run_id, table_results, job_context=job_context)
    summary["scan_run_id"] = scan_run_id
    return summary
