# Databricks notebook source

# COMMAND ----------
import os, sys
_nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
_bundle_root = os.path.dirname(os.path.dirname(_nb_path))
if not _bundle_root.startswith("/Workspace"):
    _bundle_root = "/Workspace" + _bundle_root
sys.path.insert(0, _bundle_root)

# COMMAND ----------
import json
from decimal import Decimal
from datetime import date, datetime, timezone

from src.config import load_settings, load_table_configs, merge_settings
from src.conditions import build_exclusion_clause
from src.exceptions import ArchiveConfigError
from src.utils import calculate_eligible_years, generate_archive_run_id

# COMMAND ----------
dbutils.widgets.text("config_table", "", "Config table (catalog.schema.global_settings)")
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"], "Dry run")
dbutils.widgets.text("table_config_filter", "", "Advanced: raw SQL filter (optional)")
dbutils.widgets.text("source_catalog", "", "Source catalog (optional, must be set with source_schema)")
dbutils.widgets.text("source_schema",  "", "Source schema (optional, must be set with source_catalog)")
dbutils.widgets.dropdown("job_mode", "archive", ["archive", "delete"], "Job mode")
dbutils.widgets.text("years", "", "Delete-mode years (CSV; empty=all eligible)")

config_table = dbutils.widgets.get("config_table").strip()
dry_run_widget = dbutils.widgets.get("dry_run")
table_config_filter = dbutils.widgets.get("table_config_filter").strip()
source_catalog = dbutils.widgets.get("source_catalog").strip() or None
source_schema  = dbutils.widgets.get("source_schema").strip()  or None
job_mode = (dbutils.widgets.get("job_mode") or "archive").strip().lower()
years_widget = dbutils.widgets.get("years").strip()

# COMMAND ----------
if not config_table:
    raise ArchiveConfigError("config_table is required (non-empty full 3-level global_settings table name).")

if bool(source_catalog) != bool(source_schema):
    raise ArchiveConfigError(
        "source_catalog and source_schema must both be provided together (both or neither)."
    )

if job_mode not in ("archive", "delete"):
    raise ArchiveConfigError(
        f"Invalid job_mode={job_mode!r}; must be 'archive' or 'delete'."
    )

advanced_filter = table_config_filter.strip() or None
dry_run_bool = str(dry_run_widget).lower() == "true"


def _parse_years(raw: str) -> list:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            raise ArchiveConfigError(
                f"years widget contains non-integer value {p!r}; use a CSV of years."
            )
    return sorted(set(out))


scope_years = _parse_years(years_widget)

if job_mode == "delete" and not dry_run_bool:
    if not (source_catalog and source_schema) and not advanced_filter and not scope_years:
        raise ArchiveConfigError(
            "Delete job refused: at least one filter widget is required for a live run. "
            "Set dry_run=true to explore."
        )


def _json_safe(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "asDict"):
        return {k: _json_safe(v) for k, v in value.asDict().items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return str(value)


def _table_config_for_task(row):
    return {k: _json_safe(v) for k, v in row.items()}


def _resolve_delete_years(spark_session, settings_dict, tc_dict, explicit_years):
    if explicit_years:
        return list(explicit_years)
    merged = merge_settings(settings_dict, dict(tc_dict))
    conditions_raw = merged.get("exclusion_conditions")
    if isinstance(conditions_raw, str) and conditions_raw.strip():
        try:
            conditions = json.loads(conditions_raw)
        except json.JSONDecodeError:
            conditions = []
    elif isinstance(conditions_raw, list):
        conditions = conditions_raw
    else:
        conditions = []
    exclusion_clause = build_exclusion_clause(
        conditions,
        merged["source_catalog"],
        merged["source_schema"],
        "src",
    )
    source_table = (
        f"{merged['source_catalog']}.{merged['source_schema']}."
        f"{merged['source_table']}"
    )
    current_year = datetime.now(timezone.utc).year
    retention_boundary = current_year - int(merged["retention_years"])
    return calculate_eligible_years(
        spark_session,
        source_table,
        merged["watermark_column"],
        exclusion_clause,
        configured_years=None,
        retention_year_boundary=retention_boundary,
    )


# COMMAND ----------
# ArchiveConfigError propagates — stops entire job (ERR-02)
settings = load_settings(spark, config_table)
table_configs = load_table_configs(
    spark,
    settings["table_configs_table"],
    source_catalog=source_catalog,
    source_schema=source_schema,
    filter_expr=advanced_filter,
)
filter_supplied = bool(
    (source_catalog and source_schema) or advanced_filter
)
if not table_configs and filter_supplied:
    raise ArchiveConfigError(
        "No active table_configs matched the supplied filters "
        f"(source_catalog={source_catalog!r}, source_schema={source_schema!r}, "
        f"filter_expr={advanced_filter!r})"
    )
archive_run_id = generate_archive_run_id()

foreach_inputs = []
for tc in table_configs:
    tc_dict = _table_config_for_task(tc)
    if job_mode == "archive":
        foreach_inputs.append(
            {
                "table_config": tc_dict,
                "archive_run_id": archive_run_id,
                "config_table": config_table,
                "dry_run": dry_run_bool,
            }
        )
    else:
        resolved_years = _resolve_delete_years(
            spark, settings, tc_dict, scope_years,
        )
        foreach_inputs.append(
            {
                "table_config": tc_dict,
                "archive_run_id": archive_run_id,
                "config_table": config_table,
                "dry_run": dry_run_bool,
                "years": list(resolved_years),
            }
        )

if job_mode == "archive":
    dbutils.jobs.taskValues.set(key="archive_task_inputs", value=foreach_inputs)
else:
    dbutils.jobs.taskValues.set(key="delete_source_after_archive_task_inputs", value=foreach_inputs)

print(
    json.dumps(
        {
            "archive_run_id": archive_run_id,
            "job_mode": job_mode,
            "table_count": len(foreach_inputs),
            "dry_run": dry_run_bool,
            "filter_expr": advanced_filter,
            "source_catalog": source_catalog,
            "source_schema":  source_schema,
            "years": scope_years,
        },
        indent=2,
    )
)
