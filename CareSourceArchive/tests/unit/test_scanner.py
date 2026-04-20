import re
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import ArchiveConfigError
from src.scanner import (
    check_size_threshold,
    ensure_scanner_log_table,
    flag_ambiguous,
    flag_unmatched,
    generate_table_config,
    get_table_size_gb,
    match_watermark_column,
    merge_staging_to_final,
    run_scanner,
    scan_schema,
    validate_archive_path,
    write_scanner_log,
    write_staging,
)


def _scanner_template(**overrides):
    base = {
        "source_catalog": "cat",
        "source_schema": "sch",
        "archive_base_path": "abfss://bucket/root",
        "watermark_column_patterns": [],
        "exclude_tables": [],
        "min_table_size_gb": 0.0,
        "default_retention_years": 7,
        "delete_after_archive": False,
    }
    base.update(overrides)
    return base


def _row(**kwargs):
    m = MagicMock()
    m.asDict.return_value = dict(kwargs)
    return m


def _spark_sql_chain(calls_out):
    spark = MagicMock()

    def seq(*args, **kwargs):
        if not calls_out:
            raise AssertionError("unexpected spark.sql call")
        entry = calls_out.pop(0)
        if callable(entry):
            return entry(*args, **kwargs)
        return entry

    spark.sql.side_effect = seq
    return spark


class TestMatchDateColumn:
    @pytest.mark.parametrize(
        "columns, patterns, expected_col, expected_pat, expected_matched, expected_status",
        [
            (
                ["id", "claim_date", "x"],
                ["claim_date", ".*_date$"],
                "claim_date",
                "claim_date",
                ["claim_date"],
                "matched",
            ),
            (
                ["svc_dt", "other"],
                [".*_dt$", "claim_date"],
                "svc_dt",
                ".*_dt$",
                ["svc_dt"],
                "matched",
            ),
            (
                ["start_date", "end_date"],
                [".*_date$"],
                None,
                ".*_date$",
                ["start_date", "end_date"],
                "ambiguous",
            ),
            (
                ["a", "b"],
                ["claim_date", "svc_date"],
                None,
                None,
                [],
                "unmatched",
            ),
            (
                ["dt", "dt"],
                ["dt"],
                None,
                "dt",
                ["dt", "dt"],
                "ambiguous",
            ),
        ],
        ids=[
            "scn01_exact_match_single_column",
            "scn02_regex_match_when_no_exact",
            "scn02b_ambiguous_multiple_regex",
            "scn03_unmatched",
            "exact_ambiguous_before_regex",
        ],
    )
    def test_match_watermark_column_parametrized(
        self,
        columns,
        patterns,
        expected_col,
        expected_pat,
        expected_matched,
        expected_status,
    ):
        col, pat, matched, status = match_watermark_column(columns, patterns)
        assert col == expected_col
        assert pat == expected_pat
        assert sorted(matched) == sorted(expected_matched)
        assert status == expected_status


class TestScanSchema:
    def test_show_tables_returns_names(self):
        template = {
            "source_catalog": "c",
            "source_schema": "s",
            "archive_base_path": "abfss://x/p",
            "watermark_column_patterns": [],
            "exclude_tables": [],
            "min_table_size_gb": 0.0,
        }
        mock_df = MagicMock()
        mock_df.collect.return_value = [
            _row(tableName="t1"),
            _row(tableName="t2"),
        ]
        spark = MagicMock()
        spark.sql.return_value = mock_df
        out = scan_schema(spark, template)
        spark.sql.assert_called_once()
        sql = spark.sql.call_args[0][0]
        assert "SHOW TABLES IN c.s" in sql.replace("\n", " ")
        assert out == ["t1", "t2"]


