import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import ArchiveError, ArchiveOperationError, ArchiveVerificationError
from src.utils import RunContext


@pytest.fixture(autouse=True)
def _default_archive_delta_version():
    # Bug-A fix (2026-04-27): _archive_table_year now calls
    # get_archive_delta_version after a successful write and raises
    # version_capture_failed when it returns None. Existing success-path tests
    # don't bother stubbing DESCRIBE HISTORY queries, so default to a fixed
    # version here. Tests that need to override (e.g. the version-capture
    # regression tests below) can stack their own patch.
    with patch("src.archiver.get_archive_delta_version", return_value=0):
        yield


def _global_settings():
    return {
        "audit_catalog": "gcat",
        "audit_schema": "gaudit",
        "default_retention_years": 7,
        "archive_base_path_prefix": "abfss://x/",
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
            job_context={"workspace_id": "ws1"},
            archive_run_id="run-a",
        )
    if audit is None:
        audit = MagicMock()
        audit.has_verify_failed.return_value = False
        audit.get_last_archived_record_count.return_value = None
    if spark is None:
        spark = MagicMock()
    return ArchiveEngine(ctx, audit, spark), ctx, audit, spark


def test_resolve_exclusion_no_strip_alias_kwarg():
    """The strip_alias parameter is dead after the alias unification fix —
    no remaining caller needs it. Asserting the kwarg is gone prevents
    silent reintroduction of the bug."""
    import inspect
    from src.archiver import _resolve_exclusion

    sig = inspect.signature(_resolve_exclusion)
    assert "strip_alias" not in sig.parameters, (
        f"_resolve_exclusion still accepts strip_alias: {list(sig.parameters)}"
    )
    assert _resolve_exclusion("") == "1=1"
    assert _resolve_exclusion("src.x = 1") == "src.x = 1"


def test_arc01_init_stores_ctx_audit_spark():
    from src.archiver import ArchiveEngine

    ctx = RunContext(settings=_global_settings(), job_context={}, archive_run_id="r")
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


def test_arc03_create_year_generates_write_sql():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 42}
    tc = _table_config()
    exclusion = "NOT (src.status = 'Active')"
    archive_path = "abfss://stor/archive/claims/year_2020"
    eng._create_year(tc, 2020, exclusion)
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    create_sql = next(s for s in sqls if "CREATE TABLE" in s and "USING DELTA" in s)
    assert f"delta.`{archive_path}`" in create_sql
    assert "src_cat.src_sch.claims" in create_sql.replace("\n", " ")
    assert "YEAR(src.claim_date) = 2020" in create_sql.replace("\n", " ")
    assert "src.claim_date IS NOT NULL" in create_sql.replace("\n", " ")
    assert exclusion in create_sql


def test_arc03b_count_after_create_uses_archive_unfiltered():
    """After the CTAS, the archived-row count must read delta.`path` aliased
    as `src` with NO exclusion predicate — the archive folder by construction
    holds only rows that survived exclusion, so re-applying the predicate to
    the read is what broke custom_sql in 08T."""
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 5}
    tc = _table_config()
    clause = "NOT (EXISTS (SELECT 1 FROM src_cat.src_sch.claims c WHERE c.provider_id = src.provider_id))"
    eng._create_year(tc, 2020, clause)

    sqls = [c[0][0] for c in spark.sql.call_args_list]
    count_sql = next(s for s in sqls if "SELECT COUNT(*)" in s and "delta.`" in s)
    flat = count_sql.replace("\n", " ")
    assert "FROM delta.`abfss://stor/archive/claims/year_2020` src" in flat
    assert "YEAR(src.claim_date) = 2020" in flat
    assert "src.claim_date IS NOT NULL" in flat
    # Predicate must NOT appear in the count read against the archive folder.
    assert "EXISTS" not in count_sql
    assert "NOT (" not in count_sql
    assert "src_cat.src_sch.claims" not in count_sql


def test_arc04_exclusion_conditions_applied():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 1}
    tc = _table_config()
    clause = "NOT (src.x = 1) AND NOT (src.y = 2)"
    eng._create_year(tc, 2021, clause)
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    create_sql = next(s for s in sqls if "CREATE TABLE" in s and "USING DELTA" in s)
    count_sql = next(s for s in sqls if "SELECT COUNT(*)" in s and "delta.`" in s)
    # Predicate appears only in the source-side write.
    assert clause in create_sql
    assert clause not in count_sql
    assert "src.x = 1" not in count_sql
    assert "src.y = 2" not in count_sql


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


def test_arc05b_verify_runs_source_side_count_with_predicate():
    """_verify_archive must read the source FQN with the aliased exclusion
    clause intact (no strip_alias). Comparing two equally-broken archive
    counts is what hid the custom_sql bug in 08T."""
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 7}
    tc = _table_config()
    clause = "NOT (EXISTS (SELECT 1 FROM src_cat.src_sch.claims c WHERE c.provider_id = src.provider_id))"
    eng._verify_archive(tc, 2020, 7, clause)

    sqls = [c[0][0] for c in spark.sql.call_args_list]
    count_sql = next(s for s in sqls if "SELECT COUNT(*)" in s)
    flat = count_sql.replace("\n", " ")
    assert "FROM src_cat.src_sch.claims src" in flat
    assert "YEAR(src.claim_date) = 2020" in flat
    assert "src.claim_date IS NOT NULL" in flat
    # Predicate must be intact with the src. alias (no strip).
    assert clause in count_sql
    # Verify did NOT read the archive folder.
    assert "delta.`" not in count_sql


def test_arc06_delete_archived_generates_delete_sql():
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    tc = _table_config()
    clause = "NOT (src.status = 'Active')"
    eng._delete_archived(
        tc, 2020, clause, date(2020, 2, 1), date(2020, 11, 30)
    )
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    del_sql = sqls[-1]
    flat = del_sql.replace("\n", " ")
    assert "DELETE FROM src_cat.src_sch.claims src" in flat
    assert "YEAR(src.claim_date) = 2020" in flat
    assert "src.claim_date >= DATE '2020-02-01'" in flat
    assert "src.claim_date <= DATE '2020-11-30'" in flat
    assert "src.claim_date IS NOT NULL" in flat
    assert clause in del_sql


