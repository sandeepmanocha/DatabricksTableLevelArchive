import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import ArchiveOperationError, ArchiveVerificationError
from src.utils import RunContext


def _global_settings():
    return {
        "audit_catalog": "gcat",
        "audit_schema": "gaudit",
        "default_retention_years": 7,
        "archive_base_path_prefix": "abfss://x/",
        "secret_scope": "s",
        "schema_templates_table": "gcat.cfg.tmpl",
        "table_configs_table": "gcat.cfg.tbl",
        "timezone": "America/New_York",
    }


def _table_config(**overrides):
    base = {
        "table_id": "healthcare.claims.member",
        "source_catalog": "src_cat",
        "source_schema": "src_sch",
        "source_table": "claims",
        "watermark_column": "claim_date",
        "archive_base_path": "abfss://stor/archive",
        "retention_years": 5,
        "delete_after_archive": True,
        "exclusion_conditions": json.dumps(
            [
                {
                    "name": "active_only",
                    "type": "same_table",
                    "column": "status",
                    "operator": "equals",
                    "value": "Active",
                }
            ]
        ),
        "is_active": True,
    }
    base.update(overrides)
    return base


def _make_engine(ctx=None, audit=None, spark=None):
    from src.archiver import ArchiveEngine

    if ctx is None:
        ctx = RunContext(
            settings=_global_settings(),
            secrets={},
            job_context={"workspace_id": "ws1"},
            archive_run_id="run-a",
        )
    if audit is None:
        audit = MagicMock()
    if spark is None:
        spark = MagicMock()
    return ArchiveEngine(ctx, audit, spark), ctx, audit, spark


def test_arc01_init_stores_ctx_audit_spark():
    from src.archiver import ArchiveEngine

    ctx = RunContext(settings=_global_settings(), secrets={}, job_context={}, archive_run_id="r")
    audit = MagicMock()
    spark = MagicMock()
    eng = ArchiveEngine(ctx, audit, spark)
    assert eng._ctx is ctx
    assert eng._audit is audit
    assert eng._spark is spark


def test_arc02_calculate_eligible_years_retention_window():
    eng, _, _, spark = _make_engine()

    def sql_side_effect(q):
        m = MagicMock()
        if "YEAR(current_date())" in q.replace("\n", " "):
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [
                MagicMock(yr=2018),
                MagicMock(yr=2019),
                MagicMock(yr=2020),
                MagicMock(yr=2025),
            ]
            return m
        return m

    spark.sql.side_effect = sql_side_effect
    tc = _table_config(retention_years=5)
    years = eng._calculate_eligible_years(tc, 5)
    assert years == [2018, 2019, 2020]


def test_arc03_archive_year_generates_write_sql():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 42}
    tc = _table_config()
    exclusion = "NOT (src.status = 'Active')"
    archive_path = "abfss://stor/archive/claims/year_2020"
    eng._archive_year(tc, 2020, exclusion)
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    create_sql = next(s for s in sqls if "CREATE TABLE" in s and "USING DELTA" in s)
    assert f"delta.`{archive_path}`" in create_sql
    assert "src_cat.src_sch.claims" in create_sql.replace("\n", " ")
    assert "YEAR(src.claim_date) = 2020" in create_sql.replace("\n", " ")
    assert "src.claim_date IS NOT NULL" in create_sql.replace("\n", " ")
    assert exclusion in create_sql


def test_arc04_exclusion_conditions_applied():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 1}
    tc = _table_config()
    clause = "NOT (src.x = 1) AND NOT (src.y = 2)"
    eng._archive_year(tc, 2021, clause)
    written = [c[0][0] for c in spark.sql.call_args_list][0]
    assert clause in written


def test_arc05_verify_count_match():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 100}
    eng._verify_archive(_table_config(), 2020, 100)


def test_arc05_verify_count_mismatch_raises():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 99}
    with pytest.raises(ArchiveVerificationError):
        eng._verify_archive(_table_config(), 2020, 100)