class TestExcludeTables:
    def test_scn05_excluded_tables_not_configured(self):
        template = _scanner_template(
            watermark_column_patterns=["d"],
            archive_base_path="abfss://loc/archive/base",
            exclude_tables=["skip_me"],
        )
        settings = {
            "audit_catalog": "ac",
            "audit_schema": "as",
            "schema_templates_table": "x.y.templates",
            "table_configs_table": "cfg.sch.table_configs",
        }
        spark = MagicMock()
        loc_df = MagicMock()
        loc_df.collect.return_value = [_row(url="abfss://loc")]
        tmpl_df = MagicMock()
        tmpl_df.collect.return_value = [_row(**{k: v for k, v in template.items()})]
        tables_df = MagicMock()
        tables_df.collect.return_value = [
            _row(tableName="skip_me"),
            _row(tableName="keep_me"),
        ]
        desc_keep = MagicMock()
        desc_keep.collect.return_value = [_row(column_name="d")]
        log_exists_df = MagicMock()
        log_exists_df.collect.return_value = [_row(**{"1": 1})]
        detail_df = MagicMock()
        detail_df.collect.return_value = [_row(sizeInBytes=8 * 1024**3)]

        sql_calls = []

        def sql_side_effect(q):
            sql_calls.append(q)
            if "SHOW EXTERNAL LOCATIONS" in q:
                return loc_df
            if "information_schema.tables" in q and "'scanner_log'" in q:
                return log_exists_df
            if "FROM x.y.templates" in q or "SELECT * FROM x.y.templates" in q:
                return tmpl_df
            if "SHOW TABLES" in q:
                return tables_df
            if "information_schema.columns" in q and "'keep_me'" in q:
                return desc_keep
            if "DESCRIBE DETAIL" in q:
                return detail_df
            if "INSERT INTO" in q:
                return MagicMock()
            if "MERGE INTO" in q:
                return MagicMock()
            if "NOT IN (SELECT table_id" in q or "table_id NOT IN" in q:
                return MagicMock()
            return MagicMock()

        spark.sql.side_effect = sql_side_effect

        with patch("src.scanner.config.load_table_configs", return_value=[]):
            summary = run_scanner(spark, settings, force=False)
        assert summary["tables_excluded"] == 1
        assert summary["tables_matched"] == 1
        staging_inserts = [q for q in sql_calls if "INSERT INTO" in q and "staging" in q.lower()]
        assert len(staging_inserts) >= 1
        assert all("skip_me" not in q for q in staging_inserts)


class TestGetTableSizeGb:
    def test_scn08_describe_detail_parses_size(self):
        spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [_row(sizeInBytes=2 * 1024**3)]
        spark.sql.return_value = mock_df
        gb = get_table_size_gb(spark, "c", "s", "t")
        assert gb == pytest.approx(2.0)
        spark.sql.assert_called_once()
        assert "DESCRIBE DETAIL" in spark.sql.call_args[0][0]
        assert "c.s.t" in spark.sql.call_args[0][0]

    def test_returns_none_when_missing(self):
        spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [_row(other=1)]
        spark.sql.return_value = mock_df
        assert get_table_size_gb(spark, "c", "s", "t") is None


class TestCheckSizeThreshold:
    def test_scn09_zero_min_always_active(self):
        active, reason = check_size_threshold(None, 0.0)
        assert active is True
        assert reason is None
        active2, reason2 = check_size_threshold(0.5, 0.0)
        assert active2 is True
        assert reason2 is None

    def test_scn09_unknown_size_inactive_when_min_positive(self):
        active, reason = check_size_threshold(None, 1.0)
        assert active is False
        assert "unknown" in reason.lower()
        assert "DESCRIBE DETAIL" in reason

    def test_scn09_below_threshold(self):
        active, reason = check_size_threshold(0.5, 2.0)
        assert active is False
        assert "0.5" in reason
        assert "2.0" in reason
        assert "below" in reason.lower()

    def test_scn09_at_or_above_threshold(self):
        active, reason = check_size_threshold(2.0, 2.0)
        assert active is True
        assert reason is None


