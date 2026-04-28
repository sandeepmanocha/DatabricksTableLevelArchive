import logging
import uuid
from dataclasses import fields
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from src.exceptions import ArchiveConfigError, ArchiveError, ArchiveOperationError
from src.utils import (
    RunContext,
    _AnalysisException,
    _classify_delta_probe_exception,
    archive_path_from_config,
    archive_row_count,
    archive_state_and_count,
    build_archive_path,
    build_full_table_name,
    build_insert_values_sql,
    build_multi_insert_values_sql,
    calculate_eligible_years,
    collect_column,
    configure_logging,
    create_schema_if_not_exists,
    ensure_table_exists,
    ensure_table_with_setup_message,
    generate_archive_run_id,
    get_archive_delta_version,
    get_delta_history_versions_strict,
    is_safe_identifier,
    row_to_dict,
    row_value,
    source_fq_from_config,
    spark_count,
    sql_bool,
    sql_date_or_null,
    sql_expr,
    sql_int,
    sql_int_or_null,
    sql_quote,
    sql_str_or_null,
    validate_fq_identifier,
    validate_identifier,
)


class TestRunContext:
    def test_dataclass_field_names_and_order(self):
        names = [f.name for f in fields(RunContext)]
        assert names == ["settings", "job_context", "archive_run_id"]

    def test_instantiation_data_container(self):
        rc = RunContext(
            settings={"a": 1},
            job_context={"x": None},
            archive_run_id="rid",
        )
        assert rc.settings == {"a": 1}
        assert rc.job_context == {"x": None}
        assert rc.archive_run_id == "rid"


class TestBuildPathsAndNames:
    def test_build_archive_path(self):
        assert build_archive_path("/base", "tbl", 2024) == "/base/tbl/year_2024"

    def test_build_archive_path_trailing_slash(self):
        assert build_archive_path("/base/", "tbl", 2024) == "/base/tbl/year_2024"

    def test_build_full_table_name(self):
        assert build_full_table_name("c", "s", "t") == "c.s.t"


class TestGenerateArchiveRunId:
    def test_returns_uuid_string(self):
        rid = generate_archive_run_id()
        assert isinstance(rid, str)
        uuid.UUID(rid)


class TestCreateSchemaIfNotExists:
    def test_executes_expected_sql(self, mock_spark):
        create_schema_if_not_exists(mock_spark, "my_cat", "my_sch")
        mock_spark.sql.assert_called_once_with(
            "CREATE SCHEMA IF NOT EXISTS my_cat.my_sch"
        )


class _FakeRow:
    """Minimal Spark Row stand-in for testing row_value / row_to_dict."""
    def __init__(self, data):
        self._data = data

    def asDict(self):
        return dict(self._data)

    def __getitem__(self, key):
        return self._data[key]


class TestRowValue:
    def test_dict_input(self):
        assert row_value({"a": 1}, "a") == 1

    def test_dict_missing_returns_default(self):
        assert row_value({"a": 1}, "b") is None
        assert row_value({"a": 1}, "b", "X") == "X"

    def test_spark_row_via_asdict(self):
        assert row_value(_FakeRow({"col": 42}), "col") == 42

    def test_spark_row_missing_field(self):
        assert row_value(_FakeRow({"col": 42}), "other", -1) == -1

    def test_none_row_returns_default(self):
        assert row_value(None, "x") is None

    def test_row_value_returns_default_for_none(self):
        assert row_value(None, "x", default=7) == 7

    def test_row_value_no_longer_swallows_type_error(self):
        with pytest.raises(TypeError):
            row_value(5, "x", default=0)


class TestRowToDict:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert row_to_dict(d) is d

    def test_spark_row_converted(self):
        assert row_to_dict(_FakeRow({"b": 2})) == {"b": 2}

    def test_none_returns_empty(self):
        assert row_to_dict(None) == {}


class TestCollectColumn:
    def test_returns_values(self, mock_spark):
        df = MagicMock()
        df.collect.return_value = [{"name": "a"}, {"name": "b"}]
        mock_spark.sql.return_value = df
        assert collect_column(mock_spark, "SELECT name FROM t", "name") == ["a", "b"]

    def test_empty_result(self, mock_spark):
        df = MagicMock()
        df.collect.return_value = []
        mock_spark.sql.return_value = df
        assert collect_column(mock_spark, "SELECT x FROM t", "x") == []


