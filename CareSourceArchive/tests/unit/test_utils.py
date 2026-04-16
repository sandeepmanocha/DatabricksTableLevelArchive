import logging
import uuid
from dataclasses import fields
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from src.exceptions import ArchiveConfigError
from src.utils import (
    RunContext,
    archive_folder_exists,
    archive_path_from_config,
    build_archive_path,
    build_full_table_name,
    build_insert_values_sql,
    build_multi_insert_values_sql,
    collect_column,
    configure_logging,
    create_schema_if_not_exists,
    ensure_table_exists,
    ensure_table_with_setup_message,
    generate_archive_run_id,
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


class TestArchiveFolderExists:
    def test_true_when_ls_succeeds(self):
        dbutils = MagicMock()
        dbutils.fs.ls.return_value = [MagicMock()]
        assert archive_folder_exists(dbutils, "/b", "tbl", 2020) is True
        dbutils.fs.ls.assert_called_once_with("/b/tbl/year_2020")

    def test_false_when_path_not_found(self):
        dbutils = MagicMock()
        dbutils.fs.ls.side_effect = Exception(
            "java.io.FileNotFoundException: /b/tbl/year_2021"
        )
        assert archive_folder_exists(dbutils, "/b", "tbl", 2021) is False

    def test_raises_on_non_not_found_error(self):
        dbutils = MagicMock()
        dbutils.fs.ls.side_effect = Exception(
            "PERMISSION_DENIED: User does not have READ VOLUME"
        )
        with pytest.raises(Exception, match="PERMISSION_DENIED"):
            archive_folder_exists(dbutils, "/b", "tbl", 2021)


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
