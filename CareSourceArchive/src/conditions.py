from src.exceptions import ArchiveConfigError


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


def _substitute_custom_sql(value, source_catalog, source_schema, source_alias):
    s = "" if value is None else str(value)
    return (
        s.replace("{source_alias}", source_alias)
        .replace("{source_catalog}", source_catalog)
        .replace("{source_schema}", source_schema)
    )


def _same_table_predicate(column, operator, value, source_alias):
    ref = f"{source_alias}.{column}"
    if operator == "equals":
        return f"{ref} = '{value}'"
    if operator == "not_equals":
        return f"{ref} != '{value}'"
    if operator == "in":
        return f"{ref} IN ({value})"
    if operator == "within_years":
        return f"{ref} >= date_add(current_date(), -{value} * 365)"
    if operator == "within_months":
        return f"{ref} >= add_months(current_date(), -{value})"
    if operator == "greater_than":
        return f"{ref} > {value}"
    if operator == "is_not_null":
        return f"{ref} IS NOT NULL"
    raise ArchiveConfigError(field="operator", source="conditions")


def build_individual_condition_sql(condition, source_catalog, source_schema, source_alias):
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


def build_exclusion_clause(conditions, source_catalog, source_schema, source_alias):
    if not conditions:
        return ""
    parts = [
        f"NOT ({build_individual_condition_sql(c, source_catalog, source_schema, source_alias)})"
        for c in conditions
    ]
    return " AND ".join(parts)


def get_condition_names(conditions):
    if not conditions:
        return []
    return [c["name"] for c in conditions]