class TestEnsureTableExists:
    def test_passes_when_table_exists(self, mock_spark):
        ensure_table_exists(mock_spark, "cat.sch.tbl")
        mock_spark.sql.assert_called_once_with("DESCRIBE TABLE cat.sch.tbl")

    def test_raises_when_table_missing(self, mock_spark):
        mock_spark.sql.side_effect = Exception("TABLE_NOT_FOUND")
        with pytest.raises(ArchiveConfigError, match="does not exist"):
            ensure_table_exists(mock_spark, "cat.sch.missing")


@pytest.fixture
def restore_root_logging():
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    yield
    root.setLevel(saved_level)
    root.handlers.clear()
    for h in saved_handlers:
        root.addHandler(h)


class TestSafeIdentifierHelpers:
    def test_is_safe_identifier_accepts_simple_names(self):
        assert is_safe_identifier("claims")
        assert is_safe_identifier("_x")
        assert is_safe_identifier("Col_1")

    def test_is_safe_identifier_rejects_dots_spaces_hyphens_and_quotes_and_empty(self):
        assert not is_safe_identifier("")
        assert not is_safe_identifier("a.b")
        assert not is_safe_identifier("a b")
        assert not is_safe_identifier("a-b")
        assert not is_safe_identifier("'x'")
        assert not is_safe_identifier("9bad")

    def test_validate_identifier_raises_archive_config_error(self):
        with pytest.raises(ArchiveConfigError, match="field='badfield'"):
            validate_identifier("bad-name", field="badfield")
        assert validate_identifier("ok_name", field="badfield") == "ok_name"

    def test_validate_fq_identifier_accepts_one_two_three_parts(self):
        assert validate_fq_identifier("t") == "t"
        assert validate_fq_identifier("s.t") == "s.t"
        assert validate_fq_identifier("c.s.t") == "c.s.t"

    def test_validate_fq_identifier_rejects_empty_parts(self):
        with pytest.raises(ArchiveConfigError):
            validate_fq_identifier("")
        with pytest.raises(ArchiveConfigError):
            validate_fq_identifier("a..b")
        with pytest.raises(ArchiveConfigError):
            validate_fq_identifier("a.b.c.d")


class TestConfigureLogging:
    def test_root_info_format_includes_run_id(self, restore_root_logging, capsys):
        root = logging.getLogger()
        root.handlers.clear()
        configure_logging("run-xyz-001")
        assert root.level == logging.INFO
        logging.getLogger("other.logger").info("hello")
        err = capsys.readouterr().err
        assert "run-xyz-001" in err
        assert "INFO" in err
        assert "hello" in err
        assert "[run-xyz-001] []" in err

    def test_configure_logging_idempotent(self, restore_root_logging):
        root = logging.getLogger()
        root.handlers.clear()
        configure_logging("run-1")
        configure_logging("run-1")
        named = [h for h in root.handlers if getattr(h, "name", None) == "caresource_archive"]
        assert len(named) == 1
        configure_logging("run-2")
        named = [h for h in root.handlers if getattr(h, "name", None) == "caresource_archive"]
        assert len(named) == 1
        assert named[0].filters[0].archive_run_id == "run-2"


class TestConfigureLoggingTableField:
    def test_log02_table_field_in_format(self, restore_root_logging, capsys):
        root = logging.getLogger()
        root.handlers.clear()
        configure_logging("run-tbl-001")
        logger = logging.getLogger("test.module")
        logger.info("processing", extra={"table": "healthcare.claims.member"})
        err = capsys.readouterr().err
        assert "[healthcare.claims.member]" in err
        assert "run-tbl-001" in err
        assert "processing" in err

    def test_log02_empty_table_when_not_provided(self, restore_root_logging, capsys):
        root = logging.getLogger()
        root.handlers.clear()
        configure_logging("run-tbl-002")
        logging.getLogger("test.mod2").info("no table")
        err = capsys.readouterr().err
        assert "[]" in err
        assert "run-tbl-002" in err