def test_arc06b_delete_archived_keeps_aliased_predicate():
    """DELETE FROM <fq> src ... predicate must keep the src. alias intact
    so custom_sql correlated subqueries (e.g. NOT (EXISTS (... = src.id)))
    bind to the outer source row."""
    eng, _, _, spark = _make_engine()
    spark.sql.return_value = MagicMock()
    tc = _table_config()
    clause = "NOT (EXISTS (SELECT 1 FROM src_cat.src_sch.other o WHERE o.id = src.claim_id))"
    eng._delete_archived(
        tc, 2020, clause, date(2020, 2, 1), date(2020, 11, 30)
    )
    sqls = [c[0][0] for c in spark.sql.call_args_list]
    del_sql = sqls[-1]
    flat = del_sql.replace("\n", " ")
    assert "DELETE FROM src_cat.src_sch.claims src" in flat
    assert "= src.claim_id" in flat
    assert "src.claim_date >= DATE '2020-02-01'" in flat
    assert "src.claim_date <= DATE '2020-11-30'" in flat
    assert "src.claim_date IS NOT NULL" in flat
    assert clause in del_sql


def test_arc07_watermark_window_uses_archive_unfiltered():
    """_run_watermark_window reads delta.`path` aliased as `src` with NO
    exclusion predicate. Same alias bug as the count read."""
    eng, _, _, spark = _make_engine()
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: date(2020, 1, 1) if k == "lo" else date(2020, 12, 31)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = mock_row
    tc = _table_config()
    eng._run_watermark_window(tc, 2020, after_watermark=None)

    sqls = [c[0][0] for c in spark.sql.call_args_list]
    wm_sql = next(s for s in sqls if "MIN(" in s and "MAX(" in s)
    flat = wm_sql.replace("\n", " ")
    assert "FROM delta.`abfss://stor/archive/claims/year_2020` src" in flat
    assert "YEAR(src.claim_date) = 2020" in flat
    assert "src.claim_date IS NOT NULL" in flat
    assert "EXISTS" not in wm_sql
    assert "NOT (" not in wm_sql


def test_arc07b_watermark_window_appends_after_watermark_clip():
    """When after_watermark is given (APPEND case), the SQL must add a
    'src.<wm> > <date>' clip so the window covers only this run's rows."""
    eng, _, _, spark = _make_engine()
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: date(2020, 6, 1) if k == "lo" else date(2020, 12, 31)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = mock_row
    tc = _table_config()
    eng._run_watermark_window(tc, 2020, after_watermark=date(2020, 5, 31))

    sqls = [c[0][0] for c in spark.sql.call_args_list]
    wm_sql = next(s for s in sqls if "MIN(" in s and "MAX(" in s)
    assert "src.claim_date > DATE '2020-05-31'" in wm_sql.replace("\n", " ")


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
        if "MIN(" in qs and "MAX(" in qs and "delta.`" in qs:
            m.first.return_value = {"lo": date(2019, 1, 1), "hi": date(2019, 12, 31)}
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
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


def test_arc12_append_mode_year_exists_delete_true():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 6, 15), 500)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.side_effect = [False, True, True]

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
    # committed_rows=0 so archived - committed equals the source-side count (7);
    # record_count is not asserted by this test so the simpler math is fine.
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
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


def test_append_audit_record_count_is_delta_not_total():
    """APPEND must audit record_count as rows-this-run (delta) not archive total.

    Pre-write archive count = 100, post-write = 120 -> record_count = 20 on
    both ARCHIVED and ARCHIVED_AND_DELETED rows. Locks in the semantic that
    notebooks, dashboards, and 29T expect.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 6, 15), 100)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.side_effect = [False, True, True]

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
            # Source-side count of rows past the watermark with the exclusion;
            # equals the delta this run added (post-INSERT archive 120 - committed 100).
            m.first.return_value = {"count": 20}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 800}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 120}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 12, 31)}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith(("INSERT", "DELETE")):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 100)):
        result = eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)

    archived_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED"]
    assert archived_kw, "expected an ARCHIVED audit row"
    assert archived_kw[-1].get("record_count") == 20, archived_kw[-1]
    assert archived_kw[-1].get("archive_mode") == "APPEND"

    deleted_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED_AND_DELETED"]
    assert deleted_kw, "expected an ARCHIVED_AND_DELETED audit row"
    assert deleted_kw[-1].get("record_count") == 20, deleted_kw[-1]

    tid = _table_config()["table_id"]
    assert result["tables"][tid]["years"][2018]["record_count"] == 20


def test_create_audit_record_count_unchanged_when_archive_was_empty():
    """CREATE branch: committed_rows=0, so rows_this_run == archive total.

    Guards against accidentally regressing CREATE's audit value when we
    refactored the delta calculation.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.return_value = True

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
            m.first.return_value = {"count": 42}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 800}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 42}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": date(2018, 12, 31)}
            return m
        if q.strip().upper().startswith(("CREATE", "INSERT", "DELETE")):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
        eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)

    archived_kw = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED"]
    assert archived_kw and archived_kw[-1].get("record_count") == 42


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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
        if "MIN(" in qs and "MAX(" in qs and "delta.`" in qs:
            m.first.return_value = {"lo": date(2015, 1, 1), "hi": date(2015, 12, 31)}
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    eng._create_year(tc, 2020, "")
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
        eng.run(_table_config(), dry_run=True, dbutils=dbutils)
    dry_calls = [c for c in audit.log_dry_run.call_args_list if c.kwargs]
    assert dry_calls
    pc = dry_calls[0].kwargs.get("per_condition_counts") or {}
    assert "active_only" in pc
    assert dry_calls[0].kwargs.get("action") == "WOULD_ARCHIVE"


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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
        eng._create_year(_table_config(), 2020, "NOT (1=1)")
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
        eng._create_year(_table_config(), 2020, "")
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
            # Source-side count must match the post-INSERT delta (10 - 0 committed).
            m.first.return_value = {"count": 10}
            return m
        r = _claims_count_sql_handler(qs, m)
        if r is not None:
            return r
        if "INSERT INTO" in q and "delta.`" in q:
            m.first.return_value = {"count": 10}
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
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

    def folder_side(_spark, base, table_name, year):
        return ("VALID", 0) if year == 2019 else ("MISSING", 0)

    with patch("src.archiver.archive_state_and_count", side_effect=folder_side):
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
        # Source-side verify count must match the post-CREATE archive count (12).
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 12}
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
        if "MIN(" in qs and "MAX(" in qs and "delta.`" in qs:
            m.first.return_value = {"lo": date(2018, 1, 1), "hi": date(2018, 12, 31)}
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    assert "orphan or corrupted folder" in str(ei.value)


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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    assert "orphan or corrupted folder" in str(ei.value)


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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
        eng.run(_table_config(), dry_run=False, dbutils=dbutils)
    statuses = [c.kwargs.get("status") for c in audit.log_archive.call_args_list if c.kwargs]
    assert "SKIPPED_CONCURRENT" in statuses
    create_sqls = [c[0][0] for c in spark.sql.call_args_list if "CREATE TABLE" in c[0][0] and "USING DELTA AS" in c[0][0]]
    assert not create_sqls


