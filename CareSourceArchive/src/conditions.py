"""
Builds SQL for exclusion conditions.

Per-table exclusion rules (defined in configuration) describe rows that must
never be archived. This module normalizes those rule definitions and turns
them into safe SQL predicates that the archiver applies when selecting
source rows.
"""

from src.exceptions import ArchiveConfigError
from src.utils import sql_quote


def _require_int(value, *, field) -> int:
    """
    Description: Coerces a value to int or raises ArchiveConfigError.
    Parameters: value: raw scalar to convert; field: config field name for error context
    Return: int
    """
    if isinstance(value, bool):
        raise ArchiveConfigError(field=field, source="conditions")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ArchiveConfigError(field=field, source="conditions") from exc


def normalize_condition(item) -> dict:
    """Convert a Spark Row, dict, or similar condition to a plain dict with canonical keys."""
    if hasattr(item, "asDict"):
        d = item.asDict()
    elif isinstance(item, dict):
        d = dict(item)
    else:
        d = dict(item)
    if "scope" in d and "type" not in d:
        d["type"] = d.pop("scope")
    d.pop("sql", None)
    return d


def _substitute_custom_sql(value, source_catalog, source_schema, source_alias) -> str:
    """
    Description: Replaces {source_alias}, {source_catalog}, and {source_schema} placeholders in a SQL string.
    Parameters: value: template text or None; source_catalog: source catalog name; source_schema: source schema name; source_alias: table alias for the source
    Return: str
    """
    s = "" if value is None else str(value)
    return (
        s.replace("{source_alias}", source_alias)
        .replace("{source_catalog}", source_catalog)
        .replace("{source_schema}", source_schema)
    )


def _same_table_predicate(column, operator, value, source_alias) -> str:
    """Build a same-table exclusion predicate fragment.

    For operator ``in``: ``list`` / ``tuple`` values are emitted as a quoted
    ``IN`` list. A **string** ``value`` is still interpreted as raw SQL inside
    the parentheses (legacy configs / tests); callers must ensure it is trusted
    or migrate to a list of Python values.
    """
    ref = f"{source_alias}.{column}"
    if operator == "equals":
        return f"{ref} = {sql_quote(value)}"
    if operator == "not_equals":
        return f"{ref} != {sql_quote(value)}"
    if operator == "in":
        if isinstance(value, (list, tuple)):
            inner = ", ".join(sql_quote(v) for v in value)
            return f"{ref} IN ({inner})"
        if isinstance(value, str):
            return f"{ref} IN ({value})"
        raise ArchiveConfigError(field="value", source="conditions")
    if operator == "within_years":
        n = _require_int(value, field="value")
        return f"{ref} >= date_add(current_date(), -{n} * 365)"
    if operator == "within_months":
        n = _require_int(value, field="value")
        return f"{ref} >= add_months(current_date(), -{n})"
    if operator == "greater_than":
        n = _require_int(value, field="value")
        return f"{ref} > {n}"
    if operator == "is_not_null":
        return f"{ref} IS NOT NULL"
    raise ArchiveConfigError(field="operator", source="conditions")


def build_individual_condition_sql(condition, source_catalog, source_schema, source_alias) -> str:
    """
    Description: Builds the SQL predicate for a single normalized exclusion condition.
    Parameters: condition: condition dict with type and fields; source_catalog: source catalog name; source_schema: source schema name; source_alias: table alias for the source
    Return: str
    """
    ctype = condition.get("type")
    if ctype == "custom_sql":
        return _substitute_custom_sql(
            condition.get("value"), source_catalog, source_schema, source_alias
        )
    if ctype == "same_table":
        return _same_table_predicate(
            condition["column"],
            condition["operator"],
            condition["value"],
            source_alias,
        )
    raise ArchiveConfigError(field="type", source="conditions")


def build_exclusion_clause(conditions, source_catalog, source_schema, source_alias) -> str:
    """
    Description: Builds a combined AND clause of negated predicates for all exclusion conditions.
    Parameters: conditions: list of condition dicts; source_catalog: source catalog name; source_schema: source schema name; source_alias: table alias for the source
    Return: str
    """
    if not conditions:
        return ""
    parts = [
        f"NOT ({build_individual_condition_sql(c, source_catalog, source_schema, source_alias)})"
        for c in conditions
    ]
    return " AND ".join(parts)


def get_condition_names(conditions) -> list:
    """
    Description: Returns the name field from each condition in order.
    Parameters: conditions: list of condition dicts (may be empty)
    Return: list
    """
    if not conditions:
        return []
    return [c["name"] for c in conditions]