def test_arc06_delete_archived_generates_delete_sql():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    tc = _table_config()
    clause = "NOT (src.status = 'Active')"
    eng._delete_archived(tc, 2020, clause)
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    del_sql = sqls[-1]
    assert "DELETE FROM" in del_sql
    assert "src_cat.src_sch.claims" in del_sql.replace("\n", " ")
    assert "YEAR(claim_date) = 2020" in del_sql.replace("\n", " ")
    assert "claim_date IS NOT NULL" in del_sql.replace("\n", " ")
    assert clause.replace("src.", "") in del_sql or "NOT" in del_sql


def test_arc08_write_metadata_mode_field():
    eng, _, _, spark = _make_engine()
    dbutils = MagicMock()
    tc = _table_config()
    eng._write_metadata(tc, 2020, 50, [{"name": "c1"}], "append", dbutils)
    dbutils.fs.put.assert_called_once()
    args, _ = dbutils.fs.put.call_args
    path = args[0]
    body = args[1]
    assert "_archive_metadata.json" in path
    assert json.loads(body.decode() if isinstance(body, (bytes, bytearray)) else body)["mode"] == "append"


def test_arc09_resume_archived_delete_true_runs_delete():
    eng, _, audit, spark = _make_engine()

    def last_state(table, year):
        return ("ARCHIVED", date(2019, 1, 1), 10) if year == 2019 else (None, None, None)

    audit.get_last_run_state.side_effect = last_state
    audit.check_concurrent.return_value = (False, False, None, None)

    own_rows = [MagicMock()]

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2019)]
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 10}
            return m
        if "archive_audit_log" in q and "SELECT 1" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = own_rows
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 10}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        eng.run(
            _table_config(delete_after_archive=True),
            dry_run=False,
            dbutils=dbutils,
        )
    delete_sqls = [c[0][0] for c in spark.sql.call_args_list if c[0][0].strip().upper().startswith("DELETE")]
    assert delete_sqls
    create_like = [
        c[0][0]
        for c in spark.sql.call_args_list
        if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]
    ]
    assert not create_like


def test_arc10_validate_archives_checks_folders():
    eng, _, _, spark = _make_engine()

    def sql_side_effect(q):
        m = MagicMock()
        if "YEAR(current_date())" in q.replace("\n", " "):
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2020 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2015)]
            return m
        return m

    spark.sql.side_effect = sql_side_effect
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True) as p_exists:
        out = eng.validate_archives([_table_config(retention_years=3)], dbutils)
    p_exists.assert_called()
    assert out["valid"] is True


def test_arc12_append_mode_year_exists_delete_true():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED_AND_DELETED", date(2018, 6, 15), 500)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 7}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 800}
            return m
        if "INSERT INTO" in q and "delta.`" in q:
            m.first.return_value = {"count": 7}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 7}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 12, 31)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    insert_sqls = [c[0][0] for c in spark.sql.call_args_list if "INSERT INTO" in c[0][0] and "delta.`" in c[0][0]]
    assert insert_sqls
    assert any("src.claim_date > DATE '2018-06-15'" in s.replace("\n", " ") for s in insert_sqls)
    archived_kw = [
        c.kwargs
        for c in audit.log_archive.call_args_list
        if c.kwargs.get("status") == "ARCHIVED"
    ]
    assert any(k.get("archive_mode") == "APPEND" for k in archived_kw)


def test_arc13_skip_mode_year_exists_delete_false():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 12, 31), 200)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 0}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 200}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    statuses = [c.kwargs.get("status") for c in audit.log_archive.call_args_list if c.kwargs]
    assert "SKIPPED" in statuses
    skip_kw = [
        c.kwargs
        for c in audit.log_archive.call_args_list
        if c.kwargs.get("status") == "SKIPPED"
    ]
    assert any(k.get("archive_mode") == "SKIP" for k in skip_kw)
    insert_sqls = [c[0][0] for c in spark.sql.call_args_list if "INSERT INTO" in c[0][0] and "delta.`" in c[0][0]]
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert not insert_sqls
    assert not create_sqls


