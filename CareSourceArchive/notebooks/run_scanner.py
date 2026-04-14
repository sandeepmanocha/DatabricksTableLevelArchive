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
from src.scanner import run_scanner
from src.utils import configure_logging, generate_archive_run_id

# COMMAND ----------
dbutils.widgets.text(
    "config_table",
    "",
    "Global settings table (3-level name)",
)
dbutils.widgets.dropdown("force", "false", ["true", "false"], "Force merge from staging")
dbutils.widgets.text("schema_id", "", "Schema template ID (optional)")

config_table = dbutils.widgets.get("config_table").strip()
force_bool = dbutils.widgets.get("force").lower() == "true"
schema_id = dbutils.widgets.get("schema_id").strip() or None

if not config_table:
    raise ValueError("Widget 'config_table' is required (non-empty).")

# COMMAND ----------
archive_run_id = generate_archive_run_id()
configure_logging(archive_run_id)

settings = load_settings(spark, config_table)
summary = run_scanner(spark, settings, force=force_bool, schema_id=schema_id, job_context=_job_context)

scan_run_id = summary["scan_run_id"]
audit_catalog = settings["audit_catalog"]
audit_schema = settings["audit_schema"]

# COMMAND ----------
# F12.2 — scan summary report
rows = [
    ("tables_matched", summary["tables_matched"]),
    ("ambiguous", summary["tables_ambiguous"]),
    ("unmatched", summary["tables_unmatched"]),
    ("excluded", summary["tables_excluded"]),
    ("preserved", summary["tables_preserved"]),
    ("below_size_threshold", summary["tables_below_size_threshold"]),
    ("size_unknown", summary["tables_size_unknown"]),
]
body = "".join(
    f"<tr><th>{html.escape(name)}</th><td>{int(val)}</td></tr>" for name, val in rows
)
displayHTML(
    "<h3>Scanner summary (F12.2)</h3>"
    "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
    + body
    + "</table>"
)

# COMMAND ----------
# F12.3 — audit query hint
hint_sql = (
    f"SELECT * FROM {audit_catalog}.{audit_schema}.scanner_log "
    f"WHERE scan_run_id = '{scan_run_id}'"
)
displayHTML(
    "<h3>Scan run id (F12.3)</h3>"
    f"<p><code>{html.escape(scan_run_id)}</code></p>"
    "<h4>Sample SQL</h4>"
    f"<pre style='background:#f5f5f5;padding:8px'>{html.escape(hint_sql)}</pre>"
)