class TestValidateArchivePath:
    def test_scn11_passes_when_prefix_covered(self):
        spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [
            _row(url="abfss://a/other"),
            _row(url="abfss://a/archive"),
        ]
        spark.sql.return_value = mock_df
        validate_archive_path(spark, "abfss://a/archive/claims/")
        spark.sql.assert_called_once_with("SHOW EXTERNAL LOCATIONS")

    def test_scn11_fails_when_not_covered(self):
        spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [_row(url="s3://other/p")]
        spark.sql.return_value = mock_df
        with pytest.raises(ArchiveConfigError) as ei:
            validate_archive_path(spark, "abfss://mine/path")
        assert "abfss://mine/path" in str(ei.value)

    def test_scn11_external_volume_passes(self):
        spark = MagicMock()
        desc_df = MagicMock()
        desc_df.collect.return_value = [_row(volume_type="EXTERNAL")]
        spark.sql.return_value = desc_df
        validate_archive_path(spark, "/Volumes/cat/sch/vol/subdir")
        spark.sql.assert_called_once_with("DESCRIBE VOLUME cat.sch.vol")

    def test_scn11_managed_volume_raises(self):
        spark = MagicMock()
        desc_df = MagicMock()
        desc_df.collect.return_value = [_row(volume_type="MANAGED")]
        spark.sql.return_value = desc_df
        with pytest.raises(ArchiveConfigError, match="MANAGED.*not EXTERNAL"):
            validate_archive_path(spark, "/Volumes/cat/sch/vol/subdir")

    def test_scn11_invalid_volume_path_raises(self):
        spark = MagicMock()
        with pytest.raises(ArchiveConfigError, match="not a valid volume path"):
            validate_archive_path(spark, "/Volumes/cat/sch")


class TestEnsureScannerLogTable:
    def test_scn12_passes_when_table_exists(self):
        spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [MagicMock()]
        spark.sql.return_value = mock_df
        settings = {"audit_catalog": "acat", "audit_schema": "asch"}
        ensure_scanner_log_table(spark, settings)
        spark.sql.assert_called_once()
        sql = spark.sql.call_args[0][0]
        assert "DESCRIBE TABLE" in sql
        assert "acat.asch.scanner_log" in sql

    def test_scn12_raises_when_table_missing(self):
        spark = MagicMock()
        spark.sql.side_effect = Exception("TABLE_NOT_FOUND")
        settings = {"audit_catalog": "acat", "audit_schema": "asch"}
        with pytest.raises(ArchiveConfigError, match="does not exist"):
            ensure_scanner_log_table(spark, settings)


class TestGenerateAndFlagConfigs:
    def test_scn13_generate_table_config(self):
        template = _scanner_template(
            source_catalog="c",
            source_schema="s",
            archive_base_path="abfss://x/p",
            watermark_column_patterns=["d"],
            min_table_size_gb=1.0,
            default_retention_years=5,
            delete_after_archive=True,
        )
        cfg = generate_table_config(template, "tbl", "d")
        assert cfg["table_id"] == "c.s.tbl"
        assert cfg["source_catalog"] == "c"
        assert cfg["source_schema"] == "s"
        assert cfg["source_table"] == "tbl"
        assert cfg["watermark_column"] == "d"
        assert cfg["archive_base_path"] == "abfss://x/p"
        assert cfg["is_active"] is True
        assert cfg["modified_by"] == "scanner"
        assert cfg["retention_years"] == 5
        assert cfg["delete_after_archive"] is True

    def test_scn14_flag_unmatched(self):
        template = _scanner_template(
            source_catalog="c",
            source_schema="s",
            archive_base_path="abfss://x/p",
            watermark_column_patterns=["x"],
            default_retention_years=3,
        )
        cfg = flag_unmatched(template, "t")
        assert cfg["is_active"] is False
        assert "no date column matched" in cfg["reason"].lower()
        assert cfg["table_id"] == "c.s.t"

    def test_scn14_flag_ambiguous(self):
        template = _scanner_template(
            source_catalog="c",
            source_schema="s",
            archive_base_path="abfss://x/p",
            default_retention_years=3,
        )
        cfg = flag_ambiguous(template, "t", ".*_date$", ["a", "b"])
        assert cfg["is_active"] is False
        assert "ambiguous date column" in cfg["reason"]
        assert ".*_date$" in cfg["reason"]
        assert "a" in cfg["reason"] and "b" in cfg["reason"]


class TestWriteStaging:
    def test_scn15_insert_contains_scan_run_and_timestamp(self):
        spark = MagicMock()
        spark.sql.return_value = MagicMock()
        configs = [
            {
                "table_id": "c.s.t",
                "source_catalog": "c",
                "source_schema": "s",
                "source_table": "t",
                "watermark_column": "d",
                "retention_years": 7,
                "archive_base_path": "abfss://p",
                "delete_after_archive": False,
                "is_active": True,
                "reason": None,
                "exclusion_conditions": None,
            }
        ]
        write_staging(spark, configs, "stg.table_configs_staging", "run-uuid-1")
        spark.sql.assert_called_once()
        sql = spark.sql.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "stg.table_configs_staging" in sql
        assert "run-uuid-1" in sql
        assert re.search(r"current_timestamp\s*\(\s*\)", sql, re.I)


