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

from src.config import load_settings, load_table_configs, merge_settings
from src.utils import (
    archive_path_from_config,
    archive_state_and_count,
    calculate_eligible_years,
)

# COMMAND ----------
dbutils.widgets.text("config_table", "", "Config table (catalog.schema.global_settings)")

config_table = dbutils.widgets.get("config_table").strip()
if not config_table:
    raise ValueError("config_table is required (non-empty).")

# COMMAND ----------
settings = load_settings(spark, config_table)
table_configs = load_table_configs(spark, settings["table_configs_table"])

missing = []
orphan = []
_cy_row = spark.sql("SELECT YEAR(current_date()) AS y").first()
_current_year = int(_cy_row["y"]) if _cy_row is not None else 0
for tc in table_configs:
    merged = merge_settings(settings, dict(tc))
    years = calculate_eligible_years(
        spark,
        merged["source_table"],
        merged["watermark_column"],
        None,
        None,
        _current_year - int(merged["retention_years"]),
    )
    for y in years:
        state, _ = archive_state_and_count(
            spark, merged["archive_base_path"], merged["source_table"], y,
        )
        entry = {
            "table_id": merged["table_id"],
            "year": y,
            "path": archive_path_from_config(merged, y),
        }
        if state == "MISSING":
            missing.append(entry)
        elif state == "ORPHAN":
            orphan.append(entry)

report = {
    "valid": not missing and not orphan,
    "missing": missing,
    "orphan": orphan,
}

# COMMAND ----------
valid = bool(report["valid"])

_parts = [
    "<h3>Archive path validation (LOG-04)</h3>",
    f"<p><b>Valid</b>: {valid}</p>",
    f"<p><b>Missing or orphan paths</b>: {len(missing) + len(orphan)} "
    f"(missing={len(missing)}, orphan={len(orphan)})</p>",
]

def _render_rows(rows, state_label):
    _parts.append(f"<h4>{state_label}</h4>")
    _parts.append("<table border='1' cellpadding='6' cellspacing='0'>")
    _parts.append("<tr><th>table_id</th><th>year</th><th>path</th></tr>")
    for m in rows:
        _parts.append(
            "<tr>"
            f"<td>{html.escape(str(m.get('table_id', '')))}</td>"
            f"<td>{html.escape(str(m.get('year', '')))}</td>"
            f"<td>{html.escape(str(m.get('path', '')))}</td>"
            "</tr>"
        )
    _parts.append("</table>")

if missing:
    _render_rows(missing, "Missing paths")
if orphan:
    _render_rows(
        orphan,
        "Orphan or unreadable paths (DESCRIBE HISTORY failed — see docs/runbooks/recovery.md)",
    )
if not missing and not orphan:
    _parts.append("<p>No missing or orphan archive paths for eligible years.</p>")

displayHTML("\n".join(_parts))