def test_uc6_stale_threshold_passed_from_settings():
    settings = _global_settings()
    settings["stale_started_threshold_hours"] = 8
    ctx = RunContext(settings=settings, job_context={"workspace_id": "ws1"}, archive_run_id="run-a")
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
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
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
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="MISSING", committed_rows=0,
    )
    assert result["action"] == "CREATE"
    assert result["would_archive"] == 50
    assert result["archive_state"] == "MISSING"
    assert result["committed_rows"] == 0
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
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=100,
    )
    assert result["action"] == "SKIP"
    assert result["would_archive"] == 0
    assert result["archive_state"] == "VALID"
    assert result["committed_rows"] == 100
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
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=100,
    )
    assert result["action"] == "APPEND"
    assert result["would_archive"] == 5
    assert result["archive_state"] == "VALID"
    assert result["committed_rows"] == 100


def test_resolve04_missing_folder_after_delete_returns_error():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED_AND_DELETED", date(2020, 12, 31), 100)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 50}

    tc = _table_config()
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="MISSING", committed_rows=0,
    )
    assert result["action"] == "ERROR"
    assert result["error_reason"] == "missing_folder_after_delete"


def test_resolve05_orphan_folder_returns_error():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 50}

    tc = _table_config()
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=42,
    )
    assert result["action"] == "ERROR"
    assert result["error_reason"] == "archive_folder_orphan"


def test_resolve06_resume_delete():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    audit.is_archived_by_run.return_value = True
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 100}

    tc = _table_config(delete_after_archive=True)
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=100,
    )
    assert result["action"] == "RESUME_DELETE"


def test_resolve07_orphan_state_raises_error_regardless_of_last_status():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 100}

    tc = _table_config()
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="ORPHAN", committed_rows=0,
    )
    assert result["action"] == "ERROR"
    assert result["error_reason"] == "archive_folder_orphan"


def test_resolve08_valid_with_archived_and_deleted_returns_skip():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED_AND_DELETED", date(2020, 12, 31), 100)
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 0}

    tc = _table_config()
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=100,
    )
    assert result["action"] == "SKIP"
    assert result["error_reason"] is None


def test_resolve09_cross_run_archived_does_not_resume_delete():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    audit.is_archived_by_run.return_value = False

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 0}
            return m
        m.first.return_value = {"count": 100}
        return m

    spark.sql.side_effect = sql_side_effect

    tc = _table_config(delete_after_archive=True)
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=100,
    )
    assert result["action"] in ("SKIP", "APPEND")
    assert result["action"] != "RESUME_DELETE"


def test_resolve10_cross_run_archived_with_new_rows_appends():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 6, 15), 100)
    audit.is_archived_by_run.return_value = False

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 7}
            return m
        m.first.return_value = {"count": 200}
        return m

    spark.sql.side_effect = sql_side_effect

    tc = _table_config(delete_after_archive=True)
    result = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=100,
    )
    assert result["action"] == "APPEND"
    assert result["would_archive"] == 7


def test_resolve11_archive_folder_orphan_template_has_no_dbutils_fs_ls():
    templates = ArchiveError._DIAGNOSTIC_TEMPLATES["FAILED"]
    template = templates["archive_folder_orphan"]
    assert "dbutils.fs.ls" not in template
    assert "dbutils" not in template.lower()


def test_resolve12_missing_folder_after_delete_template_unchanged():
    templates = ArchiveError._DIAGNOSTIC_TEMPLATES["FAILED"]
    template = templates["missing_folder_after_delete"]
    assert "{table}" in template
    assert "{year}" in template
    assert "{path}" in template
    assert "source rows already removed" in template


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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
        rep = eng.run(_table_config(delete_after_archive=False), dry_run=True, dbutils=dbutils)
    yr_data = rep["tables"][_table_config()["table_id"]]["years"][2018]
    assert yr_data["action"] == "APPEND"
    assert yr_data["would_archive"] == 7
    dry_calls = [c for c in audit.log_dry_run.call_args_list if c.kwargs]
    assert dry_calls[0].kwargs.get("action") == "WOULD_APPEND"


def test_dry09_permission_denied_logs_failed_before_raising():
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
    perm_error = Exception("PERMISSION_DENIED: User does not have READ VOLUME on '/Volumes/dev2_archive/...'")
    with patch("src.archiver.archive_state_and_count", side_effect=perm_error):
        with pytest.raises(Exception, match="PERMISSION_DENIED"):
            eng.run(_table_config(), dry_run=True, dbutils=dbutils)
    failed_calls = [
        c.kwargs for c in audit.log_archive.call_args_list
        if c.kwargs.get("status") == "FAILED"
    ]
    assert len(failed_calls) == 1
    assert failed_calls[0]["table"] == "healthcare.claims.member"
    assert failed_calls[0]["year"] == 2018
    assert "PERMISSION_DENIED" in failed_calls[0]["error_message"]
    audit.log_dry_run.assert_not_called()


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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
        rep = eng.run(_table_config(), dry_run=True, dbutils=dbutils)
    yr_data = rep["tables"][_table_config()["table_id"]]["years"][2018]
    assert yr_data["action"] == "ERROR"
    assert yr_data["would_archive"] == 0
    assert "orphan or corrupted folder" in (yr_data.get("error_message") or "")


def test_delete_blocked_when_not_owned():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 1, 1), 100)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.side_effect = [True, False]

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
            m.collect.return_value = [MagicMock(yr=2020)]
            return m
        if "FROM delta.`" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 100}
            return m
        if "archive_audit_log" in q and "record_count" in qs and "ARCHIVED" in q:
            m.collect.return_value = [{"record_count": 100}]
            return m
        if "COUNT(*)" in qs and "src_cat.src_sch.claims" in qs and " src" in qs:
            m.first.return_value = {"count": 100}
            return m
        if q.strip().upper().startswith("DELETE"):
            m.first.return_value = {"count": 0}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 100)):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    assert ei.value.reason == "ownership"
    assert "does not own" in str(ei.value).lower()
    delete_sqls = [
        c[0][0] for c in spark.sql.call_args_list if c[0][0].strip().upper().startswith("DELETE")
    ]
    assert not delete_sqls
    audit.is_archived_by_run.assert_called()


def test_delete_sql_failure_maps_to_operation_error():
    eng, _, _, spark = _make_engine()
    spark.sql.side_effect = RuntimeError("delete exploded")

    with pytest.raises(ArchiveOperationError) as ei:
        eng._delete_archived(
            _table_config(), 2020, "1=1", date(2020, 1, 1), date(2020, 12, 31)
        )
    assert ei.value.operation == "delete"
    assert ei.value.reason == "operation_failure"


