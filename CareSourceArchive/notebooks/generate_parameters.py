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
from datetime import date, datetime

from src.config import load_settings, load_table_configs
from src.exceptions import ArchiveConfigError
from src.utils import generate_archive_run_id

# COMMAND ----------
dbutils.widgets.text("config_table", "", "Config table (catalog.schema.global_settings)")
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"], "Dry run")
dbutils.widgets.text("table_config_filter", "", "Advanced: raw SQL filter (optional)")
dbutils.widgets.text("source_catalog", "", "Source catalog (optional, must be set with source_schema)")
dbutils.widgets.text("source_schema",  "", "Source schema (optional, must be set with source_catalog)")

config_table = dbutils.widgets.get("config_table").strip()
dry_run_widget = dbutils.widgets.get("dry_run")
table_config_filter = dbutils.widgets.get("table_config_filter").strip()
source_catalog = dbutils.widgets.get("source_catalog").strip() or None
source_schema  = dbutils.widgets.get("source_schema").strip()  or None

# COMMAND ----------
if not config_table:
    raise ArchiveConfigError("config_table is required (non-empty full 3-level global_settings table name).")

if bool(source_catalog) != bool(source_schema):
    raise ArchiveConfigError(
        "source_catalog and source_schema must both be provided together (both or neither)."
    )

advanced_filter = table_config_filter.strip() or None


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
dry_run_bool = str(dry_run_widget).lower() == "true"

foreach_inputs = []
for tc in table_configs:
    foreach_inputs.append(
        {
            "table_config": _table_config_for_task(tc),
            "archive_run_id": archive_run_id,
            "config_table": config_table,
            "dry_run": dry_run_bool,
        }
    )

dbutils.jobs.taskValues.set(key="archive_task_inputs", value=foreach_inputs)

print(
    json.dumps(
        {
            "archive_run_id": archive_run_id,
            "table_count": len(foreach_inputs),
            "dry_run": dry_run_bool,
            "filter_expr": advanced_filter,
            "source_catalog": source_catalog,
            "source_schema":  source_schema,
        },
        indent=2,
    )
)
