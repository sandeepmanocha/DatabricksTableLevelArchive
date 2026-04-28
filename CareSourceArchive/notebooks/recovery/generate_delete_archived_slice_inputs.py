# Databricks notebook source
# Generator for the caresource_delete_archived_slice for_each task. Splits a
# comma-separated `table_ids` widget into one input dict per table_id, validates
# each resolves to exactly one row in the table_configs Delta table, then emits
# the list as a task value (`delete_archived_slice_task_inputs`) for the
# downstream for_each iteration.
#
# Fails fast (before any delete_archived_slice iteration runs) on:
#   - empty / whitespace-only CSV after split
#   - missing or duplicate table_ids in table_configs_table
#   - invalid year (non-integer or outside 1900–2999)
#   - dry_run=false with empty/whitespace reason

# COMMAND ----------
import os, sys

_nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
_bundle_root = os.path.dirname(os.path.dirname(os.path.dirname(_nb_path)))
if not _bundle_root.startswith("/Workspace"):
    _bundle_root = "/Workspace" + _bundle_root
sys.path.insert(0, _bundle_root)

# COMMAND ----------
import json

from src.config import load_table_configs
from src.exceptions import ArchiveConfigError

# COMMAND ----------
dbutils.widgets.text("config_table", "", "Global settings table (3-level)")
dbutils.widgets.text("table_configs_table", "", "Table-configs Delta table (3-level)")
dbutils.widgets.text("table_ids", "", "Comma-separated table_id values")
dbutils.widgets.text("year", "", "Partition year (integer)")
dbutils.widgets.text("reason", "", "Reason (required when dry_run=false)")
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"], "Dry run (default true)")

config_table = dbutils.widgets.get("config_table").strip()
table_configs_table = dbutils.widgets.get("table_configs_table").strip()
table_ids_raw = dbutils.widgets.get("table_ids").strip()
year_raw = dbutils.widgets.get("year").strip()
reason = dbutils.widgets.get("reason").strip()
dry_run = dbutils.widgets.get("dry_run").strip().lower() == "true"

for label, val in (
    ("config_table", config_table),
    ("table_configs_table", table_configs_table),
    ("table_ids", table_ids_raw),
    ("year", year_raw),
):
    if not val:
        raise ArchiveConfigError(msg=f"Widget {label!r} is required (non-empty).")

# Order-preserving dedupe of CSV entries.
seen: set = set()
table_ids: list = []
for raw in table_ids_raw.split(","):
    tid = raw.strip()
    if not tid or tid in seen:
        continue
    seen.add(tid)
    table_ids.append(tid)

if not table_ids:
    raise ArchiveConfigError(
        msg="Widget 'table_ids' contains no non-empty entries after splitting on ','.",
    )

try:
    year_int = int(year_raw)
except ValueError as exc:
    raise ArchiveConfigError(
        msg=f"Widget 'year' must be a valid integer; got {year_raw!r}.",
    ) from exc
if year_int < 1900 or year_int > 2999:
    raise ArchiveConfigError(
        msg=f"Widget 'year' must be between 1900 and 2999 inclusive; got {year_int}.",
    )

if not dry_run and not reason:
    raise ArchiveConfigError(
        msg="Widget 'reason' is required and must be non-whitespace when dry_run is false.",
    )

# COMMAND ----------
# Validate each table_id resolves to exactly one row before launching the for_each.
missing: list = []
duplicate: list = []
for tid in table_ids:
    rows = load_table_configs(spark, table_configs_table, table_id=tid)
    if len(rows) == 0:
        missing.append(tid)
    elif len(rows) > 1:
        duplicate.append(tid)

if missing or duplicate:
    raise ArchiveConfigError(
        msg=(
            f"Invalid table_ids — missing={missing!r}, duplicate={duplicate!r}. "
            f"Each table_id must resolve to exactly one row in {table_configs_table}."
        ),
    )

foreach_inputs = [
    {
        "config_table": config_table,
        "table_configs_table": table_configs_table,
        "table_id": tid,
        "year": str(year_int),
        "reason": reason,
        "dry_run": "true" if dry_run else "false",
    }
    for tid in table_ids
]

dbutils.jobs.taskValues.set(key="delete_archived_slice_task_inputs", value=foreach_inputs)

print(
    json.dumps(
        {
            "table_count": len(foreach_inputs),
            "table_ids": table_ids,
            "year": year_int,
            "dry_run": dry_run,
            "reason_set": bool(reason),
        },
        indent=2,
    )
)