def test_conc01_started_logged_before_archive():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    order = []

    def log_archive(*args, **kwargs):
        order.append(("log", kwargs.get("status") or (args[2] if len(args) > 2 else None)))

    audit.log_archive.side_effect = log_archive

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2017)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            order.append(("sql_create",))
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 1}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2017, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    idx_started = next(i for i, x in enumerate(order) if x == ("log", "STARTED"))
    idx_create = next(i for i, x in enumerate(order) if x == ("sql_create",))
    assert idx_started < idx_create


def test_conc02_concurrent_detected_skips():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (True, False, "other-run", 1.0)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2016)]
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    statuses = [c.kwargs.get("status") for c in audit.log_archive.call_args_list if c.kwargs]
    assert "SKIPPED_CONCURRENT" in statuses
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert not create_sqls


def test_conc03_ownership_verified_before_delete():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    call_order = []

    def track_ownership(*args, **kwargs):
        call_order.append("ownership_check")
        return True

    audit.is_archived_by_run.side_effect = track_ownership

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2015)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 5}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 5}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 5}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2015, 6, 1)}
            return m
        if q.strip().upper().startswith("DELETE"):
            call_order.append("delete_sql")
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    assert "ownership_check" in call_order
    assert "delete_sql" in call_order
    assert call_order.index("ownership_check") < call_order.index("delete_sql")
    audit.is_archived_by_run.assert_called_with("healthcare.claims.member", 2015, "run-a")


def test_edge01_null_date_filter_and_count():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 3}
    tc = _table_config()
    n = eng._count_nulls(tc, 2020)
    assert n == 3
    written = [c[0][0] for c in spark.sql.call_args_list][0]
    assert "claim_date IS NULL" in written.replace("\n", " ")
    eng._archive_year(tc, 2020, "")
    arch_sql = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0]][0]
    assert "src.claim_date IS NOT NULL" in arch_sql.replace("\n", " ")


def test_edge03_timezone_applied():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    spark.conf = MagicMock()

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2014)]
            return m
        if "CREATE TABLE" in q:
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 1}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2014, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    spark.conf.set.assert_called()
    tz_calls = [c for c in spark.conf.set.call_args_list if c[0][0] == "spark.sql.session.timeZone"]
    assert tz_calls
    assert tz_calls[0][0][1] == "America/New_York"


def test_dry01_dry_run_default_true():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2013)]
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dbutils=dbutils)
    write_sqls = [
        c[0][0]
        for c in spark.sql.call_args_list
        if "CREATE TABLE" in c[0][0] or (c[0][0].strip().upper().startswith("DELETE"))
    ]
    assert not [s for s in write_sqls if "CREATE TABLE" in s and "USING DELTA AS" in s]
    assert not [s for s in write_sqls if s.strip().upper().startswith("DELETE")]


def test_dry02_per_condition_counts():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2012)]
            return m
        m.first.return_value = {"count": 11}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dry_run=True, dbutils=dbutils)
    dry_calls = [c for c in audit.log_dry_run.call_args_list if c.kwargs]
    assert dry_calls
    pc = dry_calls[0].kwargs.get("per_condition_counts") or {}
    assert "active_only" in pc
    assert dry_calls[0].kwargs.get("action") == "CREATE"


def test_dry05_structured_report_returned():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2011)]
            return m
        m.first.return_value = {"count": 4}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        rep = eng.run(_table_config(), dry_run=True, dbutils=dbutils)
    assert "tables" in rep
    tid = _table_config()["table_id"]
    assert tid in rep["tables"]
    assert "years" in rep["tables"][tid]
    assert 2011 in rep["tables"][tid]["years"]
    yr_data = rep["tables"][tid]["years"][2011]
    assert yr_data["action"] == "CREATE"