class TestMergeStagingToFinal:
    def test_scn16_merge_and_drop_update_sql(self):
        spark = MagicMock()
        spark.sql.return_value = MagicMock()
        merge_staging_to_final(
            spark,
            "cfg.staging",
            "cfg.table_configs",
            "sid-9",
            force=False,
        )
        assert spark.sql.call_count == 2
        merge_sql = spark.sql.call_args_list[0][0][0]
        update_sql = spark.sql.call_args_list[1][0][0]
        assert "MERGE INTO cfg.table_configs" in merge_sql
        assert "cfg.staging" in merge_sql
        assert "sid-9" in merge_sql
        assert "WHEN NOT MATCHED" in merge_sql
        assert "WHEN MATCHED" in merge_sql
        assert "modified_by = 'scanner'" in merge_sql
        assert "scan_run_id" in merge_sql
        assert "source.scan_run_id" in merge_sql
        assert "target.scan_run_id = source.scan_run_id" in merge_sql
        assert "UPDATE cfg.table_configs" in update_sql
        assert "is_active = false" in update_sql.lower()
        assert "sid-9" in update_sql
        deact_set = update_sql[update_sql.upper().index("SET"):update_sql.upper().index("WHERE")]
        assert "scan_run_id" in deact_set

    def test_scn16_force_true_merge_allows_non_scanner_update(self):
        spark = MagicMock()
        spark.sql.return_value = MagicMock()
        merge_staging_to_final(
            spark, "s", "t", "r1", force=True
        )
        merge_sql = spark.sql.call_args_list[0][0][0]
        assert "TRUE" in merge_sql or "true" in merge_sql.lower()
        assert "scan_run_id" in merge_sql
        assert "source.scan_run_id" in merge_sql

    def test_scn16_merge_insert_includes_scan_run_id(self):
        spark = MagicMock()
        spark.sql.return_value = MagicMock()
        merge_staging_to_final(spark, "stg", "tgt", "run-abc", force=False)
        merge_sql = spark.sql.call_args_list[0][0][0]
        start = merge_sql.index("WHEN NOT MATCHED")
        insert_section = merge_sql[start:]
        assert "scan_run_id" in insert_section
        assert "source.scan_run_id" in insert_section

    def test_scn16_deactivation_update_includes_scan_run_id(self):
        spark = MagicMock()
        spark.sql.return_value = MagicMock()
        merge_staging_to_final(spark, "stg", "tgt", "run-xyz", force=False)
        update_sql = spark.sql.call_args_list[1][0][0]
        set_clause = update_sql[update_sql.upper().index("SET"):update_sql.upper().index("WHERE")]
        assert "scan_run_id" in set_clause


class TestRunScannerOrchestration:
    def test_summary_counts_and_calls_merge(self):
        template = _scanner_template(
            watermark_column_patterns=["event_date"],
        )
        settings = {
            "audit_catalog": "ac",
            "audit_schema": "aud",
            "schema_templates_table": "p.templates",
            "table_configs_table": "cfg.sc.table_configs",
        }

        spark = MagicMock()
        loc_df = MagicMock()
        loc_df.collect.return_value = [_row(url="abfss://bucket")]
        log_exists_df = MagicMock()
        log_exists_df.collect.return_value = [MagicMock()]
        tmpl_df = MagicMock()
        tmpl_df.collect.return_value = [_row(**template)]
        tables_df = MagicMock()
        tables_df.collect.return_value = [_row(tableName="events")]
        desc_df = MagicMock()
        desc_df.collect.return_value = [
            _row(column_name="id"),
            _row(column_name="event_date"),
        ]
        detail_df = MagicMock()
        detail_df.collect.return_value = [_row(sizeInBytes=1024**3)]

        def sql_side_effect(q):
            if "SHOW EXTERNAL LOCATIONS" in q:
                return loc_df
            if "information_schema.tables" in q and "'scanner_log'" in q:
                return log_exists_df
            if "FROM p.templates" in q or "SELECT * FROM p.templates" in q:
                return tmpl_df
            if "SHOW TABLES" in q:
                return tables_df
            if "information_schema.columns" in q:
                return desc_df
            if "DESCRIBE DETAIL" in q:
                return detail_df
            return MagicMock()

        spark.sql.side_effect = sql_side_effect

        existing = [
            {
                "table_id": "cat.sch.events",
                "modified_by": "alice",
                "source_catalog": "cat",
                "source_schema": "sch",
                "source_table": "events",
                "watermark_column": "event_date",
                "archive_base_path": "abfss://bucket/root",
            }
        ]
        with patch("src.scanner.config.load_table_configs", return_value=existing):
            summary = run_scanner(spark, settings, force=False)

        assert summary["tables_matched"] == 1
        assert summary["tables_ambiguous"] == 0
        assert summary["tables_unmatched"] == 0
        assert summary["tables_excluded"] == 0
        assert summary["tables_preserved"] == 1
        merge_calls = [c for c in spark.sql.call_args_list if "MERGE INTO" in c[0][0]]
        assert len(merge_calls) == 1


