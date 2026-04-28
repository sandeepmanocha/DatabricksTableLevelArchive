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
import json
import logging

from src.audit import AuditLogger
from src.delete_job import DeleteJob
from src.config import load_settings
from src.utils import (
    RunContext,
    configure_logging,
)

# COMMAND ----------
dbutils.widgets.text("foreach_payload", "{}", "ForEach iteration (JSON)")

_raw = dbutils.widgets.get("foreach_payload")
_payload = json.loads(_raw) if _raw else {}
table_config = _payload["table_config"]
archive_run_id = _payload["archive_run_id"]
config_table = _payload["config_table"]
dry_run = bool(_payload.get("dry_run", True))
years = _payload.get("years") or None

print(f"table_id: {table_config.get('table_id', '?')}")
print(f"archive_run_id: {archive_run_id}")
print(f"dry_run: {dry_run}")
print(f"years: {years}")

# COMMAND ----------
configure_logging(archive_run_id)
_log = logging.getLogger("caresource_archive.notebook.delete_source_after_archive")

settings = load_settings(spark, config_table)
ctx = RunContext(
    settings=settings,
    job_context=_job_context,
    archive_run_id=archive_run_id,
)
audit = AuditLogger(ctx, spark)
engine = DeleteJob(ctx, audit, spark)

# COMMAND ----------
result = engine.run(
    table_config,
    years=years,
    dry_run=dry_run,
)


def _md_summary(report: dict, tid: str, *, dry: bool) -> str:
    lines = [
        "## Delete-Source-After-Archive Summary",
        f"- **table_id**: {tid}",
        f"- **archive_run_id**: {archive_run_id}",
        f"- **dry_run**: {dry}",
        f"- **had_concurrent_failures**: {report.get('had_concurrent_failures', False)}",
        "",
    ]
    tdata = report.get("tables", {}).get(tid, {})
    years_rep = tdata.get("years", {})
    if dry:
        lines.append("| Year | Action | Source | Archive | Reason |")
        lines.append("|------|--------|--------|---------|--------|")
        for yr in sorted(years_rep.keys()):
            s = years_rep[yr]
            lines.append(
                f"| {yr} | {s.get('action', '')} "
                f"| {s.get('source_count', '')} "
                f"| {s.get('archive_count', '')} "
                f"| {s.get('reason', '')} |"
            )
    else:
        lines.append("| Year | Status | Records | Reason |")
        lines.append("|------|--------|---------|--------|")
        for yr in sorted(years_rep.keys()):
            s = years_rep[yr]
            lines.append(
                f"| {yr} | {s.get('status', '')} "
                f"| {s.get('record_count', '')} "
                f"| {s.get('reason', '')} |"
            )
    return "\n".join(lines)


# COMMAND ----------
_tid = table_config.get("table_id", "?")
_log.info(
    "delete_source_after_archive complete for %s dry_run=%s concurrent_failures=%s",
    _tid, dry_run, result.get("had_concurrent_failures", False),
)
print(_md_summary(result, _tid, dry=dry_run))

if not dry_run and result.get("had_concurrent_failures", False):
    raise RuntimeError(
        f"Delete job saw concurrent archive run for {_tid}; "
        f"per-year FAILED rows written. See audit log."
    )