class TestSqlHelpers:
    @pytest.mark.parametrize(
        "fn,arg,expected",
        [
            (sql_quote, "hello", "'hello'"),
            (sql_quote, "it's", "'it''s'"),
            (sql_quote, "", "''"),
            (sql_str_or_null, "abc", "'abc'"),
            (sql_str_or_null, None, "NULL"),
            (sql_int, 42, "42"),
            (sql_int, 3.0, "3"),
            (sql_int_or_null, 7, "7"),
            (sql_int_or_null, None, "NULL"),
            (sql_bool, True, "true"),
            (sql_bool, False, "false"),
            (sql_expr, "current_timestamp()", "current_timestamp()"),
        ],
    )
    def test_sql_helpers_happy_path(self, fn, arg, expected):
        assert fn(arg) == expected

    def test_sql_int_raises_on_non_numeric(self):
        with pytest.raises((ValueError, TypeError)):
            sql_int("not_a_number")

    def test_sql_int_rejects_bool(self):
        with pytest.raises(TypeError, match="bool"):
            sql_int(True)
        with pytest.raises(TypeError, match="bool"):
            sql_int(False)

    def test_sql_int_truncates_float(self):
        assert sql_int(3.9) == "3"


class TestSourceFqFromConfig:
    def test_builds_fq_name(self):
        tc = {"source_catalog": "cat", "source_schema": "sch", "source_table": "tbl"}
        assert source_fq_from_config(tc) == "cat.sch.tbl"

    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            source_fq_from_config({"source_catalog": "c", "source_schema": "s"})


class TestArchivePathFromConfig:
    def test_builds_path(self):
        tc = {"archive_base_path": "/data", "source_table": "orders"}
        assert archive_path_from_config(tc, 2023) == "/data/orders/year_2023"

    def test_strips_trailing_slash(self):
        tc = {"archive_base_path": "/data/", "source_table": "t"}
        assert archive_path_from_config(tc, 2020) == "/data/t/year_2020"


class TestEnsureTableWithSetupMessage:
    def test_passes_when_table_exists(self, mock_spark):
        ensure_table_with_setup_message(mock_spark, "cat.sch.tbl")

    def test_raises_with_setup_hint(self, mock_spark):
        mock_spark.sql.side_effect = Exception("not found")
        with pytest.raises(ArchiveConfigError, match="Run the setup_config_tables"):
            ensure_table_with_setup_message(mock_spark, "cat.sch.missing", label="Audit")

    def test_message_contains_table_name(self, mock_spark):
        mock_spark.sql.side_effect = Exception("not found")
        with pytest.raises(ArchiveConfigError, match="cat.sch.missing"):
            ensure_table_with_setup_message(mock_spark, "cat.sch.missing")


class TestSparkCount:
    def test_spark_count_raises_when_no_rows(self, mock_spark):
        mock_spark.sql.return_value.first.return_value = None
        with pytest.raises(ArchiveError, match="no rows"):
            spark_count(mock_spark, "SELECT 1 AS count WHERE 1=0")

    def test_spark_count_custom_column_name(self, mock_spark):
        row = MagicMock()
        row.__getitem__ = MagicMock(return_value=42)
        mock_spark.sql.return_value.first.return_value = row
        assert spark_count(mock_spark, "SELECT 1", column_name="row_count") == 42

    def test_returns_integer(self, mock_spark):
        row = MagicMock()
        row.__getitem__ = MagicMock(return_value=42)
        mock_spark.sql.return_value.first.return_value = row
        assert spark_count(mock_spark, "SELECT count(*) as count FROM t") == 42
        mock_spark.sql.assert_called_once_with("SELECT count(*) as count FROM t")

    def test_coerces_to_int(self, mock_spark):
        row = MagicMock()
        row.__getitem__ = MagicMock(return_value="100")
        mock_spark.sql.return_value.first.return_value = row
        result = spark_count(mock_spark, "SELECT count(*) as count FROM t")
        assert result == 100
        assert isinstance(result, int)