def test_silent_year_drop_is_hard_failure():
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
            m.collect.return_value = [{"yr": "not_a_year"}]
            return m
        return m

    spark.sql.side_effect = sql_side_effect
    tc = _table_config(retention_years=5)
    with pytest.raises(ArchiveOperationError) as ei:
        eng._calculate_eligible_years(tc, 5)
    assert ei.value.operation == "calculate_eligible_years"


def test_resume_delete_raises_on_source_drift():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 1, 1), 100)
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
            m.collect.return_value = [MagicMock(yr=2020)]
            return m
        if "FROM delta.`" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 100}
            return m
        if "archive_audit_log" in q and "record_count" in qs and "ARCHIVED" in q:
            m.collect.return_value = [{"record_count": 100}]
            return m
        if (
            "COUNT(*)" in qs
            and "src_cat.src_sch.claims" in qs
            and " src" in qs
            and "NOT (src.status" in qs
        ):
            m.first.return_value = {"count": 77}
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
        with pytest.raises(ArchiveVerificationError) as ei:
            eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    assert ei.value.reason == "source_drift"
    audit.log_verify_failed.assert_called_once()


def test_skip_vs_error_when_watermark_none():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", None, 100)

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "COUNT(*)" in qs and "src_cat.src_sch.claims" in qs and " src" not in qs:
            m.first.return_value = {"count": 100}
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 0}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": None}
            return m
        m.first.return_value = {"count": 0}
        return m

    spark.sql.side_effect = sql_side_effect
    tc = _table_config(delete_after_archive=False)
    res_true = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="VALID", committed_rows=100,
    )
    assert res_true["action"] == "ERROR"
    assert res_true["error_reason"] == "cannot_determine_incremental_position"

    spark.sql.side_effect = None
    spark.sql.return_value = MagicMock()
    spark.sql.return_value.first.return_value = {"count": 50}
    res_false = eng._resolve_year_action(
        tc, 2020, "1=1", archive_state="MISSING", committed_rows=0,
    )
    assert res_false["action"] == "CREATE"


def test_verify_failed_refuses_append_on_retry():
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 6, 15), 500)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.has_verify_failed.return_value = True

    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "VERIFY_FAILED" in qs and "LIMIT 1" in qs:
            m.collect.return_value = [MagicMock()]
            return m
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
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 800}
            return m
        if "INSERT INTO" in q and "delta.`" in q:
            m.first.return_value = {"count": 3}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": 3}
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    assert ei.value.reason == "verify_failed_requires_reconcile"
    assert "verify-failed.md" in str(ei.value)
    insert_sqls = [c[0][0] for c in spark.sql.call_args_list if "INSERT INTO" in c[0][0] and "delta.`" in c[0][0]]
    assert not insert_sqls


def test_archive_side_has_no_committed_rows_reference():
    """R10: archive path helpers must not reference a committed_rows parameter
    or variable (a dead branch that previously re-fetched the archive count).
    Walks ArchiveBase + ArchiveEngine + DeleteJob and only inspects each method
    on the class that owns it (so the source-of-truth for _delete_archived,
    which now lives on ArchiveBase, is checked exactly once).
    """
    import inspect

    from src.archiver import ArchiveBase, ArchiveEngine
    from src.delete_job import DeleteJob

    checked = 0
    for cls in (ArchiveBase, ArchiveEngine, DeleteJob):
        for name in ("_delete_archived", "_delete_archived_year"):
            fn = getattr(cls, name, None)
            base_fn = getattr(ArchiveBase, name, None)
            if fn is None:
                continue
            # Skip when the subclass merely inherits the base implementation.
            if cls is not ArchiveBase and fn is base_fn:
                continue
            src = inspect.getsource(fn)
            assert "committed_rows" not in src, (
                f"{cls.__name__}.{name} must not reference committed_rows"
            )
            checked += 1
    assert checked >= 1, "expected at least _delete_archived to exist on ArchiveBase"


def test_resolve13_cross_run_append_delete_scopes_to_new_rows_only():
    """R30: APPEND on top of a prior foreign-run ARCHIVED (delete_after_archive=true)
    deletes only rows this run just wrote (wm > prior watermark). Prior-run rows
    below the watermark must remain in the source.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2019, 6, 15), 500)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.side_effect = [False, True, True]

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
        if "MIN(" in qs and "MAX(" in qs and "delta.`" in qs:
            m.first.return_value = {"lo": date(2019, 7, 1), "hi": date(2019, 12, 31)}
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 7}
            return m
        if "COUNT(" in qs and "src_cat.src_sch.claims" in qs and "delta" not in qs and "archive_audit" not in qs:
            if " IS NULL" in qs:
                m.first.return_value = {"count": 0}
                return m
            m.first.return_value = {"count": 507}
            return m
        if "COUNT(" in qs and "delta.`" in qs:
            m.first.return_value = {"count": 507}
            return m
        if "CAST(MAX(" in qs:
            m.first.return_value = {"wm": date(2019, 12, 31)}
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
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 500)):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)

    delete_sqls = [
        c[0][0]
        for c in spark.sql.call_args_list
        if c[0][0].strip().upper().startswith("DELETE")
    ]
    assert delete_sqls, "same-run APPEND+delete must issue a DELETE"
    del_flat = delete_sqls[-1].replace("\n", " ")
    assert "YEAR(src.claim_date) = 2019" in del_flat
    assert "src.claim_date >= DATE '2019-07-01'" in del_flat
    assert "src.claim_date <= DATE '2019-12-31'" in del_flat
    assert "src.claim_date IS NOT NULL" in del_flat
    assert "DATE '2019-06-15'" not in del_flat, (
        "DELETE must not touch rows at or below the prior-run watermark"
    )
    insert_sqls = [
        c[0][0]
        for c in spark.sql.call_args_list
        if "INSERT INTO" in c[0][0] and "delta.`" in c[0][0]
    ]
    assert insert_sqls, "APPEND must have been attempted before the delete"


def test_resolve14_same_run_resume_delete_uses_archive_watermark_window():
    """R31: Same-run ARCHIVED + RESUME_DELETE still issues a bounded DELETE (this
    run's archive watermark window). Regression guard for the new predicate shape.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 12, 31), 100)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.return_value = True

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
        if "MIN(" in qs and "MAX(" in qs and "delta.`" in qs:
            m.first.return_value = {"lo": date(2018, 2, 1), "hi": date(2018, 12, 31)}
            return m
        if "FROM delta.`" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 100}
            return m
        if "archive_audit_log" in q and "record_count" in qs and "ARCHIVED" in q:
            m.collect.return_value = [{"record_count": 100}]
            return m
        if "COUNT(*)" in qs and "src_cat.src_sch.claims" in qs and " src" in qs:
            m.first.return_value = {"count": 100}
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 100)):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)

    delete_sqls = [
        c[0][0]
        for c in spark.sql.call_args_list
        if c[0][0].strip().upper().startswith("DELETE")
    ]
    assert delete_sqls
    del_flat = delete_sqls[-1].replace("\n", " ")
    assert "YEAR(src.claim_date) = 2018" in del_flat
    assert "src.claim_date >= DATE '2018-02-01'" in del_flat
    assert "src.claim_date <= DATE '2018-12-31'" in del_flat
    archive_writes = [
        c[0][0]
        for c in spark.sql.call_args_list
        if ("INSERT INTO" in c[0][0] and "delta.`" in c[0][0])
        or ("CREATE TABLE" in c[0][0] and "USING DELTA" in c[0][0])
    ]
    assert not archive_writes, "RESUME_DELETE must not re-archive"


