import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.audit import (
    ALLOWED_ARCHIVE_STATUSES,
    ALLOWED_REHYDRATION_STATUSES,
    ARCHIVE_AUDIT_COLUMNS,
    ARCHIVE_SUCCESS_STATUSES,
    ARCHIVE_TERMINAL_STATUSES,
    AuditLogger,
)
from src.exceptions import ArchiveConfigError

ARCHIVE_STATUSES = tuple(sorted(ALLOWED_ARCHIVE_STATUSES))
REHYDRATION_STATUSES = tuple(sorted(ALLOWED_REHYDRATION_STATUSES))


@dataclass
class _TestRunContext:
    settings: dict
    job_context: dict
    archive_run_id: str


def _make_ctx(**kwargs):
    settings = {
        "audit_catalog": "test_catalog",
        "audit_schema": "audit",
        "warehouse_id": "super-secret-wh",
        "client_secret": "top-secret",
    }
    if "settings" in kwargs and isinstance(kwargs["settings"], dict):
        settings = {**settings, **kwargs["settings"]}
    base = {
        "settings": settings,
        "job_context": {
            "workspace_id": "ws-99",
            "job_id": "job-1",
            "job_run_id": "run-2",
            "task_run_id": "task-3",
        },
        "archive_run_id": "archive-run-uuid",
    }
    for k, v in kwargs.items():
        if k == "settings":
            continue
        base[k] = v
    base["settings"] = settings
    return _TestRunContext(**base)


@pytest.fixture
def audit_logger(mock_spark):
    ctx = _make_ctx()
    log = AuditLogger(ctx, mock_spark)
    return ctx, log, mock_spark


def test_audit_logger_init_extracts_catalog_and_schema(audit_logger):
    ctx, log, mock_spark = audit_logger
    assert log.audit_catalog == "test_catalog"
    assert log.audit_schema == "audit"
    assert log._spark is mock_spark
    assert log._ctx is ctx


@pytest.mark.parametrize("key_to_delete", ["audit_catalog", "audit_schema"])
def test_audit_logger_init_raises_when_setting_missing(mock_spark, key_to_delete):
    if key_to_delete == "audit_catalog":
        ctx = _make_ctx(settings={"audit_schema": "audit"})
    else:
        ctx = _make_ctx(settings={"audit_catalog": "c"})
    del ctx.settings[key_to_delete]
    with pytest.raises(ArchiveConfigError):
        AuditLogger(ctx, mock_spark)


@pytest.mark.parametrize(
    "method_name, expected_sql, should_raise",
    [
        (
            "ensure_archive_audit_table",
            "DESCRIBE TABLE `test_catalog`.`audit`.`archive_audit_log`",
            False,
        ),
        (
            "ensure_archive_audit_table",
            "DESCRIBE TABLE `test_catalog`.`audit`.`archive_audit_log`",
            True,
        ),
        (
            "ensure_rehydration_audit_table",
            "DESCRIBE TABLE `test_catalog`.`audit`.`rehydration_audit_log`",
            False,
        ),
        (
            "ensure_rehydration_audit_table",
            "DESCRIBE TABLE `test_catalog`.`audit`.`rehydration_audit_log`",
            True,
        ),
    ],
)
def test_ensure_audit_table(audit_logger, method_name, expected_sql, should_raise):
    _ctx, log, mock_spark = audit_logger
    if should_raise:
        mock_spark.sql.side_effect = Exception("TABLE_OR_VIEW_NOT_FOUND")
        with pytest.raises(ArchiveConfigError, match="does not exist"):
            getattr(log, method_name)()
    else:
        getattr(log, method_name)()
        mock_spark.sql.assert_called_once_with(expected_sql)


@pytest.mark.parametrize("status", ARCHIVE_STATUSES)
def test_log_archive_generates_insert_for_each_status(audit_logger, status):
    _ctx, log, mock_spark = audit_logger
    mock_spark.reset_mock()
    log.log_archive(
        table="health.claims.member",
        year=2020,
        status=status,
        record_count=50000,
        conditions_applied="x = 1",
        null_date_count=3,
        error_message=None,
    )
    mock_spark.sql.assert_called_once()
    sql = mock_spark.sql.call_args[0][0]
    assert sql.strip().upper().startswith("INSERT INTO")
    assert "`test_catalog`.`audit`.`archive_audit_log`" in sql
    assert status in sql
    assert "health.claims.member" in sql
    assert "50000" in sql
    assert "current_user()" in sql.lower()
    assert "current_timestamp()" in sql.lower()