class TestWriteScannerLog:
    def test_scn15_insert_includes_workspace_id_and_scanned_by(self):
        spark = MagicMock()
        settings = {
            "audit_catalog": "ac",
            "audit_schema": "asc",
        }
        job_context = {"workspace_id": "ws-42"}
        table_results = [
            {
                "log_id": "lid-1",
                "table_id": "c.s.t",
                "source_catalog": "c",
                "source_schema": "s",
                "source_table": "t",
                "match_status": "matched",
                "matched_column": "d",
                "matched_pattern": "d",
                "all_matched_columns": ["d"],
                "ambiguity_detail": None,
                "table_size_gb": 5.0,
                "size_check_passed": True,
                "is_active": True,
                "inactive_reason": None,
                "merge_action": "insert",
            }
        ]
        write_scanner_log(spark, settings, "scan-run-1", table_results, job_context=job_context)
        spark.sql.assert_called_once()
        sql = spark.sql.call_args[0][0]
        assert "workspace_id" in sql
        assert "scanned_by" in sql
        assert "ws-42" in sql
        assert "current_user()" in sql

    def test_scn15_workspace_id_null_when_missing_from_settings(self):
        spark = MagicMock()
        settings = {"audit_catalog": "ac", "audit_schema": "asc"}
        table_results = [
            {
                "log_id": "lid-2",
                "table_id": "c.s.t2",
                "source_catalog": "c",
                "source_schema": "s",
                "source_table": "t2",
                "match_status": "unmatched",
                "matched_column": None,
                "matched_pattern": None,
                "all_matched_columns": [],
                "ambiguity_detail": None,
                "table_size_gb": None,
                "size_check_passed": False,
                "is_active": False,
                "inactive_reason": "no match",
                "merge_action": None,
            }
        ]
        write_scanner_log(spark, settings, "scan-run-2", table_results)
        sql = spark.sql.call_args[0][0]
        assert "NULL, current_user()" in sql or "NULL,\ncurrent_user()" in sql.replace(" ", "")

    def test_scn15_ensure_checks_correct_table_name(self):
        spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [MagicMock()]
        spark.sql.return_value = mock_df
        settings = {"audit_catalog": "mycat", "audit_schema": "mysch"}
        ensure_scanner_log_table(spark, settings)
        spark.sql.assert_called_once()
        sql = spark.sql.call_args[0][0]
        assert "DESCRIBE TABLE" in sql
        assert "mycat.mysch.scanner_log" in sql

    def test_write_scanner_log_content_includes_ambiguity_and_merge_action(self):
        spark = MagicMock()
        settings = {"audit_catalog": "ac", "audit_schema": "asc"}
        table_results = [
            {
                "log_id": "lid-3",
                "table_id": "c.s.t3",
                "source_catalog": "c",
                "source_schema": "s",
                "source_table": "t3",
                "match_status": "ambiguous",
                "matched_column": None,
                "matched_pattern": ".*_date$",
                "all_matched_columns": ["start_date", "end_date"],
                "ambiguity_detail": "multiple matches for .*_date$",
                "table_size_gb": 3.5,
                "size_check_passed": True,
                "is_active": False,
                "inactive_reason": "ambiguous",
                "merge_action": "update",
            }
        ]
        write_scanner_log(spark, settings, "scan-run-3", table_results)
        sql = spark.sql.call_args[0][0]
        assert "ambiguity_detail" in sql.lower() or "multiple matches" in sql
        assert "all_matched_columns" in sql.lower() or "start_date" in sql
        assert "merge_action" in sql.lower() or "update" in sql

    def test_scn13_summary_with_ambiguous_tables(self):
        template = _scanner_template(
            watermark_column_patterns=[".*_date$"],
        )
        settings = {
            "audit_catalog": "ac",
            "audit_schema": "aud",
            "schema_templates_table": "p.templates",
            "table_configs_table": "cfg.sc.table_configs",
        }

        spark = MagicMock()
        loc_df = MagicMock()
        loc_df.collect.return_value = [_row(url="abfss://bucket")]
        log_exists_df = MagicMock()
        log_exists_df.collect.return_value = [MagicMock()]
        tmpl_df = MagicMock()
        tmpl_df.collect.return_value = [_row(**template)]
        tables_df = MagicMock()
        tables_df.collect.return_value = [_row(tableName="ambig_tbl")]
        desc_df = MagicMock()
        desc_df.collect.return_value = [
            _row(column_name="start_date"),
            _row(column_name="end_date"),
        ]
        detail_df = MagicMock()
        detail_df.collect.return_value = [_row(sizeInBytes=1024**3)]

        def sql_side_effect(q):
            if "SHOW EXTERNAL LOCATIONS" in q:
                return loc_df
            if "information_schema.tables" in q and "'scanner_log'" in q:
                return log_exists_df
            if "FROM p.templates" in q or "SELECT * FROM p.templates" in q:
                return tmpl_df
            if "SHOW TABLES" in q:
                return tables_df
            if "information_schema.columns" in q:
                return desc_df
            if "DESCRIBE DETAIL" in q:
                return detail_df
            return MagicMock()

        spark.sql.side_effect = sql_side_effect

        with patch("src.scanner.config.load_table_configs", return_value=[]):
            summary = run_scanner(spark, settings, force=False)
        assert summary["tables_ambiguous"] >= 1