def test_resume_delete_drift_never_writes_failed_count_mismatch():
    """R32: Drift during RESUME_DELETE writes VERIFY_FAILED / source_drift. It must
    never reach the archive audit as FAILED with reason=count_mismatch (which is
    reserved for post-archive self-verify failure, not source-side drift).
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 1, 1), 100)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_last_archived_record_count.return_value = 100

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
            m.collect.return_value = [MagicMock(yr=2020)]
            return m
        if "FROM delta.`" in qs and "COUNT" in qs:
            m.first.return_value = {"count": 100}
            return m
        if "COUNT(*)" in qs and "src_cat.src_sch.claims" in qs and " src" in qs:
            m.first.return_value = {"count": 77}
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m

    spark.sql.side_effect = sql_side_effect
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)):
        with pytest.raises(ArchiveVerificationError) as ei:
            eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    assert ei.value.reason == "source_drift"
    audit.log_verify_failed.assert_called_once()
    failed_count_mismatch = [
        c
        for c in audit.log_archive.call_args_list
        if c.kwargs.get("status") == "FAILED"
        and (
            "count_mismatch" in str(c.kwargs.get("error_message") or "")
            or c.kwargs.get("reason") == "count_mismatch"
        )
    ]
    assert not failed_count_mismatch, (
        "drift must not be audited as FAILED/count_mismatch; use VERIFY_FAILED/source_drift"
    )


def _archive_success_sql_side_effect(year, claims_count=55, watermark=None):
    """Shared sql_side_effect for archive-success-path tests.

    Mirrors the shape used by test_wm01/test_wm02 — handles current-year, year
    enumeration, count probes, watermark probes, and the audit double-check.
    """
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
            m.collect.return_value = [MagicMock(yr=year)]
            return m
        if "src_cat.src_sch.claims src" in qs and "COUNT" in qs:
            m.first.return_value = {"count": claims_count}
            return m
        if "COUNT(" in q and "delta" in q:
            m.first.return_value = {"count": claims_count}
            return m
        if "CAST(MAX(" in qs or ("MAX(claim_date)" in qs and "delta`" in qs):
            m.first.return_value = {"wm": watermark or date(year, 12, 1)}
            return m
        if "INSERT INTO" in q and "delta.`" in q:
            m.first.return_value = {"count": claims_count}
            return m
        if "archive_audit_log" in q and "ARCHIVED" in q and "run-a" in q:
            m.collect.return_value = [MagicMock()]
            return m
        if q.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m
    return sql_side_effect


def test_archive_create_records_archive_delta_version():
    """Bug A: CREATE flow captures the post-write Delta version and stamps it
    onto both the ARCHIVED and ARCHIVED_AND_DELETED audit rows.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.return_value = True
    spark.sql.side_effect = _archive_success_sql_side_effect(2018, claims_count=55)
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)), \
         patch("src.archiver.get_archive_delta_version", return_value=5):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    archived = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED"]
    deleted = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED_AND_DELETED"]
    assert archived, "expected an ARCHIVED audit row"
    assert deleted, "expected an ARCHIVED_AND_DELETED audit row (delete_after_archive=True)"
    assert all(k.get("archive_mode") == "CREATE" for k in archived)
    assert all(k.get("archive_delta_version") == 5 for k in archived)
    assert all(k.get("archive_delta_version") == 5 for k in deleted)


def test_archive_append_records_archive_delta_version():
    """Bug A: APPEND flow captures the post-write Delta version on the same two
    audit rows as the CREATE flow.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2018, 6, 15), 500)
    audit.check_concurrent.return_value = (False, False, None, None)
    # First call (resolver) returns False so we get APPEND, not RESUME_DELETE.
    # Subsequent calls (post-archive ownership check) return True so the
    # delete-after path proceeds to log ARCHIVED_AND_DELETED.
    audit.is_archived_by_run.side_effect = [False, True, True]
    spark.sql.side_effect = _archive_success_sql_side_effect(2018, claims_count=10)
    spark.conf = MagicMock()
    dbutils = MagicMock()
    # committed_rows=0 keeps archived(10) - committed(0) == source-side count(10);
    # this test asserts on archive_mode/version, not record_count.
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)), \
         patch("src.archiver.get_archive_delta_version", return_value=12):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)
    archived = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED"]
    deleted = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED_AND_DELETED"]
    assert archived, "expected an ARCHIVED audit row"
    assert deleted, "expected an ARCHIVED_AND_DELETED audit row"
    assert all(k.get("archive_mode") == "APPEND" for k in archived)
    assert all(k.get("archive_delta_version") == 12 for k in archived)
    assert all(k.get("archive_delta_version") == 12 for k in deleted)


def test_archive_raises_when_version_capture_returns_none():
    """Bug A: a None return from get_archive_delta_version halts the run with
    version_capture_failed; a FAILED audit row is written and no ARCHIVED row
    is logged. Prevents the NULL archive_delta_version that broke rollback in
    38R Run 2026-04-27.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.check_concurrent.return_value = (False, False, None, None)
    spark.sql.side_effect = _archive_success_sql_side_effect(2018, claims_count=55)
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)), \
         patch("src.archiver.get_archive_delta_version", return_value=None):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(_table_config(delete_after_archive=False), dry_run=False, dbutils=dbutils)
    assert ei.value.reason == "version_capture_failed"
    failed = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "FAILED"]
    archived = [c.kwargs for c in audit.log_archive.call_args_list if c.kwargs.get("status") == "ARCHIVED"]
    assert failed, "expected a FAILED audit row when version capture returns None"
    assert not archived, "must not log an ARCHIVED row when version capture failed"
    error_message = failed[-1].get("error_message") or ""
    assert "version probe returned None" in error_message
    assert "archive_delta_version" in error_message