def test_err03_custom_sql_error_wrapped():
    eng, _, _, spark = _make_engine()

    def boom(q):
        if "CREATE TABLE" in q and "USING DELTA" in q:
            raise RuntimeError("bad custom sql")
        m = MagicMock()
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = boom
    with pytest.raises(ArchiveOperationError) as ei:
        eng._archive_year(_table_config(), 2020, "NOT (1=1)")
    assert "bad custom sql" in str(ei.value).lower() or "bad custom sql" in str(ei.value)


def test_arc06_no_delete_when_delete_after_false_and_archive_succeeds():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2017)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 10}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 10}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 10}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2017, 6, 1)}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    delete_sqls = [
        c[0][0]
        for c in spark.sql.call_args_list
        if c[0][0].strip().upper().startswith("DELETE")
    ]
    assert not delete_sqls
    statuses = [c.kwargs.get("status") for c in audit.log_archive.call_args_list if c.kwargs]
    assert "ARCHIVED" in statuses
    assert "ARCHIVED_AND_DELETED" not in statuses


def test_conc04_concurrent_append_exception_surfaces_as_operation_error():
    eng, _, _, spark = _make_engine()

    def boom(q):
        m = MagicMock()
        if "CREATE TABLE" in q and "USING DELTA" in q:
            raise ArchiveOperationError(
                "ConcurrentAppendException: conflicting commit",
                table="c.s.t",
                year=2020,
                operation="archive",
                reason="ConcurrentAppendException",
            )
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = boom
    with pytest.raises(ArchiveOperationError) as ei:
        eng._archive_year(_table_config(), 2020, "")
    assert "ConcurrentAppendException" in str(ei.value) or "concurrent" in str(ei.value).lower()


def test_edge02_schema_evolution_archive_succeeds():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2016)]
            return m
        if "CREATE TABLE" in q and "USING DELTA AS" in q:
            m.first.return_value = {"count": 5}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 5}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 5}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2016, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    tc = _table_config(
        exclusion_conditions=json.dumps([
            {"name": "active_only", "type": "same_table", "column": "new_col", "operator": "equals", "value": "Y"}
        ])
    )
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(tc, dry_run=False, dbutils=dbutils)
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert create_sqls
    assert "new_col" in create_sqls[0]


def test_dry03_per_condition_counts_all_conditions():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)

    multi_cond_config = _table_config(
        exclusion_conditions=json.dumps([
            {"name": "cond_a", "type": "same_table", "column": "status", "operator": "equals", "value": "Active"},
            {"name": "cond_b", "type": "same_table", "column": "flag", "operator": "is_not_null", "value": None},
        ])
    )

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2010)]
            return m
        m.first.return_value = {"count": 20}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(multi_cond_config, dry_run=True, dbutils=dbutils)
    dry_calls = [c for c in audit.log_dry_run.call_args_list if c.kwargs]
    assert dry_calls
    pc = dry_calls[0].kwargs.get("per_condition_counts") or {}
    assert "cond_a" in pc
    assert "cond_b" in pc


def test_dry04_conditions_applied_in_audit_log():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2009)]
            return m
        m.first.return_value = {"count": 15}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dry_run=True, dbutils=dbutils)
    dry_calls = [c for c in audit.log_dry_run.call_args_list if c.kwargs]
    assert dry_calls
    kw = dry_calls[0].kwargs
    assert "total_eligible" in kw
    assert "would_archive" in kw
    assert "per_condition_counts" in kw
    assert isinstance(kw["per_condition_counts"], dict)


def _claims_count_sql_handler(qs, m):
    if "src_cat.src_sch.claims" not in qs or "archive_audit" in qs:
        return None
    if "COUNT(" not in qs:
        return None
    if " IS NULL" in qs:
        m.first.return_value = {"count": 0}
        return m
    m.first.return_value = {"count": 55}
    return m