def test_log_archive_invalid_status_raises(audit_logger):
    _ctx, log, _mock_spark = audit_logger
    with pytest.raises(ArchiveConfigError, match=re.escape("Invalid archive audit status: 'NOT_A_STATUS'")):
        log.log_archive(
            table="t",
            year=1,
            status="NOT_A_STATUS",
            record_count=0,
        )


def test_log_archive_job_06_archived_by_is_current_user(audit_logger):
    _ctx, log, mock_spark = audit_logger
    log.log_archive(table="a.b.c", year=2021, status="STARTED", record_count=1)
    sql = mock_spark.sql.call_args[0][0]
    assert re.search(r"current_user\s*\(\s*\)", sql, re.IGNORECASE)


def test_log_archive_sql_never_contains_secrets(audit_logger):
    _ctx, log, mock_spark = audit_logger
    log.log_archive(
        table="a.b.c",
        year=2022,
        status="FAILED",
        record_count=0,
        error_message="something failed",
    )
    sql = mock_spark.sql.call_args[0][0]
    assert "super-secret-wh" not in sql
    assert "top-secret" not in sql


@pytest.mark.parametrize("status", REHYDRATION_STATUSES)
def test_log_rehydrate_generates_insert(audit_logger, status):
    _ctx, log, mock_spark = audit_logger
    log.log_rehydrate(
        archive_path="abfss://x/y",
        source="src.table",
        target_catalog="tc",
        target_schema="ts",
        years="2020,2021",
        tables_created=2,
        status=status,
        error_message=None,
    )
    mock_spark.sql.assert_called_once()
    sql = mock_spark.sql.call_args[0][0]
    assert sql.strip().upper().startswith("INSERT INTO")
    assert "`test_catalog`.`audit`.`rehydration_audit_log`" in sql
    assert "abfss://x/y" in sql
    assert "src.table" in sql
    assert "tc" in sql
    assert "ts" in sql
    assert "2020,2021" in sql
    assert status in sql
    assert re.search(r"current_user\s*\(\s*\)", sql, re.IGNORECASE)
    assert "current_timestamp()" in sql.lower()


def test_log_rehydrate_rejects_invalid_status(audit_logger):
    _ctx, log, _mock_spark = audit_logger
    with pytest.raises(ArchiveConfigError, match=re.escape("Invalid rehydration audit status: 'SUCCESS'")):
        log.log_rehydrate(
            archive_path="abfss://x/y",
            source="src.table",
            target_catalog="tc",
            target_schema="ts",
            years="2020,2021",
            tables_created=2,
            status="SUCCESS",
            error_message=None,
        )


def test_log_rehydrate_sql_never_contains_secrets(audit_logger):
    _ctx, log, mock_spark = audit_logger
    log.log_rehydrate(
        archive_path="p",
        source="s",
        target_catalog="c",
        target_schema="s",
        years="1",
        tables_created=0,
        status="FAILED",
        error_message="e",
    )
    sql = mock_spark.sql.call_args[0][0]
    assert "super-secret-wh" not in sql
    assert "top-secret" not in sql


def test_log_dry_run_sets_status_and_conditions(audit_logger):
    _ctx, log, mock_spark = audit_logger
    counts = {"cond_a": 10, "cond_b": 5}
    log.log_dry_run(
        table="c.s.t",
        year=2019,
        total_eligible=100,
        would_archive=50,
        per_condition_counts=counts,
        null_date_count=2,
        action="CREATE",
    )
    sql = mock_spark.sql.call_args[0][0]
    assert "DRY_RUN" in sql
    payload = {
        "action": "CREATE",
        "per_condition_counts": counts,
        "total_eligible": 100,
        "would_archive": 50,
    }
    expected = json.dumps(payload, sort_keys=True).replace("'", "''")
    assert expected in sql
    assert "c.s.t" in sql