def test_create_with_delete_after_uses_full_year_window():
    """CREATE+DAA recovery path must call _run_watermark_window with
    after_watermark=None so the source DELETE covers the entire year that
    CREATE just wrote, not only rows past the prior run's high watermark.

    Regression for the archiver-create-skips-source-delete defect surfaced
    by 36T phase 2e: under MISSING+ARCHIVED+DAA recovery, passing
    after_watermark=effective_wm produced a wm > effective_wm filter that
    matched zero rows in a freshly-written CREATE archive, silently no-oping
    the source DELETE while still logging ARCHIVED_AND_DELETED.
    """
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.return_value = True

    eng._run_watermark_window = MagicMock(
        return_value=(date(2020, 1, 1), date(2020, 12, 31))
    )

    spark.sql.side_effect = _archive_success_sql_side_effect(2020, claims_count=163)
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)), \
         patch("src.archiver.get_archive_delta_version", return_value=7):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)

    assert eng._run_watermark_window.call_count == 1, (
        "expected exactly one _run_watermark_window call from the delete branch"
    )
    call_kwargs = eng._run_watermark_window.call_args.kwargs
    assert call_kwargs.get("after_watermark") is None, (
        f"CREATE+DAA must pass after_watermark=None; got {call_kwargs!r}"
    )

    delete_sqls = [
        c[0][0]
        for c in spark.sql.call_args_list
        if c[0][0].strip().upper().startswith("DELETE")
    ]
    assert delete_sqls, (
        "CREATE+DAA must issue a source DELETE; the silent no-op was the bug"
    )

    deleted = [
        c.kwargs
        for c in audit.log_archive.call_args_list
        if c.kwargs.get("status") == "ARCHIVED_AND_DELETED"
    ]
    assert deleted, "expected an ARCHIVED_AND_DELETED audit row"
    assert deleted[-1].get("archive_mode") == "CREATE"


def test_append_with_delete_after_uses_effective_watermark():
    """APPEND+DAA must keep passing after_watermark=effective_wm so the
    delete window covers only the slice this run appended, not older rows
    written by prior runs that already live in the archive.

    Pin for the option-A fix's APPEND branch — guards against the new
    per-action mapping accidentally inverting the APPEND case.
    """
    effective_wm = date(2018, 6, 15)
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = ("ARCHIVED", effective_wm, 500)
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.side_effect = [False, True]

    eng._run_watermark_window = MagicMock(
        return_value=(date(2018, 7, 1), date(2018, 12, 31))
    )

    spark.sql.side_effect = _archive_success_sql_side_effect(2018, claims_count=20)
    spark.conf = MagicMock()
    dbutils = MagicMock()
    # committed_rows=0 keeps archived(20) - committed(0) == source-side count(20);
    # this test asserts on after_watermark plumbing, not record_count.
    with patch("src.archiver.archive_state_and_count", return_value=("VALID", 0)), \
         patch("src.archiver.get_archive_delta_version", return_value=11):
        eng.run(_table_config(delete_after_archive=True), dry_run=False, dbutils=dbutils)

    assert eng._run_watermark_window.call_count == 1
    call_kwargs = eng._run_watermark_window.call_args.kwargs
    assert call_kwargs.get("after_watermark") == effective_wm, (
        f"APPEND+DAA must pass after_watermark={effective_wm!r}; got {call_kwargs!r}"
    )

    archived = [
        c.kwargs
        for c in audit.log_archive.call_args_list
        if c.kwargs.get("status") == "ARCHIVED"
    ]
    assert any(k.get("archive_mode") == "APPEND" for k in archived)


def test_unexpected_action_raises_in_delete_branch():
    """Future-enum guardrail: if a new action enum reaches the delete branch
    without being added to the per-action mapping, fail loud rather than
    inheriting the buggy "treat as APPEND" default that produced the
    CREATE-skips-delete defect.
    """
    eng, _, audit, spark = _make_engine()
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.is_archived_by_run.return_value = True

    eng._resolve_year_action = MagicMock(return_value={
        "source_year_count": 42,
        "archive_state": "MISSING",
        "committed_rows": 0,
        "last_status": "ARCHIVED",
        "last_watermark": date(2020, 12, 31),
        "last_count": 100,
        "would_archive": 42,
        "error_message": None,
        "error_reason": None,
        "action": "PHANTOM",
    })
    eng._run_watermark_window = MagicMock()

    spark.sql.side_effect = _archive_success_sql_side_effect(2020, claims_count=42)
    spark.conf = MagicMock()
    dbutils = MagicMock()
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)), \
         patch("src.archiver.get_archive_delta_version", return_value=9):
        with pytest.raises(ArchiveOperationError) as ei:
            eng.run(
                _table_config(delete_after_archive=True),
                dry_run=False,
                dbutils=dbutils,
            )

    assert ei.value.reason == "invalid_action"
    assert "PHANTOM" in str(ei.value)
    assert eng._run_watermark_window.call_count == 0, (
        "delete branch must raise before computing the watermark window for "
        "an unrecognized action"
    )


def _delete_engine(archive_run_id="run-del"):
    """Factory for delete-job tests with a stub audit and spark."""
    ctx = RunContext(
        settings=_global_settings(),
        job_context={"workspace_id": "ws1"},
        archive_run_id=archive_run_id,
    )
    audit = MagicMock()
    audit.has_verify_failed.return_value = False
    audit.get_last_archived_record_count.return_value = None
    spark = MagicMock()
    spark.conf = MagicMock()
    from src.delete_job import DeleteJob
    eng = DeleteJob(ctx, audit, spark)
    return eng, ctx, audit, spark


def _install_delete_sql_mock(spark, source_count=100, watermark=date(2020, 12, 31)):
    """Wire spark.sql to return sensible defaults for delete-job queries."""
    def sql_side_effect(q):
        m = MagicMock()
        qs = q.replace("\n", " ")
        if "YEAR(current_date())" in qs:
            fr = MagicMock()
            fr.__getitem__ = lambda self, k: 2026 if k == "y" else None
            fr.__contains__ = lambda self, k: k == "y"
            m.first.return_value = fr
            return m
        if "DISTINCT YEAR" in qs:
            m.collect.return_value = [MagicMock(yr=2020)]
            return m
        if "MIN(" in qs and "MAX(" in qs and "delta.`" in qs:
            m.first.return_value = {"lo": date(2020, 1, 1), "hi": watermark}
            return m
        if "SELECT COUNT(*)" in qs and " src" in qs:
            m.first.return_value = {"count": source_count}
            return m
        if qs.strip().upper().startswith("DELETE"):
            return m
        m.first.return_value = {"count": 0}
        m.collect.return_value = []
        return m
    spark.sql.side_effect = sql_side_effect