def test_wm01_folder_missing_creates_archive():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        r = _claims_count_sql_handler(qs, m)
        if r is not None:
            return r
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 55}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 55}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 11, 1)}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert create_sqls
    archived_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED"]
    assert any(k.get("archive_mode") == "CREATE" for k in archived_kw)
    assert all(k.get("watermark_value") is not None for k in archived_kw)
    assert all(k.get("source_year_count") is not None for k in archived_kw)


def test_wm02_folder_exists_new_data_appends():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 6, 15), 500)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 3}
            return m
        r = _claims_count_sql_handler(qs, m)
        if r is not None:
            return r
        if "INSERT INTO" in q and "delta.`" in q:
            m.first.return_value = {"count": 3}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 10}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 12, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    insert_sqls = [c[0][0] for c in spark.sql.call_args_list if "INSERT INTO" in c[0][0] and "delta.`" in c[0][0]]
    assert insert_sqls
    assert any("src.claim_date > DATE '2018-06-15'" in s.replace("\n", " ") for s in insert_sqls)
    archived_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED"]
    assert any(k.get("archive_mode") == "APPEND" for k in archived_kw)


def test_wm03_folder_exists_no_new_data_skips():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 12, 31), 200)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 0}
            return m
        r = _claims_count_sql_handler(qs, m)
        if r is not None:
            return r
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    skip_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "SKIPPED"]
    assert skip_kw
    assert any(k.get("archive_mode") == "SKIP" for k in skip_kw)
    insert_sqls = [c[0][0] for c in spark.sql.call_args_list if "INSERT INTO" in c[0][0] and "delta.`" in c[0][0]]
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert not insert_sqls
    assert not create_sqls


def test_wm04_multiple_years_independent():
    eng, _, audit, spark = _make_engine()

    def last_state(table, year):
        if year == 2018:
            return (None, None, None)
        return ("ARCHIVED", date(2019, 12, 31), 100)

    audit.get_last_run_state.side_effect = last_state
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018), MagicMock(yr=2019)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs and "2019" in qs:
            m.first.return_value = {"count": 0}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            if "= 2019" in qs or "= 2019" in q:
                m.first.return_value = {"count": 100}
                return m
            if "= 2018" in qs or "= 2018" in q:
                m.first.return_value = {"count": 40}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 40}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 40}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 10, 1)}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()

    def folder_side(db, base, table_name, year):
        return year == 2019

    with patch("src.archiver.archive_folder_exists", side_effect=folder_side):
        eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert any("YEAR(src.claim_date) = 2018" in s.replace("\n", " ") for s in create_sqls)
    assert not any("YEAR(src.claim_date) = 2019" in s.replace("\n", " ") for s in create_sqls)
    insert_sqls = [c[0][0] for c in spark.sql.call_args_list if "INSERT INTO" in c[0][0] and "delta.`" in c[0][0]]
    assert not insert_sqls


def test_wm05_delete_after_true_verify_and_delete():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.return_value = True
    call_order = []

    def track_ownership(*args, **kwargs):
        call_order.append("ownership_check")
        return True

    audit.is_archived_by_run.side_effect = track_ownership

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        r = _claims_count_sql_handler(qs, m)
        if r is not None:
            return r
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 12}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 12}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 8, 1)}
            return m
        if q.strip().upper().startswith("DELETE"):
            call_order.append("delete_sql")
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    delete_sqls = [c[0][0] for c in spark.sql.call_args_list if c[0][0].strip().upper().startswith("DELETE")]
    assert delete_sqls
    assert "ownership_check" in call_order
    assert "delete_sql" in call_order
    assert call_order.index("ownership_check") < call_order.index("delete_sql")
    audit.is_archived_by_run.assert_called_with("healthcare.claims.member", 2018, "run-a")


def test_wm06_count_drift_warning_logged():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 12, 31), 500)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 0}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 400}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        with patch("src.archiver.LOGGER") as mock_log:
            eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    mock_log.warning.assert_called()
    joined = " ".join(str(c) for c in mock_log.warning.call_args_list)
    assert "dropped" in joined.lower()


