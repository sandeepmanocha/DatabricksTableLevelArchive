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
import json
import logging

from src.audit import AuditLogger
from src.config import load_settings, load_table_configs
from src.exceptions import ArchiveConfigError, ArchiveOperationError
from src.recovery import rollback_archived_slice
from src.utils import RunContext, configure_logging, generate_archive_run_id

# COMMAND ----------
dbutils.widgets.text(
    "config_table",
    "",
    "Global settings table (3-level name)",
)
dbutils.widgets.text(
    "table_configs_table",
    "",
    "Table configs Delta table (FQ name)",
)
dbutils.widgets.text("table_id", "", "table_id in config")
dbutils.widgets.text("year", "", "Partition year (integer)")
dbutils.widgets.text("target_audit_id", "", "Audit row id to roll back to")
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"], "Dry run (default true)")

config_table = dbutils.widgets.get("config_table").strip()
table_configs_table = dbutils.widgets.get("table_configs_table").strip()
table_id = dbutils.widgets.get("table_id").strip()
year_raw = dbutils.widgets.get("year").strip()
target_audit_id = dbutils.widgets.get("target_audit_id").strip()
dry_run = dbutils.widgets.get("dry_run").strip().lower() == "true"

for label, val in (
    ("config_table", config_table),
    ("table_configs_table", table_configs_table),
    ("table_id", table_id),
    ("target_audit_id", target_audit_id),
):
    if not val:
        raise ValueError(f"Widget {label!r} is required (non-empty).")

if not year_raw:
    raise ValueError("Widget 'year' is required (non-empty).")

try:
    year_int = int(year_raw)
except ValueError as exc:
    raise ValueError(
        f"Widget 'year' must be a valid integer; got {year_raw!r}.",
    ) from exc
if year_int < 1900 or year_int > 2999:
    raise ValueError(
        f"Widget 'year' must be between 1900 and 2999 inclusive; got {year_int}.",
    )

# COMMAND ----------
_log = logging.getLogger("caresource_archive.notebook.rollback_archived_slice")
archive_run_id = generate_archive_run_id()
configure_logging(archive_run_id)

settings = load_settings(spark, config_table)
ctx = RunContext(
    settings=settings,
    job_context=_job_context,
    archive_run_id=archive_run_id,
)
audit = AuditLogger(ctx, spark)

rows = load_table_configs(spark, table_configs_table, table_id=table_id)
if len(rows) == 0:
    raise ArchiveConfigError(
        msg=f"No table config found for table_id={table_id!r} (expected exactly one row).",
    )
if len(rows) > 1:
    raise ArchiveConfigError(
        msg=f"Multiple table configs matched table_id={table_id!r} (found {len(rows)} rows; expected exactly one).",
    )
table_config = rows[0]

# COMMAND ----------
try:
    result = rollback_archived_slice(
        spark,
        audit,
        ctx,
        table_config,
        year=year_int,
        target_audit_id=target_audit_id,
        dry_run=dry_run,
    )
except (ArchiveOperationError, ArchiveConfigError) as exc:
    displayHTML(
        "<h3 style='color:#b00020'>Rollback archived slice failed</h3>"
        "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
        f"<tr><th>exception</th><td><code>{html.escape(type(exc).__name__)}</code></td></tr>"
        f"<tr><th>message</th><td>{html.escape(str(exc))}</td></tr>"
        "</table>"
    )
    raise

rows_html = "".join(
    f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(result[k]))}</td></tr>"
    for k in sorted(result.keys())
)
displayHTML(
    "<h3>Rollback archived slice summary</h3>"
    "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
    f"{rows_html}"
    "</table>"
)
_log.info("%s", json.dumps(result, sort_keys=True))