def test_tdel01_success_writes_archived_and_deleted_row():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    audit.get_prior_archived_run.return_value = (
        "prior-run",
        datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
    )
    _install_delete_sql_mock(spark, source_count=100)
    with patch("src.delete_job.archive_row_count", return_value=100):
        report = eng.run(
            _table_config(), years=[2020], dry_run=False,
        )
    tid = "healthcare.claims.member"
    assert report["tables"][tid]["years"][2020]["status"] == "ARCHIVED_AND_DELETED"
    assert report["had_concurrent_failures"] is False
    audit.log_archive.assert_called_once()
    kw = audit.log_archive.call_args.kwargs
    assert kw["status"] == "ARCHIVED_AND_DELETED"
    assert kw["archive_mode"] == "DELETE"
    assert kw["archive_delta_version"] is None
    assert kw["needs_review"] is False
    assert "prior-run" in (kw["error_message"] or "")


def test_tdel02_d13_last_status_archived_and_deleted_writes_failed():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (False, "not_archived_state")
    audit.get_last_run_state.return_value = (
        "ARCHIVED_AND_DELETED", date(2020, 12, 31), 100,
    )
    _install_delete_sql_mock(spark)
    report = eng.run(
        _table_config(), years=[2020], dry_run=False,
    )
    tid = "healthcare.claims.member"
    assert report["tables"][tid]["years"][2020]["status"] == "FAILED"
    audit.log_archive.assert_called_once()
    kw = audit.log_archive.call_args.kwargs
    assert kw["status"] == "FAILED"
    assert kw["archive_mode"] is None
    assert kw["archive_delta_version"] is None
    assert kw["needs_review"] is True
    assert "ARCHIVED_AND_DELETED" in (kw["error_message"] or "")


def test_tdel03_d13_verify_failed_present_writes_failed():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (False, "verify_failed_present")
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    _install_delete_sql_mock(spark)
    report = eng.run(
        _table_config(), years=[2020], dry_run=False,
    )
    tid = "healthcare.claims.member"
    assert report["tables"][tid]["years"][2020]["reason"] == "verify_failed_present"
    audit.log_archive.assert_called_once()
    kw = audit.log_archive.call_args.kwargs
    assert kw["status"] == "FAILED"
    assert kw["archive_mode"] is None
    assert kw["needs_review"] is True


def test_tdel04_concurrent_run_writes_failed_for_every_scope_year():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (True, "foreign-run", 2019, 1.5)
    _install_delete_sql_mock(spark)
    report = eng.run(
        _table_config(), years=[2019, 2020, 2021], dry_run=False,
    )
    tid = "healthcare.claims.member"
    assert report["had_concurrent_failures"] is True
    for y in (2019, 2020, 2021):
        assert report["tables"][tid]["years"][y]["reason"] == "concurrent_run_on_table"
        assert report["tables"][tid]["years"][y]["status"] == "FAILED"
    assert audit.log_archive.call_count == 3
    for call in audit.log_archive.call_args_list:
        assert call.kwargs["status"] == "FAILED"
        assert call.kwargs["archive_mode"] is None
        assert call.kwargs["needs_review"] is True
        assert "foreign-run" in (call.kwargs["error_message"] or "")


def test_tdel05_drift_writes_verify_failed_not_failed():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    _install_delete_sql_mock(spark, source_count=120)
    with patch("src.delete_job.archive_row_count", return_value=100):
        report = eng.run(
            _table_config(), years=[2020], dry_run=False,
        )
    tid = "healthcare.claims.member"
    assert report["tables"][tid]["years"][2020]["status"] == "VERIFY_FAILED"
    assert report["tables"][tid]["years"][2020]["reason"] == "source_drift"
    audit.log_verify_failed.assert_called_once()
    kw = audit.log_verify_failed.call_args.kwargs
    assert kw["archive_count"] == 100
    assert kw["source_count"] == 120
    audit.log_archive.assert_not_called()


def test_tdel07_dry_run_emits_preview_rows_for_all_eligible():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    _install_delete_sql_mock(spark, source_count=100)
    with patch("src.delete_job.archive_row_count", return_value=100):
        report = eng.run(
            _table_config(), years=[2019, 2020], dry_run=True,
        )
    tid = "healthcare.claims.member"
    for y in (2019, 2020):
        assert report["tables"][tid]["years"][y]["action"] == "WOULD_DELETE"
    assert audit.log_dry_run.call_count == 2
    for call in audit.log_dry_run.call_args_list:
        assert call.kwargs["action"] == "WOULD_DELETE"


def test_tdel08_dry_run_would_delete_has_counts_and_no_terminal_row():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    _install_delete_sql_mock(spark, source_count=77)
    with patch("src.delete_job.archive_row_count", return_value=77):
        eng.run(
            _table_config(), years=[2020], dry_run=True,
        )
    audit.log_dry_run.assert_called_once()
    kw = audit.log_dry_run.call_args.kwargs
    assert kw["action"] == "WOULD_DELETE"
    assert kw["total_eligible"] == 77
    assert kw["would_archive"] == 77
    audit.log_archive.assert_not_called()
    audit.log_verify_failed.assert_not_called()
    delete_sqls = [
        c[0][0] for c in spark.sql.call_args_list
        if c[0][0].strip().upper().startswith("DELETE")
    ]
    assert not delete_sqls, "dry-run must never issue DELETE SQL"


def test_tdel09_d7_message_fallback_when_no_prior_archived_row():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    audit.get_prior_archived_run.return_value = None
    _install_delete_sql_mock(spark, source_count=100)
    with patch("src.delete_job.archive_row_count", return_value=100):
        eng.run(
            _table_config(), years=[2020], dry_run=False,
        )
    audit.log_archive.assert_called_once()
    msg = audit.log_archive.call_args.kwargs["error_message"] or ""
    assert "Prior archive run not found in audit" in msg


def test_tdel10_delete_after_archive_false_not_a_blocker():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    audit.get_prior_archived_run.return_value = None
    _install_delete_sql_mock(spark, source_count=100)
    tc = _table_config(delete_after_archive=False)
    with patch("src.delete_job.archive_row_count", return_value=100):
        report = eng.run(tc, years=[2020], dry_run=False)
    tid = "healthcare.claims.member"
    assert report["tables"][tid]["years"][2020]["status"] == "ARCHIVED_AND_DELETED"


def test_tdel11_years_widget_narrows_scope():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    _install_delete_sql_mock(spark, source_count=100)
    with patch("src.delete_job.archive_row_count", return_value=100):
        report = eng.run(
            _table_config(), years=[2020], dry_run=True,
        )
    tid = "healthcare.claims.member"
    assert list(report["tables"][tid]["years"].keys()) == [2020]