def test_wm07_folder_missing_archived_deleted_errors():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED_AND_DELETED", date(2018, 12, 31), 500)
    audit.check_concurrent.return_value = (False, False, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 400}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    assert "missing_folder_after_delete" in str(ei.value) or "lost" in str(ei.value).lower()


def test_uc2_orphan_folder_raises_error():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = None

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    assert "no successful archive is recorded" in str(ei.value)


def test_uc3_failed_retry_logs_info():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = ("FAILED", "old-run-id")

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2017)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 1}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2017, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        with patch("src.archiver.LOGGER") as mock_log:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    joined = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "FAILED" in joined
    assert "old-run-id" in joined


def test_uc3_stale_started_retry_logs_warning():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = ("STARTED", "old-run-id")

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2017)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 1}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2017, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        with patch("src.archiver.LOGGER") as mock_log:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    joined = " ".join(str(c) for c in mock_log.warning.call_args_list)
    assert "STARTED" in joined
    assert "completed cleanly" in joined.lower()


def test_uc2_uc3_failed_with_folder_raises_orphan():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = ("FAILED", "old-run-id")

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    assert "no successful archive is recorded" in str(ei.value)


def test_uc1_multi_year_fail_fast():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = None

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018), MagicMock(yr=2019)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            if "= 2018" in qs:
                raise RuntimeError("create failed 2018")
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 1}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        with pytest.raises(ArchiveOperationError):
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    started_years = [
        c.kwargs.get("year")
        for c in audit.log_archive.call_args_list
        if c.kwargs.get("status") == "STARTED"
    ]
    assert started_years == [2018]
    failed_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "FAILED"]
    assert failed_kw
    assert failed_kw[0].get("year") == 2018


def test_uc1_catch_all_logs_failed_on_unhandled_exception():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = None

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2017)]
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        with patch.object(eng, "_count_source_by_year", side_effect=RuntimeError("boom uc1")):
            with pytest.raises(ArchiveOperationError) as ei:
                eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    assert "boom uc1" in str(ei.value) or (
        ei.value.__cause__ is not None and "boom uc1" in str(ei.value.__cause__)
    )
    failed_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "FAILED"]
    assert failed_kw
    assert "boom uc1" in (failed_kw[0].get("error_message") or "")


def test_uc6_stale_concurrent_proceeds():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, True, "foreign-run", 10.5)
    audit.get_latest_status.return_value = None

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2017)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 1}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2017, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        with patch("src.archiver.LOGGER") as mock_log:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    warn_joined = " ".join(str(c) for c in mock_log.warning.call_args_list)
    assert "stale" in warn_joined.lower()
    assert "STARTED" in warn_joined
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert create_sqls


def test_uc6_fresh_concurrent_skips():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (True, False, "foreign-run", 2.0)
    audit.get_latest_status.return_value = None

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2016)]
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    statuses = [c.kwargs.get("status") for c in audit.log_archive.call_args_list if c.kwargs]
    assert "SKIPPED_CONCURRENT" in statuses
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert not create_sqls


def test_uc6_stale_threshold_passed_from_settings():
    settings = _global_settings()
    settings["stale_started_threshold_hours"] = 8
    ctx = RunContext(settings=settings, secrets={}, job_context={"workspace_id": "ws1"}, archive_run_id="run-a")
    eng, _, audit, spark = _make_engine(ctx=ctx)
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = None

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2016)]
            return m
        if "CREATE TABLE" in q and "USING DELTA" in q:
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 1}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 1}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2016, 6, 1)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=False):
        eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    cc_call = audit.check_concurrent.call_args
    assert cc_call.kwargs.get("stale_threshold_hours") == 8.0


# --- _resolve_year_action tests ---


def test_resolve01_no_folder_returns_create():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 50}

    tc = _table_config()
    result = eng._resolve_year_action(tc, 2020, "1=1", folder_exists=False)
    assert result["action"] == "CREATE"
    assert result["would_archive"] == 50
    assert result["folder_exists"] is False
    assert result["last_status"] is None


