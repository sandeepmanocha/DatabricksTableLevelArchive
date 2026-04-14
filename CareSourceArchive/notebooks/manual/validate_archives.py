# Databricks notebook source

# COMMAND ----------
import os, sys

_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
_nb_path = _ctx.notebookPath().get()
_bundle_root = os.path.dirname(os.path.dirname(os.path.dirname(_nb_path)))
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

from src.archiver import ArchiveEngine
from src.audit import AuditLogger
from src.config import load_settings, load_table_configs
from src.utils import (
    RunContext,
    generate_archive_run_id,
    load_secrets,
)

# COMMAND ----------
dbutils.widgets.text("config_table", "", "Config table (catalog.schema.global_settings)")

config_table = dbutils.widgets.get("config_table").strip()
if not config_table:
    raise ValueError("config_table is required (non-empty).")

# COMMAND ----------
settings = load_settings(spark, config_table)
table_configs = load_table_configs(spark, settings["table_configs_table"])
secrets = load_secrets(settings, dbutils)
ctx = RunContext(
    settings=settings,
    secrets=secrets,
    job_context=_job_context,
    archive_run_id=generate_archive_run_id(),
)
engine = ArchiveEngine(ctx, AuditLogger(ctx, spark), spark)
report = engine.validate_archives(table_configs, dbutils)

# COMMAND ----------
missing = report.get("missing", [])
valid = bool(report.get("valid"))

_parts = [
    "<h3>Archive path validation (LOG-04)</h3>",
    f"<p><b>Valid</b>: {valid}</p>",
    f"<p><b>Missing paths</b>: {len(missing)}</p>",
]
if missing:
    _parts.append("<table border='1' cellpadding='6' cellspacing='0'>")
    _parts.append("<tr><th>table_id</th><th>year</th><th>path</th></tr>")
    for m in missing:
        _parts.append(
            "<tr>"
            f"<td>{html.escape(str(m.get('table_id', '')))}</td>"
            f"<td>{html.escape(str(m.get('year', '')))}</td>"
            f"<td>{html.escape(str(m.get('path', '')))}</td>"
            "</tr>"
        )
    _parts.append("</table>")
else:
    _parts.append("<p>No missing archive paths for eligible years.</p>")

displayHTML("\n".join(_parts))