def test_tdel_d13_ineligible_dry_run_still_emits_preview_row():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (False, "not_archived_state")
    audit.get_last_run_state.return_value = (
        "ARCHIVED_AND_DELETED", date(2020, 12, 31), 100,
    )
    _install_delete_sql_mock(spark)
    eng.run(
        _table_config(), years=[2020], dry_run=True,
    )
    audit.log_dry_run.assert_called_once()
    kw = audit.log_dry_run.call_args.kwargs
    assert kw["action"] == "SKIP_NOT_ELIGIBLE"
    audit.log_archive.assert_not_called()


def test_tdel_dry_run_concurrent_emits_skip_concurrent_preview():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (True, "other-run", 2019, 2.0)
    _install_delete_sql_mock(spark)
    report = eng.run(
        _table_config(), years=[2019, 2020], dry_run=True,
    )
    tid = "healthcare.claims.member"
    assert report["had_concurrent_failures"] is True
    assert audit.log_dry_run.call_count == 2
    for c in audit.log_dry_run.call_args_list:
        assert c.kwargs["action"] == "SKIP_CONCURRENT"
    audit.log_archive.assert_not_called()
    for y in (2019, 2020):
        assert report["tables"][tid]["years"][y]["action"] == "SKIP_CONCURRENT"


def test_tdel_empty_years_expands_via_calculate_eligible_years():
    eng, _, audit, spark = _delete_engine()
    audit.check_concurrent_any_year.return_value = (False, None, None, None)
    audit.is_eligible_for_delete.return_value = (True, None)
    audit.get_last_run_state.return_value = ("ARCHIVED", date(2020, 12, 31), 100)
    _install_delete_sql_mock(spark, source_count=100)
    with patch(
        "src.archiver.calculate_eligible_years", return_value=[2019]
    ) as mock_calc, patch("src.delete_job.archive_row_count", return_value=100):
        report = eng.run(
            _table_config(), years=None, dry_run=True,
        )
    assert mock_calc.called, "empty years must call calculate_eligible_years"
    tid = "healthcare.claims.member"
    assert list(report["tables"][tid]["years"].keys()) == [2019]


def test_arc_append_year_writes_and_returns_count():
    """Task 2: _append_year does INSERT INTO delta.<path> SELECT ... and
    returns the post-write archive row count."""
    eng, _, _, spark = _make_engine()

    captured: list[str] = []

    def sql_side_effect(q):
        captured.append(q)
        m = MagicMock()
        if "SELECT COUNT(*)" in q:
            m.first.return_value = {"count": 17}
        return m

    spark.sql.side_effect = sql_side_effect

    archived = eng._append_year(
        _table_config(),
        year=2020,
        exclusion_clause="src.status = 'Active'",
        last_wm=date(2020, 6, 30),
    )

    assert archived == 17
    insert_q = next(q for q in captured if q.startswith("INSERT INTO delta.`"))
    assert "src.claim_date > " in insert_q
    assert "YEAR(src.claim_date) = 2020" in insert_q
    assert "(src.status = 'Active')" in insert_q


def test_arc_archive_table_year_invalid_action_raises():
    """Task 2: an unknown action label from _resolve_year_action falls into
    the new explicit else branch and raises ArchiveOperationError with
    reason='invalid_action'."""
    eng, _, audit, spark = _make_engine()
    audit.check_concurrent.return_value = (False, False, None, None)
    audit.get_latest_status.return_value = None
    audit.has_verify_failed.return_value = False

    fake = {
        "source_year_count": 0,
        "archive_state": "MISSING",
        "committed_rows": 0,
        "last_status": None,
        "last_watermark": None,
        "last_count": None,
        "would_archive": 0,
        "error_message": None,
        "error_reason": None,
        "action": "MIGRATE",  # not a known action
    }

    with patch.object(eng, "_resolve_year_action", return_value=fake), \
         patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)), \
         patch("src.archiver.get_archive_delta_version", return_value=0):
        with pytest.raises(ArchiveOperationError) as ei:
            eng._archive_table_year(
                _table_config(), 2020, "1=1", [], None,
            )
    assert ei.value.reason == "invalid_action"
    assert ei.value.operation == "archive", (
        "raise must come from the new write-branch else, not the existing delete branch"
    )
    assert "MIGRATE" in str(ei.value)


def test_arc_run_returns_dry_run_shape_when_dry_run_true():
    """Task 4: dry_run=True returns {'tables': {tid: {'years': {...}}}}."""
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    audit.is_archived_by_run.return_value = False
    spark.sql.return_value.first.return_value = {"y": 2026, "count": 0}

    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)):
        report = eng.run(_table_config(), dry_run=True)

    tid = "healthcare.claims.member"
    assert "tables" in report
    assert tid in report["tables"]
    assert "years" in report["tables"][tid]


def test_arc_run_returns_live_shape_when_dry_run_false():
    """Task 4: dry_run=False returns {'tables': {tid: {'years': {...}}}}
    with year_results values from _archive_table_year."""
    eng, _, audit, spark = _make_engine()
    audit.get_last_run_state.return_value = (None, None, None)
    spark.sql.return_value.first.return_value = {"y": 2026, "count": 0}

    fake = {"status": "SKIPPED", "record_count": 0}
    with patch("src.archiver.archive_state_and_count", return_value=("MISSING", 0)), \
         patch.object(eng, "_archive_table_year", return_value=fake):
        report = eng.run(_table_config(), dry_run=False)

    tid = "healthcare.claims.member"
    assert "tables" in report
    assert tid in report["tables"]
    assert "years" in report["tables"][tid]


def test_arc_archive_engine_inherits_archive_base():
    """Task 5: ArchiveEngine extends ArchiveBase. Shared helpers like
    _prepare_run, _calculate_eligible_years, _source_year_count,
    _delete_archived, _run_watermark_window, _set_timezone live on
    ArchiveBase and are reachable through the subclass."""
    from src.archiver import ArchiveBase, ArchiveEngine

    assert issubclass(ArchiveEngine, ArchiveBase)
    for name in (
        "_prepare_run",
        "_calculate_eligible_years",
        "_source_year_count",
        "_delete_archived",
        "_run_watermark_window",
        "_set_timezone",
    ):
        assert hasattr(ArchiveEngine, name), f"ArchiveEngine missing {name}"
        assert getattr(ArchiveBase, name, None) is not None, (
            f"ArchiveBase missing {name}"
        )


def test_arc_delete_job_imports_and_extends_archive_base():
    """Task 6: DeleteJob lives in src.delete_job and extends ArchiveBase."""
    from src.archiver import ArchiveBase
    from src.delete_job import DeleteJob

    assert issubclass(DeleteJob, ArchiveBase)
    assert hasattr(DeleteJob, "run")
    assert hasattr(DeleteJob, "_record_delete_skip")
    assert hasattr(DeleteJob, "_log_delete_failed")