def test_resolve02_folder_no_new_data_returns_skip():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "COUNT(" in qs and "src_cat.src_sch.claims src" in qs:
            m.first.return_value = {"count": 0}
            return m
        m.first.return_value = {"count": 100}
        return m

    spark.sql.side_effect = sql_side_effect

    tc = _table_config(delete_after_archive=False)
    result = eng._resolve_year_action(tc, 2020, "1=1", folder_exists=True)
    assert result["action"] == "SKIP"
    assert result["would_archive"] == 0
    assert result["folder_exists"] is True
    assert result["last_status"] == "ARCHIVED"


def test_resolve03_folder_new_data_returns_append():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 6, 15), 100)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "COUNT(" in qs and "src_cat.src_sch.claims src" in qs:
            m.first.return_value = {"count": 5}
            return m
        m.first.return_value = {"count": 200}
        return m

    spark.sql.side_effect = sql_side_effect

    tc = _table_config(delete_after_archive=False)
    result = eng._resolve_year_action(tc, 2020, "1=1", folder_exists=True)
    assert result["action"] == "APPEND"
    assert result["would_archive"] == 5
    assert result["folder_exists"] is True


def test_resolve04_missing_folder_after_delete_returns_error():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED_AND_DELETED", date(2020, 12, 31), 100)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 50}

    tc = _table_config()
    result = eng._resolve_year_action(tc, 2020, "1=1", folder_exists=False)
    assert result["action"] == "ERROR"
    assert result["error_reason"] == "missing_folder_after_delete"


def test_resolve05_orphan_folder_returns_error():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 50}

    tc = _table_config()
    result = eng._resolve_year_action(tc, 2020, "1=1", folder_exists=True)
    assert result["action"] == "ERROR"
    assert result["error_reason"] == "orphan_folder"


def test_resolve06_resume_delete():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 100}

    tc = _table_config(delete_after_archive=True)
    result = eng._resolve_year_action(tc, 2020, "1=1", folder_exists=True)
    assert result["action"] == "RESUME_DELETE"


# --- Enhanced dry-run tests ---


def test_dry06_skip_predicted_for_existing_archive():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 12, 31), 200)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 0}
            return m
        m.first.return_value = {"count": 200}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        rep = eng.run(_table_config(delete_after_archive=False), dry_run=True, dbutils=dbutils)
    yr_data = rep["tables"][_table_config()["table_id"]]["years"][2018]
    assert yr_data["action"] == "SKIP"
    assert yr_data["would_archive"] == 0
    dry_calls = [c for c in audit.log_dry_run.call_args_list if c.kwargs]
    assert dry_calls[0].kwargs.get("action") == "SKIP"


def test_dry07_append_predicted_for_new_data():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 6, 15), 500)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 7}
            return m
        m.first.return_value = {"count": 600}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        rep = eng.run(_table_config(delete_after_archive=False), dry_run=True, dbutils=dbutils)
    yr_data = rep["tables"][_table_config()["table_id"]]["years"][2018]
    assert yr_data["action"] == "APPEND"
    assert yr_data["would_archive"] == 7
    dry_calls = [c for c in audit.log_dry_run.call_args_list if c.kwargs]
    assert dry_calls[0].kwargs.get("action") == "APPEND"


def test_dry08_error_predicted_for_orphan_folder():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in q:
            m.collect.return_value = [MagicMock(yr=2018)]
            return m
        m.first.return_value = {"count": 50}
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_folder_exists", return_value=True):
        rep = eng.run(_table_config(), dry_run=True, dbutils=dbutils)
    yr_data = rep["tables"][_table_config()["table_id"]]["years"][2018]
    assert yr_data["action"] == "ERROR"
    assert yr_data["would_archive"] == 0
    assert "no successful archive is recorded" in (yr_data.get("error_message") or "")

