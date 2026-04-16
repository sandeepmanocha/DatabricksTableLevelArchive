import json
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import ArchiveOperationError
from src.rehydrator import RehydrationEngine
from src.utils import RunContext


def _ctx():
    return RunContext(
        settings={"audit_catalog": "ac", "audit_schema": "asch"},
        job_context={},
        archive_run_id="rid-1",
    )


@pytest.fixture
def rehydration_engine():
    ctx = _ctx()
    audit = MagicMock()
    spark = MagicMock()
    eng = RehydrationEngine(ctx, audit, spark)
    return eng, audit, spark


def _params(**overrides):
    base = {
        "archive_base_path": "abfss://c@acct.dfs.core.windows.net/archive/root",
        "source_table": "live_cat.live_sch.claims",
        "target_catalog": "tgt_cat",
        "target_schema": "tgt_sch",
        "years": [2020, 2021],
        "available_archive_years": [2020, 2021],
        "table_prefix": "",
        "create_unified_view": True,
    }
    base.update(overrides)
    return base


def test_status_completed_when_all_years_restored(rehydration_engine):
    eng, audit, _spark = rehydration_engine
    result = eng.run(_params(years=[2020, 2021], available_archive_years=[2020, 2021]))
    assert result["tables_created"] == 2
    assert result["status"] == "COMPLETED"
    assert result["restored_years"] == [2020, 2021]
    assert result["skipped_years"] == []
    audit.ensure_rehydration_audit_table.assert_called_once()
    kwargs = audit.log_rehydrate.call_args.kwargs
    assert kwargs["status"] == "COMPLETED"
    assert kwargs["tables_created"] == 2
    assert kwargs["error_message"] is None
    assert kwargs["source"] == "live_cat.live_sch.claims"
    assert kwargs["target_catalog"] == "tgt_cat"
    assert kwargs["target_schema"] == "tgt_sch"


def test_status_partial_completed_when_some_years_missing(rehydration_engine):
    eng, audit, _spark = rehydration_engine
    result = eng.run(_params(years=[2020, 2021], available_archive_years=[2020]))
    assert result["tables_created"] == 1
    assert result["status"] == "PARTIAL_COMPLETED"
    assert result["restored_years"] == [2020]
    assert result["skipped_years"] == [2021]
    kwargs = audit.log_rehydrate.call_args.kwargs
    assert kwargs["status"] == "PARTIAL_COMPLETED"
    assert kwargs["tables_created"] == 1
    assert kwargs["error_message"] is None
    assert kwargs["source"] == "live_cat.live_sch.claims"
    assert kwargs["target_catalog"] == "tgt_cat"
    assert kwargs["target_schema"] == "tgt_sch"


def test_failed_when_no_year_restored(rehydration_engine):
    eng, audit, _spark = rehydration_engine
    with pytest.raises(ArchiveOperationError) as exc:
        eng.run(_params(years=[2020], available_archive_years=[]))
    assert exc.value.reason == "no_years_restored"
    assert exc.value.year == "all"
    assert exc.value.operation == "rehydrate"
    kwargs = audit.log_rehydrate.call_args.kwargs
    assert kwargs["status"] == "FAILED"
    assert kwargs["tables_created"] == 0
    assert kwargs["error_message"] is not None


def test_location_create_failure_raises_typed_error_without_clone(rehydration_engine):
    eng, audit, spark = rehydration_engine
    loc_path = "abfss://c@acct.dfs.core.windows.net/archive/root/claims/year_2020"

    def side_effect(sql):
        if "USING DELTA LOCATION" in sql and loc_path in sql:
            raise RuntimeError("location bind failed")
        return MagicMock()

    spark.sql.side_effect = side_effect
    with pytest.raises(ArchiveOperationError) as exc:
        eng.run(_params(years=[2020], available_archive_years=[2020]))
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    assert any("USING DELTA LOCATION" in sql for sql in sqls)
    assert not any("SHALLOW CLONE" in sql for sql in sqls)
    assert exc.value.table == "live_cat.live_sch.claims"
    assert exc.value.year == 2020
    assert exc.value.operation == "create_external_table"
    assert "location bind failed" in str(exc.value)
    kwargs = audit.log_rehydrate.call_args.kwargs
    assert kwargs["status"] == "FAILED"
    assert kwargs["tables_created"] == 0
    assert kwargs["error_message"] is not None