class TestRunScannerSchemaId:
    _SETTINGS = {
        "schema_templates_table": "cat.sch.schema_templates",
        "table_configs_table": "cat.sch.table_configs",
        "audit_catalog": "cat",
        "audit_schema": "sch",
    }

    def test_run_scanner_with_schema_id_passes_through(self):
        spark = MagicMock()
        with patch("src.scanner.ensure_scanner_log_table"):
            with patch(
                "src.scanner.config.load_schema_templates", return_value=[]
            ) as load_mock:
                run_scanner(spark, self._SETTINGS, schema_id="claims")
        load_mock.assert_called_once_with(
            spark, "cat.sch.schema_templates", schema_id="claims"
        )

    def test_run_scanner_without_schema_id_scans_all(self):
        spark = MagicMock()
        with patch("src.scanner.ensure_scanner_log_table"):
            with patch(
                "src.scanner.config.load_schema_templates", return_value=[]
            ) as load_mock:
                run_scanner(spark, self._SETTINGS)
        load_mock.assert_called_once_with(
            spark, "cat.sch.schema_templates", schema_id=None
        )

    def test_run_scanner_zero_active_templates_returns_zeros(self):
        spark = MagicMock()
        with patch("src.scanner.ensure_scanner_log_table"):
            with patch("src.scanner.config.load_schema_templates", return_value=[]):
                with patch("src.scanner.write_staging") as write_staging:
                    with patch(
                        "src.scanner.merge_staging_to_final"
                    ) as merge_staging:
                        with patch(
                            "src.scanner.write_scanner_log"
                        ) as write_log:
                            summary = run_scanner(spark, self._SETTINGS)
        assert summary["tables_matched"] == 0
        assert summary["tables_ambiguous"] == 0
        assert summary["tables_unmatched"] == 0
        assert summary["tables_excluded"] == 0
        assert summary["tables_below_size_threshold"] == 0
        assert summary["tables_size_unknown"] == 0
        assert summary["tables_preserved"] == 0
        assert "scan_run_id" in summary
        write_staging.assert_not_called()
        merge_staging.assert_not_called()
        write_log.assert_not_called()
