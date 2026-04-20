import json
from unittest.mock import MagicMock

import pytest

from src.exceptions import ArchiveConfigError


def _mock_spark_with_rows(rows):
    spark = MagicMock()
    mock_df = MagicMock()
    mock_rows = [MagicMock(asDict=MagicMock(return_value=r)) for r in rows]
    mock_df.collect.return_value = mock_rows
    spark.sql.return_value = mock_df
    return spark


def _mock_spark_multi_query(query_results):
    spark = MagicMock()
    results = []
    for rows in query_results:
        mock_rows = [MagicMock(asDict=MagicMock(return_value=r)) for r in rows]
        mock_df = MagicMock()
        mock_df.collect.return_value = mock_rows
        results.append(mock_df)
    spark.sql.side_effect = results
    return spark


def _valid_schema_template_row(**overrides):
    base = {
        "schema_id": "default",
        "is_active": True,
        "source_catalog": "c",
        "source_schema": "s",
        "min_table_size_gb": 1.0,
    }
    base.update(overrides)
    return base


def _valid_table_row(**overrides):
    base = {
        "table_id": "t1",
        "is_active": True,
        "source_catalog": "c",
        "source_schema": "s",
        "source_table": "tbl",
        "watermark_column": "dt",
        "archive_base_path": "/p",
        "exclusion_conditions": None,
    }
    base.update(overrides)
    return base


def _valid_settings_row(**overrides):
    base = {
        "audit_catalog": "ac",
        "audit_schema": "asch",
        "default_retention_years": 7,
        "archive_base_path_prefix": "abfss://x",
        "schema_templates_table": "c.cfg.tmpl",
        "table_configs_table": "c.cfg.tables",
    }
    base.update(overrides)
    return base


class TestLoadTableConfigs:
    def test_builds_sql_active_only_and_returns_dicts(self):
        from src import config

        rows = [_valid_table_row(table_id="a"), _valid_table_row(table_id="b")]
        spark = _mock_spark_with_rows(rows)
        out = config.load_table_configs(
            spark, "cat.cfg.table_cfgs", active_only=True, filter_expr=None
        )
        spark.sql.assert_called_once()
        call = spark.sql.call_args[0][0]
        assert "FROM cat.cfg.table_cfgs" in call
        assert "is_active = true" in call
        assert len(out) == 2
        assert out[0]["table_id"] == "a"

    def test_filter_expr_appended_with_and(self):
        from src import config

        rows = [_valid_table_row(table_id="only")]
        spark = _mock_spark_with_rows(rows)
        config.load_table_configs(
            spark, "t", active_only=True, filter_expr="source_catalog = 'x'"
        )
        sql = spark.sql.call_args[0][0]
        assert "source_catalog = 'x'" in sql
        assert " AND " in sql

    def test_active_only_false_omits_is_active_clause(self):
        from src import config

        rows = [_valid_table_row()]
        spark = _mock_spark_with_rows(rows)
        config.load_table_configs(spark, "t", active_only=False)
        sql = spark.sql.call_args[0][0]
        assert "is_active" not in sql

    def test_raises_on_duplicate_table_id(self):
        from src import config

        rows = [
            _valid_table_row(table_id="dup"),
            _valid_table_row(table_id="dup", source_table="other"),
        ]
        spark = _mock_spark_with_rows(rows)
        with pytest.raises(ArchiveConfigError):
            config.load_table_configs(spark, "t", active_only=False)

    def test_raises_on_invalid_exclusion_operator(self):
        from src import config

        conds = json.dumps(
            [
                {
                    "name": "n",
                    "type": "same_table",
                    "column": "c",
                    "operator": "bad_op",
                    "value": 1,
                }
            ]
        )
        rows = [_valid_table_row(exclusion_conditions=conds)]
        spark = _mock_spark_with_rows(rows)
        with pytest.raises(ArchiveConfigError):
            config.load_table_configs(spark, "t", active_only=False)

    def test_custom_sql_without_placeholder_raises(self):
        from src import config

        conds = json.dumps(
            [
                {
                    "name": "n",
                    "type": "custom_sql",
                    "column": None,
                    "operator": "equals",
                    "value": "SELECT 1",
                }
            ]
        )
        rows = [_valid_table_row(exclusion_conditions=conds)]
        spark = _mock_spark_with_rows(rows)
        with pytest.raises(ArchiveConfigError):
            config.load_table_configs(spark, "t", active_only=False)

    def test_accepts_valid_operators_and_custom_sql_placeholder(self):
        from src import config

        for op in (
            "equals",
            "not_equals",
            "in",
            "within_years",
            "within_months",
            "greater_than",
            "is_not_null",
        ):
            conds = json.dumps(
                [
                    {
                        "name": "n",
                        "type": "same_table",
                        "column": "c",
                        "operator": op,
                        "value": 1 if op != "is_not_null" else None,
                    }
                ]
            )
            rows = [_valid_table_row(table_id=f"id_{op}", exclusion_conditions=conds)]
            spark = _mock_spark_with_rows(rows)
            out = config.load_table_configs(spark, "t", active_only=False)
            assert len(out) == 1

        cs = json.dumps(
            [
                {
                    "name": "cs",
                    "type": "custom_sql",
                    "column": "",
                    "operator": "equals",
                    "value": "x {source_alias}.y = 1",
                }
            ]
        )
        spark2 = _mock_spark_with_rows([_valid_table_row(table_id="cs1", exclusion_conditions=cs)])
        assert len(config.load_table_configs(spark2, "t", active_only=False)) == 1


