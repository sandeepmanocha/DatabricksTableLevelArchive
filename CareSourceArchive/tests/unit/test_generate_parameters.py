import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import ArchiveConfigError

_NB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "notebooks",
    "generate_parameters.py",
)


def _load_nb_source() -> str:
    with open(_NB_PATH, encoding="utf-8") as fh:
        src = fh.read()
    src = re.sub(r"(?m)^# Databricks notebook source.*?\n", "", src)
    src = re.sub(r"(?m)^# COMMAND ----------\s*\n", "", src)
    src = src.replace(
        "_nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()",
        "_nb_path = os.path.join(_bundle_root_override, 'notebooks', 'generate_parameters.py')",
    )
    src = src.replace(
        "_bundle_root = os.path.dirname(os.path.dirname(_nb_path))",
        "_bundle_root = _bundle_root_override",
    )
    return src


def _make_stub_dbutils(widget_values):
    dbutils = MagicMock()
    dbutils.widgets.get.side_effect = lambda k: widget_values.get(k, "")
    dbutils.jobs.taskValues.set = MagicMock()
    return dbutils


def _make_stub_spark():
    return MagicMock()


def _run_nb(widget_values, *, table_configs=None, settings=None, eligible_years=None):
    settings = settings or {
        "audit_catalog": "a",
        "audit_schema": "b",
        "table_configs_table": "a.b.table_configs",
        "default_retention_years": 5,
        "archive_base_path_prefix": "abfss://stor/",
    }
    table_configs = table_configs or []
    src = _load_nb_source()
    stub_dbutils = _make_stub_dbutils(widget_values)
    stub_spark = _make_stub_spark()
    bundle_root = os.path.dirname(os.path.dirname(_NB_PATH))
    exec_globals = {
        "__name__": "__main__",
        "_bundle_root_override": bundle_root,
        "dbutils": stub_dbutils,
        "spark": stub_spark,
    }
    patches = [
        patch("src.config.load_settings", return_value=settings),
        patch("src.config.load_table_configs", return_value=table_configs),
        patch(
            "src.utils.generate_archive_run_id",
            return_value="00000000-0000-0000-0000-000000000000",
        ),
    ]
    if eligible_years is not None:
        patches.append(
            patch("src.utils.calculate_eligible_years", return_value=list(eligible_years))
        )
    for p in patches:
        p.start()
    try:
        exec(compile(src, _NB_PATH, "exec"), exec_globals)
    finally:
        for p in patches:
            p.stop()
    return stub_dbutils, exec_globals


def _valid_tc(**overrides):
    base = {
        "table_id": "h.c.m",
        "source_catalog": "src_cat",
        "source_schema": "src_sch",
        "source_table": "claims",
        "watermark_column": "claim_date",
        "archive_base_path": "abfss://stor/archive",
        "retention_years": 5,
        "delete_after_archive": True,
        "exclusion_conditions": "[]",
        "is_active": True,
    }
    base.update(overrides)
    return base


def test_generate_parameters_scope_guard_refuses_live_delete_with_empty_filters():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "false",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "delete",
        "years": "",
    }
    with pytest.raises(ArchiveConfigError, match="Delete job refused"):
        _run_nb(widgets, table_configs=[_valid_tc()])


def test_generate_parameters_scope_guard_allows_dry_run_delete_with_empty_filters():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "true",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "delete",
        "years": "",
    }
    stub_dbutils, _ = _run_nb(
        widgets, table_configs=[_valid_tc()], eligible_years=[2019, 2020],
    )
    stub_dbutils.jobs.taskValues.set.assert_called_once()
    kwargs = stub_dbutils.jobs.taskValues.set.call_args.kwargs
    assert kwargs["key"] == "delete_source_after_archive_task_inputs"
    inputs = kwargs["value"]
    assert len(inputs) == 1
    assert inputs[0]["dry_run"] is True
    assert inputs[0]["years"] == [2019, 2020]


def test_generate_parameters_scope_guard_allows_live_delete_when_filter_supplied():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "false",
        "table_config_filter": "",
        "source_catalog": "src_cat",
        "source_schema": "src_sch",
        "job_mode": "delete",
        "years": "",
    }
    stub_dbutils, _ = _run_nb(
        widgets, table_configs=[_valid_tc()], eligible_years=[2019],
    )
    stub_dbutils.jobs.taskValues.set.assert_called_once()
    kwargs = stub_dbutils.jobs.taskValues.set.call_args.kwargs
    assert kwargs["key"] == "delete_source_after_archive_task_inputs"


def test_generate_parameters_scope_guard_allows_live_delete_with_years_widget():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "false",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "delete",
        "years": "2020",
    }
    stub_dbutils, _ = _run_nb(widgets, table_configs=[_valid_tc()])
    stub_dbutils.jobs.taskValues.set.assert_called_once()
    kwargs = stub_dbutils.jobs.taskValues.set.call_args.kwargs
    assert kwargs["value"][0]["years"] == [2020]


def test_generate_parameters_explicit_years_list_preserved():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "true",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "delete",
        "years": "2019, 2020, 2021",
    }
    stub_dbutils, _ = _run_nb(widgets, table_configs=[_valid_tc()])
    kwargs = stub_dbutils.jobs.taskValues.set.call_args.kwargs
    assert kwargs["value"][0]["years"] == [2019, 2020, 2021]


def test_generate_parameters_empty_years_expands_via_calculate_eligible_years():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "true",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "delete",
        "years": "",
    }
    stub_dbutils, _ = _run_nb(
        widgets, table_configs=[_valid_tc()], eligible_years=[2018, 2019],
    )
    kwargs = stub_dbutils.jobs.taskValues.set.call_args.kwargs
    assert kwargs["value"][0]["years"] == [2018, 2019]


def test_generate_parameters_archive_mode_emits_archive_task_inputs():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "true",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "archive",
        "years": "",
    }
    stub_dbutils, _ = _run_nb(widgets, table_configs=[_valid_tc()])
    kwargs = stub_dbutils.jobs.taskValues.set.call_args.kwargs
    assert kwargs["key"] == "archive_task_inputs"
    inputs = kwargs["value"]
    assert len(inputs) == 1
    assert "years" not in inputs[0]


def test_generate_parameters_invalid_job_mode_raises():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "true",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "nonsense",
        "years": "",
    }
    with pytest.raises(ArchiveConfigError, match="Invalid job_mode"):
        _run_nb(widgets, table_configs=[_valid_tc()])


def test_generate_parameters_years_csv_rejects_non_integer():
    widgets = {
        "config_table": "a.b.global_settings",
        "dry_run": "true",
        "table_config_filter": "",
        "source_catalog": "",
        "source_schema": "",
        "job_mode": "delete",
        "years": "2020, abc",
    }
    with pytest.raises(ArchiveConfigError, match="non-integer"):
        _run_nb(widgets, table_configs=[_valid_tc()])