def test_check_resume_state_query(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [{"status": "ARCHIVED"}]
    mock_spark.sql.return_value = out_df
    table_config = {"table_id": "health.claims.member"}
    assert log.check_resume_state(table_config, 2020) == "ARCHIVED"
    sql = mock_spark.sql.call_args[0][0]
    assert "ORDER BY" in sql.upper()
    assert "created_at" in sql.lower()
    assert "LIMIT 1" in sql.upper()
    assert "health.claims.member" in sql


def test_check_resume_state_returns_none_when_empty(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    assert log.check_resume_state({"table_id": "x"}, 2000) is None


def test_check_concurrent_detects_other_run(audit_logger):
    fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"archive_run_id": "other-run", "created_at": fixed - timedelta(hours=1)}
    ]
    mock_spark.sql.return_value = out_df
    with patch("src.audit._utc_now", return_value=fixed):
        got = log.check_concurrent("a.b.c", 2020, "run-a")
    assert got[0] is True and got[1] is False and got[2] == "other-run"
    assert got[3] == pytest.approx(1.0)
    sql = mock_spark.sql.call_args[0][0]
    assert "STARTED" in sql
    assert "run-a" in sql
    assert "a.b.c" in sql
    assert "archive_run_id" in sql
    assert "created_at" in sql.lower()
    assert "NOT EXISTS" in sql