class TestLoadSettings:
    def test_returns_dict_and_validates_required(self):
        from src import config

        row = _valid_settings_row()
        spark = _mock_spark_with_rows([row])
        s = config.load_settings(spark, "g.settings")
        spark.sql.assert_called_once_with("SELECT * FROM g.settings")
        for k in (
            "audit_catalog",
            "audit_schema",
            "default_retention_years",
            "archive_base_path_prefix",
            "schema_templates_table",
            "table_configs_table",
        ):
            assert k in s
            assert s[k] == row[k]

    def test_empty_raises(self):
        from src import config

        spark = _mock_spark_with_rows([])
        with pytest.raises(ArchiveConfigError):
            config.load_settings(spark, "g")

    def test_missing_field_raises(self):
        from src import config

        row = _valid_settings_row()
        del row["audit_catalog"]
        spark = _mock_spark_with_rows([row])
        with pytest.raises(ArchiveConfigError):
            config.load_settings(spark, "g")

    def test_invalid_timezone_raises(self):
        from src import config

        row = _valid_settings_row(timezone="Not/A_Real_Zone_999")
        spark = _mock_spark_with_rows([row])
        with pytest.raises(ArchiveConfigError):
            config.load_settings(spark, "g")

    def test_valid_timezone_accepted(self):
        from src import config

        row = _valid_settings_row(timezone="America/New_York")
        spark = _mock_spark_with_rows([row])
        s = config.load_settings(spark, "g")
        assert s["timezone"] == "America/New_York"


