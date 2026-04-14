# Databricks notebook source

# COMMAND ----------
import os, sys
_nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
_bundle_root = os.path.dirname(os.path.dirname(os.path.dirname(_nb_path)))
if not _bundle_root.startswith("/Workspace"):
    _bundle_root = "/Workspace" + _bundle_root
sys.path.insert(0, _bundle_root)

# COMMAND ----------
import html
import traceback

from src.config import load_schema_templates, load_settings, load_table_configs
from src.exceptions import ArchiveConfigError

# COMMAND ----------
dbutils.widgets.text("config_table", "", "Config table (catalog.schema.global_settings)")

config_table = dbutils.widgets.get("config_table").strip()
if not config_table:
    raise ArchiveConfigError("config_table is required (non-empty).")

# COMMAND ----------
rows = []
settings = None


def _add(ok: bool, step: str, detail: str):
    rows.append({"ok": ok, "step": step, "detail": detail})


try:
    settings = load_settings(spark, config_table)
    _add(True, "global_settings", f"Loaded from {config_table}")
except ArchiveConfigError as exc:
    _add(False, "global_settings", str(exc))
except Exception as exc:
    _add(False, "global_settings", f"{exc!s}\n{traceback.format_exc()}")

if settings is not None:
    try:
        st = load_schema_templates(spark, settings["schema_templates_table"])
        _add(
            True,
            "schema_templates",
            f"{len(st)} row(s) from {settings['schema_templates_table']}",
        )
    except ArchiveConfigError as exc:
        _add(False, "schema_templates", str(exc))
    except Exception as exc:
        _add(False, "schema_templates", f"{exc!s}\n{traceback.format_exc()}")

if settings is not None and len(rows) >= 2 and rows[1]["ok"]:
    try:
        tc = load_table_configs(spark, settings["table_configs_table"])
        _add(
            True,
            "table_configs",
            f"{len(tc)} active row(s) from {settings['table_configs_table']}",
        )
    except ArchiveConfigError as exc:
        _add(False, "table_configs", str(exc))
    except Exception as exc:
        _add(False, "table_configs", f"{exc!s}\n{traceback.format_exc()}")

# COMMAND ----------
_ok_count = sum(1 for r in rows if r["ok"])
_all_ok = _ok_count == len(rows) and len(rows) == 3

_parts = [
    "<h3>Config validation (LOG-04)</h3>",
    f"<p><b>Overall</b>: {'PASS' if _all_ok else 'FAIL'} "
    f"({_ok_count}/{len(rows)} steps ok)</p>",
    "<table border='1' cellpadding='6' cellspacing='0'>",
    "<tr><th>Step</th><th>Status</th><th>Detail</th></tr>",
]
for r in rows:
    status = "OK" if r["ok"] else "FAILED"
    color = "#d4edda" if r["ok"] else "#f8d7da"
    _parts.append(
        "<tr style='background-color:"
        f"{color}'>"
        f"<td>{html.escape(r['step'])}</td>"
        f"<td>{html.escape(status)}</td>"
        f"<td><pre>{html.escape(r['detail'])}</pre></td></tr>"
    )
_parts.append("</table>")

displayHTML("\n".join(_parts))

if not _all_ok:
    raise ArchiveConfigError("One or more config validation steps failed — see HTML summary above.")
