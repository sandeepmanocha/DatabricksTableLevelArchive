# Databricks notebook source

# COMMAND ----------
import os, sys

_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
_nb_path = _ctx.notebookPath().get()
_bundle_root = os.path.dirname(os.path.dirname(_nb_path))
if not _bundle_root.startswith("/Workspace"):
    _bundle_root = "/Workspace" + _bundle_root
sys.path.insert(0, _bundle_root)

def _ctx_val(fn):
    try:
        result = fn()
        if hasattr(result, "get"):
            v = result.get()
        else:
            v = result
        return str(v) if v else None
    except Exception:
        return None
_task_run_id = _ctx_val(_ctx.runId)
_job_run_id = None
if _task_run_id:
    try:
        from databricks.sdk import WorkspaceClient
        _run = WorkspaceClient().jobs.get_run(int(_task_run_id))
        if hasattr(_run, "job_run_id") and _run.job_run_id:
            _job_run_id = str(_run.job_run_id)
    except Exception:
        pass
_job_context = {
    "workspace_id": _ctx_val(_ctx.workspaceId),
    "job_id": _ctx_val(_ctx.jobId),
    "job_run_id": _job_run_id,
    "task_run_id": _task_run_id,
}

# COMMAND ----------
import html

from src.config import load_settings
from src.utils import (
    RunContext,
    archive_folder_exists,
    generate_archive_run_id,
    configure_logging,
)
from src.audit import AuditLogger
from src.rehydrator import RehydrationEngine
from src.exceptions import ArchiveOperationError

# COMMAND ----------
dbutils.widgets.text(
    "config_table",
    "",
    "Global settings table (3-level name)",
)
dbutils.widgets.text("archive_base_path", "", "Archive base path")
dbutils.widgets.text("source_table", "", "Source table (catalog.schema.table)")
dbutils.widgets.text("target_catalog", "", "Target catalog")
dbutils.widgets.text("target_schema", "", "Target schema")
dbutils.widgets.text("years", "", "Years (comma-separated, e.g. 2021,2022,2023)")

config_table = dbutils.widgets.get("config_table").strip()
archive_base_path = dbutils.widgets.get("archive_base_path").strip()
source_table = dbutils.widgets.get("source_table").strip()
target_catalog = dbutils.widgets.get("target_catalog").strip()
target_schema = dbutils.widgets.get("target_schema").strip()
years_raw = dbutils.widgets.get("years").strip()

# COMMAND ----------
for label, val in (
    ("config_table", config_table),
    ("archive_base_path", archive_base_path),
    ("source_table", source_table),
    ("target_catalog", target_catalog),
    ("target_schema", target_schema),
    ("years", years_raw),
):
    if not val:
        raise ValueError(f"Widget {label!r} is required (non-empty).")

years = [int(y.strip()) for y in years_raw.split(",") if y.strip()]
base_name = source_table.split(".")[-1].strip()
available_archive_years = [
    y
    for y in years
    if archive_folder_exists(dbutils, archive_base_path, base_name, y)
]

# COMMAND ----------
archive_run_id = generate_archive_run_id()
configure_logging(archive_run_id)

settings = load_settings(spark, config_table)
ctx = RunContext(
    settings=settings,
    job_context=_job_context,
    archive_run_id=archive_run_id,
)

audit = AuditLogger(ctx, spark)
engine = RehydrationEngine(ctx, audit, spark)

params = {
    "archive_base_path": archive_base_path,
    "source_table": source_table,
    "target_catalog": target_catalog,
    "target_schema": target_schema,
    "years": years,
    "available_archive_years": available_archive_years,
}

# COMMAND ----------
try:
    result = engine.run(params)
except ArchiveOperationError as exc:
    displayHTML(
        "<h3>Rehydration failed (LOG-04)</h3>"
        f"<p style='color:#b00020'>{html.escape(str(exc))}</p>"
    )
    raise

view_safe = html.escape(result["view_name"]) if result["view_name"] else "(no unified view)"
displayHTML(
    "<h3>Rehydration summary (LOG-04)</h3>"
    "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
    f"<tr><th>tables_created</th><td>{int(result['tables_created'])}</td></tr>"
    f"<tr><th>view_name</th><td><code>{view_safe}</code></td></tr>"
    "</table>"
)