class TestLoadSchemaTemplates:
    def test_returns_list_validates_min_table_size_gb(self):
        from src import config

        rows = [
            _valid_schema_template_row(
                id=1,
                source_catalog="c1",
                source_schema="s1",
                min_table_size_gb=0,
            ),
            _valid_schema_template_row(
                id=2,
                schema_id="other",
                source_catalog="c2",
                source_schema="s2",
                min_table_size_gb=1.5,
            ),
        ]
        spark = _mock_spark_with_rows(rows)
        out = config.load_schema_templates(spark, "c.t.tmpl")
        sql = spark.sql.call_args[0][0]
        assert "FROM c.t.tmpl" in sql
        assert "is_active = true" in sql
        assert len(out) == 2
        assert out[0]["min_table_size_gb"] == 0

    def test_missing_min_table_size_gb_raises(self):
        from src import config

        row = _valid_schema_template_row()
        del row["min_table_size_gb"]
        spark = _mock_spark_with_rows([row])
        with pytest.raises(ArchiveConfigError):
            config.load_schema_templates(spark, "t")

    def test_negative_min_table_size_gb_raises(self):
        from src import config

        spark = _mock_spark_with_rows(
            [_valid_schema_template_row(min_table_size_gb=-1)]
        )
        with pytest.raises(ArchiveConfigError):
            config.load_schema_templates(spark, "t")

    def test_schema_id_returns_single_active_template(self):
        from src import config

        row = _valid_schema_template_row(
            schema_id="claims",
            source_catalog="cat",
            source_schema="sch",
            min_table_size_gb=2.0,
        )
        spark = _mock_spark_with_rows([row])
        out = config.load_schema_templates(spark, "c.s.t", schema_id="claims")
        assert out == [row]
        sql = spark.sql.call_args[0][0]
        assert "schema_id = 'claims'" in sql
        assert "is_active = true" in sql

    def test_schema_id_inactive_raises(self):
        from src import config

        spark = _mock_spark_multi_query([[], [{"x": 1}]])
        with pytest.raises(ArchiveConfigError) as exc_info:
            config.load_schema_templates(spark, "c.s.t", schema_id="claims")
        assert "inactive" in str(exc_info.value).lower()

    def test_schema_id_not_found_raises(self):
        from src import config

        spark = _mock_spark_multi_query([[], []])
        with pytest.raises(ArchiveConfigError) as exc_info:
            config.load_schema_templates(spark, "c.s.t", schema_id="claims")
        assert "not found" in str(exc_info.value).lower()

    def test_schema_id_duplicate_raises(self):
        from src import config

        spark = _mock_spark_multi_query(
            [
                [
                    {"schema_id": "claims"},
                    {"schema_id": "claims"},
                ]
            ]
        )
        with pytest.raises(ArchiveConfigError) as exc_info:
            config.load_schema_templates(spark, "c.s.t", schema_id="claims")
        assert "duplicate" in str(exc_info.value).lower()

    def test_no_schema_id_returns_active_only(self):
        from src import config

        rows = [
            _valid_schema_template_row(
                schema_id="a",
                source_catalog="c1",
                source_schema="s1",
                min_table_size_gb=0.5,
            ),
            _valid_schema_template_row(
                schema_id="b",
                source_catalog="c2",
                source_schema="s2",
                min_table_size_gb=1.0,
            ),
        ]
        spark = _mock_spark_with_rows(rows)
        out = config.load_schema_templates(spark, "c.cfg.tmpl")
        sql = spark.sql.call_args[0][0]
        assert "is_active = true" in sql
        assert out == rows

    def test_no_schema_id_empty_table_returns_empty_list(self):
        from src import config

        spark = _mock_spark_with_rows([])
        out = config.load_schema_templates(spark, "c.cfg.tmpl")
        assert out == []

    def test_schema_id_with_quote_is_escaped(self):
        from src import config

        row = _valid_schema_template_row(
            schema_id="a'b",
            source_catalog="cat",
            source_schema="sch",
        )
        spark = _mock_spark_with_rows([row])
        out = config.load_schema_templates(spark, "c.s.t", schema_id="a'b")
        sql = spark.sql.call_args[0][0]
        assert "a''b" in sql
        assert out == [row]

    def test_no_schema_id_duplicate_catalog_schema_raises(self):
        from src import config

        rows = [
            _valid_schema_template_row(schema_id="a"),
            _valid_schema_template_row(schema_id="b"),
        ]
        spark = _mock_spark_with_rows(rows)
        with pytest.raises(ArchiveConfigError) as exc_info:
            config.load_schema_templates(spark, "t")
        assert "duplicate" in str(exc_info.value).lower()


class TestGetActiveTables:
    def test_filters_true_only(self):
        from src import config

        cfgs = [
            {"is_active": True, "table_id": "a"},
            {"is_active": False, "table_id": "b"},
            {"is_active": True, "table_id": "c"},
        ]
        out = config.get_active_tables(cfgs)
        assert [x["table_id"] for x in out] == ["a", "c"]


class TestMergeSettings:
    def test_inherits_retention_from_global(self):
        from src import config

        g = {"default_retention_years": 7}
        t = {"table_id": "x"}
        m = config.merge_settings(g, t)
        assert m["retention_years"] == 7

    def test_table_retention_overrides(self):
        from src import config

        g = {"default_retention_years": 7}
        t = {"table_id": "x", "retention_years": 3}
        m = config.merge_settings(g, t)
        assert m["retention_years"] == 3

    def test_inherits_timezone_from_global(self):
        from src import config

        g = {"default_retention_years": 7, "timezone": "US/Eastern"}
        t = {"table_id": "x"}
        m = config.merge_settings(g, t)
        assert m["timezone"] == "US/Eastern"

    def test_table_timezone_overrides_global(self):
        from src import config

        g = {"default_retention_years": 7, "timezone": "US/Eastern"}
        t = {"table_id": "x", "timezone": "US/Pacific"}
        m = config.merge_settings(g, t)
        assert m["timezone"] == "US/Pacific"

    def test_timezone_defaults_to_utc(self):
        from src import config

        g = {"default_retention_years": 7}
        t = {"table_id": "x"}
        m = config.merge_settings(g, t)
        assert m["timezone"] == "UTC"


class TestValidateTableConfigFields:
    def test_missing_source_catalog_raises(self):
        from src import config

        r = _valid_table_row()
        del r["source_catalog"]
        with pytest.raises(ArchiveConfigError):
            config.validate_table_config_dict(r)
