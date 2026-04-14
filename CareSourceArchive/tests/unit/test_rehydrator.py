from unittest.mock import MagicMock, patch

import pytest

from src.rehydrator import RehydrationEngine
from src.utils import RunContext


def _ctx():
    return RunContext(
        settings={"audit_catalog": "ac", "audit_schema": "asch"},
        secrets={},
        job_context={},
        archive_run_id="rid-1",
    )


@pytest.fixture
def rehydration_engine():
    ctx = _ctx()
    audit = MagicMock()
    spark = MagicMock()
    eng = RehydrationEngine(ctx, audit, spark)
    return (eng, ctx, audit, spark)


def _dbutils():
    return MagicMock()


def _params(**overrides):
    base = {
        "archive_base_path": "abfss://c@acct.dfs.core.windows.net/archive/root",
        "source_table": "live_cat.live_sch.claims",
        "target_catalog": "tgt_cat",
        "target_schema": "tgt_sch",
        "years": [2020, 2021],
        "dbutils": _dbutils(),
    }
    base.update(overrides)
    return base


def test_rhy01_init_stores_ctx_audit_spark(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine
    assert eng._ctx is ctx
    assert eng._audit is audit
    assert eng._spark is spark


def test_rhy02_run_no_shared_state_between_different_targets(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine
    with patch("src.rehydrator.archive_folder_exists", return_value=True):
        eng.run(
            _params(
                target_catalog="a",
                target_schema="s1",
                years=[2020],
            )
        )
        eng.run(
            _params(
                target_catalog="b",
                target_schema="s2",
                years=[2020],
            )
        )
    sql_texts = [c[0][0] for c in spark.sql.call_args_list]
    view_sqls = [s for s in sql_texts if "CREATE OR REPLACE VIEW" in s]
    assert len(view_sqls) == 2
    assert "a.s1.claims_unified" in view_sqls[0]
    assert "b.s2.claims_unified" in view_sqls[1]
    create_ext = [s for s in sql_texts if "claims_year_2020" in s and "CREATE TABLE" in s]
    assert any("a.s1.claims_year_2020" in s for s in create_ext)
    assert any("b.s2.claims_year_2020" in s for s in create_ext)


def test_rhy03_location_preferred_shallow_clone_fallback(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine
    loc_path = "abfss://c@acct.dfs.core.windows.net/archive/root/claims/year_2020"

    def sql_side_effect(q):
        if "USING DELTA LOCATION" in q and loc_path in q:
            raise RuntimeError("location bind failed")
        return MagicMock()

    spark.sql.side_effect = sql_side_effect
    with patch("src.rehydrator.archive_folder_exists", return_value=True):
        out = eng.run(_params(years=[2020]))
    sql_texts = [c[0][0] for c in spark.sql.call_args_list]
    assert any("USING DELTA LOCATION" in s and loc_path in s for s in sql_texts)
    assert any("SHALLOW CLONE" in s and f"delta.`{loc_path}`" in s for s in sql_texts)
    assert out["tables_created"] == 1


def test_rhy04_unified_view_union_all_source_and_external(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine
    with patch("src.rehydrator.archive_folder_exists", return_value=True):
        eng.run(_params(years=[2020, 2021]))
    view_sqls = [
        c[0][0] for c in spark.sql.call_args_list if "CREATE OR REPLACE VIEW" in c[0][0]
    ]
    assert len(view_sqls) == 1
    v = view_sqls[0]
    assert "UNION ALL" in v
    assert "FROM live_cat.live_sch.claims" in v.replace("\n", " ")
    assert "FROM tgt_cat.tgt_sch.claims_year_2020" in v.replace("\n", " ")
    assert "FROM tgt_cat.tgt_sch.claims_year_2021" in v.replace("\n", " ")


def test_rhy05_skips_year_when_archive_folder_missing(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine

    def folder_exists(dbutils, base_path, table_name, year):
        return year == 2020

    with patch("src.rehydrator.archive_folder_exists", side_effect=folder_exists):
        out = eng.run(_params(years=[2020, 2021]))
    sql_texts = [c[0][0] for c in spark.sql.call_args_list]
    ext_create = [s for s in sql_texts if "claims_year_" in s and "CREATE TABLE" in s]
    assert any("claims_year_2020" in s for s in ext_create)
    assert not any("claims_year_2021" in s for s in ext_create)
    assert out["tables_created"] == 1
    view_sqls = [s for s in sql_texts if "CREATE OR REPLACE VIEW" in s]
    assert len(view_sqls) == 1
    assert "claims_year_2021" not in view_sqls[0]


def test_rhy06_audit_completed_on_success_failed_on_error(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine
    with patch("src.rehydrator.archive_folder_exists", return_value=True):
        eng.run(_params(years=[2020]))
    audit.log_rehydrate.assert_called()
    kw = audit.log_rehydrate.call_args.kwargs
    assert kw["status"] == "COMPLETED"
    assert kw["tables_created"] == 1
    assert kw["error_message"] is None

    audit.reset_mock()
    spark.reset_mock()

    def boom(q):
        if "CREATE OR REPLACE VIEW" in q:
            raise RuntimeError("view failed")
        return MagicMock()

    spark.sql.side_effect = boom
    with patch("src.rehydrator.archive_folder_exists", return_value=True):
        with pytest.raises(Exception):
            eng.run(_params(years=[2020]))
    audit.log_rehydrate.assert_called()
    kw2 = audit.log_rehydrate.call_args.kwargs
    assert kw2["status"] == "FAILED"
    assert kw2["error_message"] is not None


def test_roll01_unified_view_combines_live_and_archived(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine
    with patch("src.rehydrator.archive_folder_exists", return_value=True):
        eng.run(_params(years=[2019]))
    view_sqls = [
        c[0][0] for c in spark.sql.call_args_list if "CREATE OR REPLACE VIEW" in c[0][0]
    ]
    body = view_sqls[0]
    idx_live = body.find("live_cat.live_sch.claims")
    idx_arch = body.find("tgt_cat.tgt_sch.claims_year_2019")
    assert idx_live != -1 and idx_arch != -1
    assert idx_live < idx_arch


def test_rhy05_schema_created_before_rehydration(rehydration_engine):
    eng, ctx, audit, spark = rehydration_engine
    with patch("src.rehydrator.archive_folder_exists", return_value=True):
        with patch("src.rehydrator.create_schema_if_not_exists") as mock_create_schema:
            eng.run(_params(target_catalog="tc", target_schema="ts", years=[2020]))
            mock_create_schema.assert_called_once_with(spark, "tc", "ts")