def test_runtime_table_prefix_applies_to_external_tables_and_view(rehydration_engine):
    eng, _audit, spark = rehydration_engine
    result = eng.run(
        _params(years=[2020], available_archive_years=[2020], table_prefix="rhy_")
    )
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    assert any("tgt_cat.tgt_sch.rhy_claims_year_2020" in sql for sql in sqls)
    view_sql = next(sql for sql in sqls if sql.startswith("CREATE OR REPLACE VIEW"))
    assert "CREATE OR REPLACE VIEW tgt_cat.tgt_sch.rhy_claims_unified" in view_sql
    assert "SELECT * FROM live_cat.live_sch.claims" in view_sql
    assert "UNION ALL" in view_sql
    assert "SELECT * FROM tgt_cat.tgt_sch.rhy_claims_year_2020" in view_sql
    assert result["view_name"] == "tgt_cat.tgt_sch.rhy_claims_unified"


def test_runtime_can_disable_unified_view_creation(rehydration_engine):
    eng, _audit, spark = rehydration_engine
    result = eng.run(
        _params(
            years=[2020],
            available_archive_years=[2020],
            create_unified_view=False,
        )
    )
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    assert not any("CREATE OR REPLACE VIEW" in sql for sql in sqls)
    assert result["view_name"] is None


def test_audit_write_failure_logs_fallback_and_preserves_context(rehydration_engine):
    eng, audit, spark = rehydration_engine

    def sql_boom(sql):
        if "CREATE OR REPLACE VIEW" in sql:
            raise RuntimeError("view failed")
        return MagicMock()

    spark.sql.side_effect = sql_boom
    audit.log_rehydrate.side_effect = RuntimeError("audit write failed")
    with patch("src.rehydrator.LOGGER.error") as error_log:
        with pytest.raises(ArchiveOperationError) as exc:
            eng.run(_params(years=[2020], available_archive_years=[2020]))
    assert "view failed" in str(exc.value)
    assert "audit write failed" in str(exc.value)
    assert error_log.call_count == 1
    fallback_payload = json.loads(error_log.call_args.args[0])
    assert fallback_payload["event"] == "rehydration_audit_write_failed"
    assert fallback_payload["created_years"] == [2020]
    assert fallback_payload["skipped_years"] == []
    assert fallback_payload["tables_created"] == 1
    assert fallback_payload["status"] == "FAILED"
    assert "view failed" in fallback_payload["primary_error"]
    assert "audit write failed" in fallback_payload["audit_error"]


@pytest.mark.parametrize(
    "missing_key",
    [
        "archive_base_path",
        "source_table",
        "target_catalog",
        "target_schema",
        "years",
        "available_archive_years",
    ],
)
def test_missing_required_param_fails_with_actionable_error(rehydration_engine, missing_key):
    eng, audit, _spark = rehydration_engine
    params = _params(years=[2020])
    del params[missing_key]
    with pytest.raises(ArchiveOperationError, match=missing_key) as exc:
        eng.run(params)
    assert exc.value.reason == "invalid_params"
    kwargs = audit.log_rehydrate.call_args.kwargs
    assert kwargs["status"] == "FAILED"


def test_view_creation_failure_raises_typed_error(rehydration_engine):
    eng, audit, spark = rehydration_engine

    def side_effect(sql):
        if "CREATE OR REPLACE VIEW" in sql:
            raise RuntimeError("view grant denied")
        return MagicMock()

    spark.sql.side_effect = side_effect
    with pytest.raises(ArchiveOperationError) as exc:
        eng.run(_params(years=[2020], available_archive_years=[2020]))
    assert exc.value.operation == "create_unified_view"
    assert exc.value.reason == "view_create_failed"
    assert "view grant denied" in str(exc.value)
    kwargs = audit.log_rehydrate.call_args.kwargs
    assert kwargs["status"] == "FAILED"
    assert kwargs["tables_created"] == 1


def test_empty_years_list_raises_no_years_restored(rehydration_engine):
    eng, audit, _spark = rehydration_engine
    with pytest.raises(ArchiveOperationError) as exc:
        eng.run(_params(years=[], available_archive_years=[]))
    assert exc.value.reason == "no_years_restored"
    kwargs = audit.log_rehydrate.call_args.kwargs
    assert kwargs["status"] == "FAILED"
    assert kwargs["tables_created"] == 0