class TestInsertBuilders:
    def test_build_insert_values_sql_correct_shape(self):
        result = build_insert_values_sql(
            "db.schema.tbl",
            ["col_a", "col_b"],
            ["'v1'", "42"],
        )
        assert result == "INSERT INTO db.schema.tbl (col_a, col_b) VALUES ('v1', 42)"

    @pytest.mark.parametrize(
        "fn,args,match",
        [
            (
                build_insert_values_sql,
                ("t", ["a", "b"], ["1"]),
                "length mismatch",
            ),
            (
                build_insert_values_sql,
                ("t", [], []),
                "columns must not be empty",
            ),
            (
                build_multi_insert_values_sql,
                ("t", ["a"], []),
                "rows must not be empty",
            ),
            (
                build_multi_insert_values_sql,
                ("t", ["a", "b"], [["1", "2"], ["3"]]),
                "row 1 has 1 values, expected 2",
            ),
            (
                build_multi_insert_values_sql,
                ("t", [], [["1"]]),
                "columns must not be empty",
            ),
        ],
    )
    def test_insert_builders_raise_value_error(self, fn, args, match):
        with pytest.raises(ValueError, match=match):
            fn(*args)

    def test_build_multi_insert_single_row(self):
        result = build_multi_insert_values_sql(
            "db.tbl",
            ["x", "y"],
            [["'a'", "'b'"]],
        )
        assert result == "INSERT INTO db.tbl (x, y) VALUES ('a', 'b')"

    def test_build_multi_insert_multiple_rows(self):
        result = build_multi_insert_values_sql(
            "db.tbl",
            ["x", "y"],
            [["'a'", "'b'"], ["'c'", "'d'"]],
        )
        assert result == "INSERT INTO db.tbl (x, y) VALUES ('a', 'b'), ('c', 'd')"


class TestSqlDateOrNull:
    def test_none_returns_null(self):
        assert sql_date_or_null(None) == "NULL"

    def test_date_formats_as_date_literal(self):
        assert sql_date_or_null(date(2020, 12, 31)) == "DATE '2020-12-31'"

    def test_datetime_is_truncated_to_date(self):
        assert sql_date_or_null(datetime(2022, 6, 15, 14, 30, 0)) == "DATE '2022-06-15'"

    def test_single_digit_month_and_day_zero_padded(self):
        assert sql_date_or_null(date(2021, 1, 5)) == "DATE '2021-01-05'"

    def test_sql_date_or_null_rejects_non_date(self):
        with pytest.raises(TypeError):
            sql_date_or_null("2025-01-01")
        with pytest.raises(TypeError):
            sql_date_or_null(42)

    def test_sql_date_or_null_accepts_date_and_datetime_and_none(self):
        assert sql_date_or_null(None) == "NULL"
        assert sql_date_or_null(date(2025, 1, 1)) == "DATE '2025-01-01'"
        assert sql_date_or_null(datetime(2025, 1, 1, 15, 30, 0)) == "DATE '2025-01-01'"


