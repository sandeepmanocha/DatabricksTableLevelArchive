import pytest

from src.exceptions import ArchiveConfigError
from src.conditions import (
    build_exclusion_clause,
    build_individual_condition_sql,
    get_condition_names,
)
from src.utils import sql_quote


class TestSameTableOperators:
    @pytest.mark.parametrize(
        "name,operator,column,value,catalog,schema,alias,expected_sql",
        [
            ("eq1", "equals", "status", "ACTIVE", "cat", "sch", "src", "src.status = 'ACTIVE'"),
            ("ne1", "not_equals", "region", "XX", "c", "s", "a", "a.region != 'XX'"),
            ("in1", "in", "code", "'A','B','C'", "c", "s", "t1", "t1.code IN ('A','B','C')"),
            (
                "wy",
                "within_years",
                "svc_date",
                7,
                "c",
                "s",
                "x",
                "x.svc_date >= date_add(current_date(), -7 * 365)",
            ),
            (
                "wm",
                "within_months",
                "updated_at",
                12,
                "c",
                "s",
                "x",
                "x.updated_at >= add_months(current_date(), -12)",
            ),
            ("gt", "greater_than", "amt", 1000, "c", "s", "p", "p.amt > 1000"),
            ("nn", "is_not_null", "ext_id", None, "c", "s", "p", "p.ext_id IS NOT NULL"),
        ],
    )
    def test_same_table_operator_sql(
        self, name, operator, column, value, catalog, schema, alias, expected_sql
    ):
        c = {
            "name": name,
            "type": "same_table",
            "column": column,
            "operator": operator,
            "value": value,
        }
        assert build_individual_condition_sql(c, catalog, schema, alias) == expected_sql


class TestCustomSqlPlaceholders:
    @pytest.mark.parametrize(
        "name,value_template,catalog,schema,alias,expected",
        [
            ("cs1", "{source_alias}.id IS NOT NULL", "cat", "sch", "alias1", "alias1.id IS NOT NULL"),
            (
                "cs2",
                "{source_catalog}.shared.lookup.x = 1",
                "my_catalog",
                "sch",
                "a",
                "my_catalog.shared.lookup.x = 1",
            ),
            (
                "cs3",
                "EXISTS (SELECT 1 FROM {source_schema}.ref r WHERE r.k = {source_alias}.k)",
                "c",
                "schema_beta",
                "t",
                "EXISTS (SELECT 1 FROM schema_beta.ref r WHERE r.k = t.k)",
            ),
        ],
    )
    def test_substitutes_placeholders(
        self, name, value_template, catalog, schema, alias, expected
    ):
        c = {
            "name": name,
            "type": "custom_sql",
            "column": None,
            "operator": None,
            "value": value_template,
        }
        assert build_individual_condition_sql(c, catalog, schema, alias) == expected


class TestBuildExclusionClause:
    @pytest.mark.parametrize("conditions", [[], None])
    def test_empty_or_none_returns_empty_string(self, conditions):
        assert build_exclusion_clause(conditions, "c", "s", "a") == ""

    def test_combined_not_and_pattern(self):
        c1 = {
            "name": "n1",
            "type": "same_table",
            "column": "x",
            "operator": "equals",
            "value": "1",
        }
        c2 = {
            "name": "n2",
            "type": "same_table",
            "column": "y",
            "operator": "greater_than",
            "value": 0,
        }
        out = build_exclusion_clause([c1, c2], "cat", "sch", "t")
        assert out == "NOT (t.x = '1') AND NOT (t.y > 0)"


class TestGetConditionNames:
    def test_extracts_names_in_order(self):
        conds = [
            {"name": "first", "type": "same_table", "column": "a", "operator": "equals", "value": "v"},
            {"name": "second", "type": "custom_sql", "column": None, "operator": None, "value": "1=1"},
        ]
        assert get_condition_names(conds) == ["first", "second"]

    @pytest.mark.parametrize("conditions", [[], None])
    def test_empty_or_none_returns_empty_list(self, conditions):
        assert get_condition_names(conditions) == []


class TestBuildIndividualConditionSql:
    def test_returns_raw_predicate_not_wrapped_in_not(self):
        c = {
            "name": "raw",
            "type": "same_table",
            "column": "z",
            "operator": "equals",
            "value": "Q",
        }
        sql = build_individual_condition_sql(c, "c", "s", "tbl")
        assert sql == "tbl.z = 'Q'"
        assert not sql.strip().upper().startswith("NOT")


class TestSqlQuotingAndIntValidation:
    def test_equals_escapes_apostrophe(self):
        c = {
            "name": "n",
            "type": "same_table",
            "column": "name",
            "operator": "equals",
            "value": "O'Brien",
        }
        sql = build_individual_condition_sql(c, "c", "s", "t")
        assert sql == "t.name = 'O''Brien'"

    def test_equals_quotes_injection_attempt(self):
        payload = "1; DROP TABLE --"
        c = {
            "name": "n",
            "type": "same_table",
            "column": "name",
            "operator": "equals",
            "value": payload,
        }
        sql = build_individual_condition_sql(c, "c", "s", "t")
        assert sql.startswith("t.name = '")
        assert sql == f"t.name = {sql_quote(payload)}"

    def test_within_years_rejects_non_numeric_string(self):
        c = {
            "name": "n",
            "type": "same_table",
            "column": "d",
            "operator": "within_years",
            "value": "seven",
        }
        with pytest.raises(ArchiveConfigError, match="field='value'"):
            build_individual_condition_sql(c, "c", "s", "a")

    def test_within_years_rejects_bool(self):
        c = {
            "name": "n",
            "type": "same_table",
            "column": "d",
            "operator": "within_years",
            "value": True,
        }
        with pytest.raises(ArchiveConfigError, match="field='value'"):
            build_individual_condition_sql(c, "c", "s", "a")

    def test_in_with_list_quotes_each_value(self):
        c = {
            "name": "n",
            "type": "same_table",
            "column": "ref",
            "operator": "in",
            "value": ["A", "O'Brien"],
        }
        sql = build_individual_condition_sql(c, "c", "s", "t1")
        assert sql == "t1.ref IN ('A', 'O''Brien')"