def test_check_concurrent_excludes_completed_foreign_runs(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    got = log.check_concurrent("a.b.c", 2020, "run-a")
    assert got == (False, False, None, None)
    sql = mock_spark.sql.call_args[0][0]
    assert "NOT EXISTS" in sql
    for status in ARCHIVE_TERMINAL_STATUSES:
        assert status in sql


def test_check_concurrent_false_when_empty(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    assert log.check_concurrent("a.b.c", 2020, "run-a") == (
        False,
        False,
        None,
        None,
    )


def test_aud06_aud07_context_values_bound_in_insert(audit_logger):
    _ctx, log, mock_spark = audit_logger
    log.log_archive(
        table="a.b.c",
        year=2023,
        status="ARCHIVED",
        record_count=100,
    )
    sql = mock_spark.sql.call_args[0][0]
    assert "'archive-run-uuid'" in sql
    assert "'ws-99'" in sql
    assert "'job-1'" in sql
    assert "'run-2'" in sql
    assert "'task-3'" in sql


def test_aud_archive_audit_columns_include_new_fields():
    assert "watermark_value" in ARCHIVE_AUDIT_COLUMNS
    assert "source_year_count" in ARCHIVE_AUDIT_COLUMNS
    assert "archive_mode" in ARCHIVE_AUDIT_COLUMNS


def test_aud_log_archive_with_watermark_fields(audit_logger):
    _ctx, log, mock_spark = audit_logger
    mock_spark.reset_mock()
    log.log_archive(
        table="t",
        year=2020,
        status="ARCHIVED",
        record_count=100,
        watermark_value=date(2020, 12, 31),
        source_year_count=500,
        archive_mode="CREATE",
    )
    mock_spark.sql.assert_called_once()
    sql = mock_spark.sql.call_args[0][0]
    assert "DATE '2020-12-31'" in sql
    assert "500" in sql
    assert "'CREATE'" in sql


def test_is_archived_by_run_true_when_row_exists(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [{"n": 1}]
    mock_spark.sql.return_value = out_df
    assert log.is_archived_by_run("a.b.c", 2020, "run-xyz") is True
    sql = mock_spark.sql.call_args[0][0]
    assert "ARCHIVED" in sql
    assert "a.b.c" in sql
    assert "run-xyz" in sql
    assert "LIMIT 1" in sql


def test_is_archived_by_run_false_when_no_rows(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    assert log.is_archived_by_run("a.b.c", 2020, "run-xyz") is False
    mock_spark.sql.assert_called_once()
    sql = mock_spark.sql.call_args[0][0]
    assert "a.b.c" in sql
    assert "2020" in sql
    assert "run-xyz" in sql


def test_is_archived_by_run_sql_uses_sql_quote(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    log.is_archived_by_run("it's.a.table", 2020, "run-O'Brien")
    sql = mock_spark.sql.call_args[0][0]
    assert "it''s.a.table" in sql
    assert "run-O''Brien" in sql


def test_aud_get_last_run_state_returns_tuple(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"status": "ARCHIVED", "watermark_value": date(2022, 6, 15), "source_year_count": 1000}
    ]
    mock_spark.sql.return_value = out_df
    assert log.get_last_run_state("my.table", 2022) == ("ARCHIVED", date(2022, 6, 15), 1000)
    sql = mock_spark.sql.call_args[0][0]
    assert "ORDER BY created_at DESC" in sql
    assert "LIMIT 1" in sql
    assert "ARCHIVED" in sql and "ARCHIVED_AND_DELETED" in sql


def test_aud_get_last_run_state_no_prior_run(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    assert log.get_last_run_state("my.table", 2022) == (None, None, None)


def test_aud_get_last_run_state_handles_null_source_year_count(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {
            "status": "ARCHIVED_AND_DELETED",
            "watermark_value": date(2022, 6, 15),
            "source_year_count": None,
        }
    ]
    mock_spark.sql.return_value = out_df
    assert log.get_last_run_state("my.table", 2022) == (
        "ARCHIVED_AND_DELETED",
        date(2022, 6, 15),
        None,
    )


def test_get_latest_status_returns_most_recent(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [{"status": "FAILED", "archive_run_id": "run-z"}]
    mock_spark.sql.return_value = out_df
    assert log.get_latest_status("health.claims.member", 2020) == ("FAILED", "run-z")
    sql = mock_spark.sql.call_args[0][0]
    assert "ORDER BY created_at DESC" in sql
    assert "LIMIT 1" in sql
    assert "status IN" not in sql
    assert "archive_run_id" in sql
    assert "health.claims.member" in sql


def test_get_latest_status_returns_none_when_empty(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    assert log.get_latest_status("x.y.z", 2000) is None


def test_check_concurrent_returns_tuple_not_stale(audit_logger):
    fixed = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
    created = fixed - timedelta(hours=1)
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"archive_run_id": "foreign-run", "created_at": created}
    ]
    mock_spark.sql.return_value = out_df
    with patch("src.audit._utc_now", return_value=fixed):
        got = log.check_concurrent(
            "a.b.c", 2020, "run-a", stale_threshold_hours=4
        )
    assert got[0] is True and got[1] is False and got[2] == "foreign-run"
    assert got[3] == pytest.approx(1.0)
    sql = mock_spark.sql.call_args[0][0]
    assert "archive_run_id" in sql
    assert "created_at" in sql.lower()


def test_check_concurrent_returns_stale(audit_logger):
    fixed = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
    created = fixed - timedelta(hours=10)
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"archive_run_id": "foreign-run", "created_at": created}
    ]
    mock_spark.sql.return_value = out_df
    with patch("src.audit._utc_now", return_value=fixed):
        got = log.check_concurrent(
            "a.b.c", 2020, "run-a", stale_threshold_hours=4
        )
    assert got[0] is False and got[1] is True and got[2] == "foreign-run"
    assert got[3] == pytest.approx(10.0)


def test_check_concurrent_custom_threshold(audit_logger):
    fixed = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
    created = fixed - timedelta(hours=2)
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"archive_run_id": "foreign-run", "created_at": created}
    ]
    mock_spark.sql.return_value = out_df
    with patch("src.audit._utc_now", return_value=fixed):
        fresh = log.check_concurrent(
            "a.b.c", 2020, "run-a", stale_threshold_hours=4
        )
        stale = log.check_concurrent(
            "a.b.c", 2020, "run-a", stale_threshold_hours=1
        )
    assert fresh[:2] == (True, False)
    assert stale[:2] == (False, True)


def test_check_concurrent_non_datetime_created_at_treated_as_stale(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"archive_run_id": "foreign-run", "created_at": "not-a-datetime"}
    ]
    mock_spark.sql.return_value = out_df
    result = log.check_concurrent("a.b.c", 2020, "run-a", stale_threshold_hours=4)
    assert result[0] is False
    assert result[1] is True
    assert result[2] == "foreign-run"
    assert result[3] == pytest.approx(4.0)


def test_check_concurrent_boundary_at_exact_threshold(audit_logger):
    fixed = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"archive_run_id": "edge-run", "created_at": fixed - timedelta(hours=4)}
    ]
    mock_spark.sql.return_value = out_df
    with patch("src.audit._utc_now", return_value=fixed):
        result = log.check_concurrent("a.b.c", 2020, "run-a", stale_threshold_hours=4)
    assert result[0] is False
    assert result[1] is True
    assert result[2] == "edge-run"
    assert result[3] == pytest.approx(4.0)


def test_terminal_statuses_invariant():
    assert ARCHIVE_TERMINAL_STATUSES == ALLOWED_ARCHIVE_STATUSES - {"STARTED"}


def test_success_statuses_subset_of_terminal():
    assert ARCHIVE_SUCCESS_STATUSES < ARCHIVE_TERMINAL_STATUSES


def test_get_last_run_state_uses_success_statuses(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = []
    mock_spark.sql.return_value = out_df
    log.get_last_run_state("my.table", 2022)
    sql = mock_spark.sql.call_args[0][0]
    for status in ARCHIVE_SUCCESS_STATUSES:
        assert status in sql
    assert "FAILED" not in sql.split("IN")[1]