class TestArchiveRowCount:
    def test_valid_delta_returns_count(self, mock_spark):
        expected_path = "/base/my_table/year_2024"
        count_df = MagicMock()
        count_df.first.return_value = _FakeRow({"c": 42})

        def sql_side_effect(sql: str):
            assert f"delta.`{expected_path}`" in sql
            assert "COUNT(*)" in sql and "AS c" in sql
            return count_df

        mock_spark.sql.side_effect = sql_side_effect
        assert archive_row_count(mock_spark, "/base", "my_table", 2024) == 42
        assert mock_spark.sql.call_count == 1

    def test_missing_folder_raises_archive_operation_error(self, mock_spark):
        expected_path = "/base/t/year_2020"

        def sql_side_effect(sql: str):
            assert f"delta.`{expected_path}`" in sql
            assert "COUNT(*)" in sql
            raise _AnalysisException(f"PATH_NOT_FOUND: {expected_path}")

        mock_spark.sql.side_effect = sql_side_effect
        with pytest.raises(ArchiveOperationError) as exc_info:
            archive_row_count(mock_spark, "/base", "t", 2020)
        assert exc_info.value.reason == "archive_folder_missing"
        msg = str(exc_info.value)
        assert "archive_folder_missing" not in msg
        assert "t" in msg
        assert "2020" in msg
        assert expected_path in msg

    def test_orphan_folder_raises_archive_operation_error(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException("is not a Delta table")
        with pytest.raises(ArchiveOperationError) as exc_info:
            archive_row_count(mock_spark, "/b", "orphan_tbl", 2019)
        assert exc_info.value.reason == "archive_folder_orphan"
        msg = str(exc_info.value)
        assert "archive_folder_orphan" not in msg
        assert "orphan_tbl" in msg
        assert "2019" in msg

    def test_delta_missing_delta_table_classified_as_orphan(self, mock_spark):
        # Regression for 25T: on modern DBR a SELECT COUNT(*) against a path
        # that exists but lacks a Delta log surfaces as DELTA_MISSING_DELTA_TABLE.
        # That is a true orphan. Missing paths raise PATH_NOT_FOUND instead
        # (covered by test_missing_folder_raises_archive_operation_error).
        mock_spark.sql.side_effect = _AnalysisException(
            "[DELTA_MISSING_DELTA_TABLE] `/x/y/z` is not a Delta table."
        )
        with pytest.raises(ArchiveOperationError) as exc_info:
            archive_row_count(mock_spark, "/b", "t", 2018)
        assert exc_info.value.reason == "archive_folder_orphan"

    def test_count_first_none_returns_zero(self, mock_spark):
        count_df = MagicMock()
        count_df.first.return_value = None
        mock_spark.sql.return_value = count_df
        assert archive_row_count(mock_spark, "/x", "tbl", 2021) == 0

    def test_invalid_table_name_raises_config_error(self, mock_spark):
        bad_table = "bad`name"
        with pytest.raises(ArchiveConfigError):
            archive_row_count(mock_spark, "/base", bad_table, 2024)
        mock_spark.sql.assert_not_called()


class TestArchiveStateAndCount:
    def test_valid_archive_returns_valid_and_count(self, mock_spark):
        count_df = MagicMock()
        count_df.first.return_value = _FakeRow({"c": 10})
        mock_spark.sql.return_value = count_df
        assert archive_state_and_count(mock_spark, "/p", "tbl", 2022) == ("VALID", 10)
        # Single SQL probe — no separate classify query.
        assert mock_spark.sql.call_count == 1

    def test_missing_folder_returns_missing_zero(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException(
            "java.io.FileNotFoundException: /missing",
        )
        assert archive_state_and_count(mock_spark, "/p", "tbl", 2022) == ("MISSING", 0)

    def test_orphan_folder_returns_orphan_zero(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException("is not a Delta table")
        assert archive_state_and_count(mock_spark, "/p", "tbl", 2022) == ("ORPHAN", 0)

    def test_invalid_table_name_raises_config_error(self, mock_spark):
        with pytest.raises(ArchiveConfigError):
            archive_state_and_count(mock_spark, "/p", "bad`name", 2022)
        mock_spark.sql.assert_not_called()

    def test_unexpected_operation_error_propagates(self, mock_spark):
        err = ArchiveOperationError(
            "synthetic", table="tbl", year=2022,
            operation="archive_row_count", reason="unexpected_reason",
        )
        with pytest.MonkeyPatch.context() as mp:
            def _raise(*args, **kwargs):
                raise err

            mp.setattr("src.utils.archive_row_count", _raise)
            with pytest.raises(ArchiveOperationError) as ei:
                archive_state_and_count(mock_spark, "/p", "tbl", 2022)
            assert ei.value.reason == "unexpected_reason"


class TestClassifyDeltaProbeException:
    def test_classify_delta_probe_exception_recognises_missing_transaction_log_uppercase(self):
        # Empty archive folder fixture (12T phase 2c): Spark surfaces
        # [DELTA_MISSING_TRANSACTION_LOG] when _delta_log is missing or empty.
        # That is a true orphan, same shape as DELTA_MISSING_DELTA_TABLE.
        result = _classify_delta_probe_exception(
            Exception("[DELTA_MISSING_TRANSACTION_LOG] details")
        )
        assert result == "archive_folder_orphan"

    def test_classify_delta_probe_exception_recognises_missing_transaction_log_phrase(self):
        # Some Spark error variants embed a free-form phrase rather than the
        # canonical SQLSTATE; cover both shapes.
        result = _classify_delta_probe_exception(
            Exception("missing transaction log at /path")
        )
        assert result == "archive_folder_orphan"


class TestGetArchiveDeltaVersion:
    def test_valid_returns_max_version(self, mock_spark):
        df = MagicMock()
        df.first.return_value = _FakeRow({"v": 7})
        mock_spark.sql.return_value = df
        assert get_archive_delta_version(mock_spark, "/p", "tbl", 2022) == 7
        sql = mock_spark.sql.call_args[0][0]
        assert "MAX(version)" in sql
        assert "DESCRIBE HISTORY" in sql
        assert "delta.`/p/tbl/year_2022`" in sql

    def test_missing_returns_none(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException(
            "java.io.FileNotFoundException: /missing"
        )
        assert get_archive_delta_version(mock_spark, "/p", "tbl", 2022) is None

    def test_orphan_returns_none(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException("not a valid delta table")
        assert get_archive_delta_version(mock_spark, "/p", "tbl", 2022) is None

    def test_empty_history_returns_none(self, mock_spark):
        # MAX over zero history rows yields one row with v=NULL.
        df = MagicMock()
        df.first.return_value = _FakeRow({"v": None})
        mock_spark.sql.return_value = df
        assert get_archive_delta_version(mock_spark, "/p", "tbl", 2022) is None

    def test_first_returns_none_returns_none(self, mock_spark):
        df = MagicMock()
        df.first.return_value = None
        mock_spark.sql.return_value = df
        assert get_archive_delta_version(mock_spark, "/p", "tbl", 2022) is None

    def test_invalid_table_name_returns_none(self, mock_spark):
        bad_table = "bad`name"
        assert get_archive_delta_version(mock_spark, "/p", bad_table, 2022) is None
        assert mock_spark.sql.call_count == 0


class TestGetDeltaHistoryVersionsStrict:
    def test_valid_preserves_order(self, mock_spark):
        hist_df = MagicMock()
        hist_df.collect.return_value = [
            _FakeRow({"version": 5}),
            _FakeRow({"version": 4}),
            _FakeRow({"version": 3}),
        ]
        mock_spark.sql.return_value = hist_df
        assert get_delta_history_versions_strict(mock_spark, "/p", "t", 2000) == [5, 4, 3]

    def test_missing_raises(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException("PATH_NOT_FOUND: /nope")
        with pytest.raises(ArchiveOperationError) as exc_info:
            get_delta_history_versions_strict(mock_spark, "/p", "t", 2000)
        assert exc_info.value.reason == "archive_folder_missing"

    def test_orphan_raises(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException("is not a Delta table")
        with pytest.raises(ArchiveOperationError) as exc_info:
            get_delta_history_versions_strict(mock_spark, "/p", "t", 2000)
        assert exc_info.value.reason == "archive_folder_orphan"

    def test_invalid_version_raises_archive_operation_error(self, mock_spark):
        hist_df = MagicMock()
        hist_df.collect.return_value = [_FakeRow({"version": None})]
        mock_spark.sql.return_value = hist_df
        with pytest.raises(ArchiveOperationError) as exc_info:
            get_delta_history_versions_strict(mock_spark, "/p", "t", 2000)
        assert exc_info.value.reason == "archive_folder_orphan"

    def test_non_numeric_version_raises_archive_operation_error(self, mock_spark):
        hist_df = MagicMock()
        hist_df.collect.return_value = [_FakeRow({"version": "abc"})]
        mock_spark.sql.return_value = hist_df
        with pytest.raises(ArchiveOperationError) as exc_info:
            get_delta_history_versions_strict(mock_spark, "/p", "t", 2000)
        assert exc_info.value.reason == "archive_folder_orphan"

    def test_invalid_table_name_raises_config_error(self, mock_spark):
        bad_table = "bad`name"
        with pytest.raises(ArchiveConfigError):
            get_delta_history_versions_strict(mock_spark, "/p", bad_table, 2000)
        mock_spark.sql.assert_not_called()


class TestCalculateEligibleYears:
    def test_configured_years_short_circuits_source_scan(self, mock_spark):
        result = calculate_eligible_years(
            mock_spark,
            "cat.sch.tbl",
            "wm",
            None,
            [2020, 2021, 2022],
            2024,
        )
        assert result == [2020, 2021, 2022]
        mock_spark.sql.assert_not_called()

    def test_empty_configured_years_triggers_source_scan(self, mock_spark):
        df = MagicMock()
        df.collect.return_value = [_FakeRow({"yr": 2019}), _FakeRow({"yr": 2020})]
        mock_spark.sql.return_value = df
        result = calculate_eligible_years(
            mock_spark,
            "cat.sch.tbl",
            "wm",
            None,
            [],
            2024,
        )
        assert result == [2019, 2020]
        sql = mock_spark.sql.call_args[0][0]
        assert "SELECT DISTINCT YEAR(wm) AS yr FROM cat.sch.tbl" in sql
        assert "wm IS NOT NULL" in sql

    def test_none_configured_years_triggers_source_scan(self, mock_spark):
        df = MagicMock()
        df.collect.return_value = [_FakeRow({"yr": 2018})]
        mock_spark.sql.return_value = df
        assert calculate_eligible_years(
            mock_spark,
            "cat.sch.tbl",
            "wm",
            None,
            None,
            2024,
        ) == [2018]

    def test_boundary_is_inclusive(self, mock_spark):
        df = MagicMock()
        df.collect.return_value = [
            _FakeRow({"yr": 2019}),
            _FakeRow({"yr": 2020}),
            _FakeRow({"yr": 2021}),
            _FakeRow({"yr": 2022}),
        ]
        mock_spark.sql.return_value = df
        assert calculate_eligible_years(
            mock_spark, "cat.sch.tbl", "wm", None, None, 2020,
        ) == [2019, 2020]

    def test_exclusion_clause_appended(self, mock_spark):
        df = MagicMock()
        df.collect.return_value = []
        mock_spark.sql.return_value = df
        calculate_eligible_years(
            mock_spark,
            "cat.sch.tbl",
            "wm",
            "claim_status <> 'void'",
            None,
            2024,
        )
        sql = mock_spark.sql.call_args[0][0]
        assert "wm IS NOT NULL AND (claim_status <> 'void')" in sql

    def test_invalid_watermark_column_raises_config_error(self, mock_spark):
        with pytest.raises(ArchiveConfigError):
            calculate_eligible_years(
                mock_spark, "cat.sch.tbl", "bad-col", None, None, 2024,
            )
        mock_spark.sql.assert_not_called()

    def test_invalid_source_table_raises_config_error(self, mock_spark):
        with pytest.raises(ArchiveConfigError):
            calculate_eligible_years(
                mock_spark, "cat..tbl", "wm", None, None, 2024,
            )
        mock_spark.sql.assert_not_called()

    def test_unparseable_year_raises_operation_error(self, mock_spark):
        df = MagicMock()
        df.collect.return_value = [_FakeRow({"yr": None})]
        mock_spark.sql.return_value = df
        with pytest.raises(ArchiveOperationError) as ei:
            calculate_eligible_years(
                mock_spark, "cat.sch.tbl", "wm", None, None, 2024,
            )
        assert ei.value.operation == "calculate_eligible_years"


class TestDeltaProbeExceptionClassification:
    # Ensures unknown Spark failures (permission, transient) surface with their
    # original diagnostic instead of being mislabeled missing/orphan.
    def test_unknown_analysis_exception_propagates(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException(
            "PERMISSION_DENIED: missing READ"
        )
        with pytest.raises(_AnalysisException, match="PERMISSION_DENIED"):
            archive_state_and_count(mock_spark, "/p", "tbl", 2022)

    def test_delta_missing_error_classifies_orphan(self, mock_spark):
        mock_spark.sql.side_effect = _AnalysisException(
            "DELTA_MISSING_DELTA_TABLE: path is not a Delta table"
        )
        assert archive_state_and_count(mock_spark, "/p", "tbl", 2022) == ("ORPHAN", 0)
