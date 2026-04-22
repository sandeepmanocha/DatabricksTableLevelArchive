"""
Loads and validates pipeline configuration.

Reads global settings, schema templates, and per-table archive configuration
from their Delta tables, checks that required fields are present and safe,
and produces the merged configuration that the scanner, archiver, and
rehydrator consume at runtime.
"""

import json
import numbers
from zoneinfo import ZoneInfo

from src.conditions import normalize_condition
from src.exceptions import ArchiveConfigError
from src.utils import row_to_dict, sql_quote, validate_identifier

_REQUIRED_SETTINGS_KEYS = (
    "audit_catalog",
    "audit_schema",
    "default_retention_years",
    "archive_base_path_prefix",
    "schema_templates_table",
    "table_configs_table",
)

_REQUIRED_TABLE_CONFIG_KEYS = (
    "source_catalog",
    "source_schema",
    "source_table",
    "archive_base_path",
    "table_id",
    "watermark_column",
)

_EXCLUSION_OPERATORS = frozenset(
    {
        "equals",
        "not_equals",
        "in",
        "within_years",
        "within_months",
        "greater_than",
        "is_not_null",
    }
)

_CONDITION_KEYS = frozenset({"name", "type", "column", "operator", "value"})


def _validate_timezone_string(tz_value, *, field="timezone", table_id=None) -> None:
    """
    Description: Ensures a timezone string is non-empty and valid for ZoneInfo, or raises.
    Parameters: tz_value: optional IANA timezone string; field: error field name; table_id: optional table id for errors
    Return: None
    """
    if tz_value is None:
        return
    if not isinstance(tz_value, str) or not tz_value.strip():
        raise ArchiveConfigError(field=field, table_id=table_id)
    try:
        ZoneInfo(tz_value.strip())
    except Exception as exc:
        raise ArchiveConfigError(
            msg=f"Invalid timezone: {tz_value!r}", field=field, table_id=table_id
        ) from exc


def _require_non_empty_str(d, key, *, table_id=None) -> None:
    """
    Description: Requires a dict value for key to be a non-empty string or raises.
    Parameters: d: mapping; key: key name; table_id: optional table id for errors
    Return: None
    """
    v = d.get(key)
    if v is None or not isinstance(v, str) or not v.strip():
        raise ArchiveConfigError(field=key, table_id=table_id)


def validate_exclusion_conditions(raw, *, table_id=None) -> None:
    """
    Description: Validates exclusion_conditions JSON or list shape, operators, and values.
    Parameters: raw: JSON string, list, or None; table_id: optional table id for errors
    Return: None
    """
    if raw is None:
        return
    if isinstance(raw, str) and not raw.strip():
        return
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArchiveConfigError(
                field="exclusion_conditions", table_id=table_id
            ) from exc
    elif isinstance(raw, list):
        parsed = [normalize_condition(c) for c in raw]
    else:
        raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
    if not isinstance(parsed, list):
        raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
    for cond in parsed:
        if not isinstance(cond, dict):
            raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
        keys = set(cond.keys())
        if not keys >= {"name", "type", "column", "operator", "value"}:
            raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
        ctype = cond["type"]
        if ctype not in ("same_table", "custom_sql"):
            raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
        op = cond["operator"]
        if op not in _EXCLUSION_OPERATORS:
            raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
        if ctype == "same_table":
            col = cond["column"]
            if col is None or (isinstance(col, str) and not col.strip()):
                raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
            if op != "is_not_null" and cond["value"] is None:
                raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)
        if ctype == "custom_sql":
            val = cond["value"]
            if not isinstance(val, str) or "{source_alias}" not in val:
                raise ArchiveConfigError(field="exclusion_conditions", table_id=table_id)


def validate_table_config_dict(d) -> None:
    """
    Description: Validates required keys, identifiers, timezone, and exclusion conditions on a table config.
    Parameters: d: table configuration dict
    Return: None
    """
    tid = d.get("table_id")
    for key in _REQUIRED_TABLE_CONFIG_KEYS:
        _require_non_empty_str(d, key, table_id=tid)
    validate_identifier(
        d["watermark_column"], field="watermark_column", table_id=tid
    )
    if "timezone" in d:
        _validate_timezone_string(d.get("timezone"), field="timezone", table_id=tid)
    validate_exclusion_conditions(d.get("exclusion_conditions"), table_id=tid)


def _assert_unique_table_ids(rows) -> None:
    """
    Description: Ensures each table_id appears at most once across loaded rows.
    Parameters: rows: list of row dicts containing table_id
    Return: None
    """
    seen = set()
    for r in rows:
        tid = r["table_id"]
        if tid in seen:
            raise ArchiveConfigError(field="table_id", table_id=tid)
        seen.add(tid)


def _filter_expr_forbidden(filter_expr: str) -> bool:
    """
    Description: Detects SQL comment or statement-separator tokens disallowed in filter_expr.
    Parameters: filter_expr: SQL WHERE fragment string
    Return: True if forbidden tokens are present, else False
    """
    for tok in (";", "--", "/*", "*/"):
        if tok in filter_expr:
            return True
    return False


def load_table_configs(
    spark,
    table_configs_table,
    active_only=True,
    *,
    source_catalog: str | None = None,
    source_schema: str | None = None,
    table_id: str | None = None,
    filter_expr: str | None = None,
) -> list[dict]:
    """
    Description: Loads table config rows from Delta, applies filters, validates each row, and returns them.
    Parameters: spark: SparkSession; table_configs_table: fully qualified table name; active_only: restrict to active rows; source_catalog: optional catalog filter; source_schema: optional schema filter; table_id: optional id filter; filter_expr: optional SQL predicate fragment
    Return: list of validated table configuration dicts
    """
    q = f"SELECT * FROM {table_configs_table}"
    parts = []
    if active_only:
        parts.append("is_active = true")
    if source_catalog is not None:
        parts.append(f"source_catalog = {sql_quote(source_catalog)}")
    if source_schema is not None:
        parts.append(f"source_schema = {sql_quote(source_schema)}")
    if table_id is not None:
        parts.append(f"table_id = {sql_quote(table_id)}")
    if filter_expr:
        if _filter_expr_forbidden(filter_expr):
            raise ArchiveConfigError(
                field="filter_expr",
                msg="filter_expr must not contain SQL comments or statement separators",
            )
        try:
            spark.sql(
                f"SELECT 1 FROM {table_configs_table} WHERE {filter_expr} LIMIT 0"
            ).collect()
        except Exception as exc:
            raise ArchiveConfigError(
                field="filter_expr",
                msg=f"filter_expr failed to parse: {exc}",
            ) from exc
        parts.append(f"({filter_expr})")
    if parts:
        q += " WHERE " + " AND ".join(parts)
    df = spark.sql(q)
    rows = [row_to_dict(r) for r in df.collect()]
    _assert_unique_table_ids(rows)
    for r in rows:
        validate_table_config_dict(r)
    return rows


def load_settings(spark, config_table) -> dict:
    """
    Description: Reads the global settings row from Delta and validates required fields and types.
    Parameters: spark: SparkSession; config_table: fully qualified global settings table name
    Return: validated global settings dict
    """
    df = spark.sql(f"SELECT * FROM {config_table}")
    collected = df.collect()
    if not collected:
        raise ArchiveConfigError(msg="Global settings table is empty")
    row = row_to_dict(collected[0])
    for key in _REQUIRED_SETTINGS_KEYS:
        if key not in row or row[key] is None:
            raise ArchiveConfigError(field=key)
        if key != "default_retention_years":
            if isinstance(row[key], str) and not str(row[key]).strip():
                raise ArchiveConfigError(field=key)
    if not isinstance(
        row["default_retention_years"], numbers.Real
    ) or isinstance(row["default_retention_years"], bool):
        raise ArchiveConfigError(field="default_retention_years")
    if "timezone" in row:
        _validate_timezone_string(row.get("timezone"), field="timezone")
    return row


def _validate_template_row(d) -> None:
    """Validate min_table_size_gb on a single template row dict."""
    if "min_table_size_gb" not in d or d["min_table_size_gb"] is None:
        raise ArchiveConfigError(field="min_table_size_gb")
    v = d["min_table_size_gb"]
    if not isinstance(v, numbers.Real) or isinstance(v, bool):
        raise ArchiveConfigError(field="min_table_size_gb")
    if v < 0:
        raise ArchiveConfigError(field="min_table_size_gb")


def load_schema_templates(spark, schema_templates_table, schema_id=None) -> list[dict]:
    """
    Description: Loads active schema template rows, optionally one schema_id, with duplicate and value checks.
    Parameters: spark: SparkSession; schema_templates_table: fully qualified templates table name; schema_id: optional single-template filter
    Return: list of validated template row dicts
    """
    if schema_id is not None:
        quoted = sql_quote(schema_id)
        df = spark.sql(
            f"SELECT * FROM {schema_templates_table} "
            f"WHERE schema_id = {quoted} AND is_active = true"
        )
        rows = [row_to_dict(r) for r in df.collect()]
        if len(rows) == 0:
            probe = spark.sql(
                f"SELECT 1 FROM {schema_templates_table} "
                f"WHERE schema_id = {quoted} LIMIT 1"
            )
            if probe.collect():
                raise ArchiveConfigError(
                    msg=f"schema_id '{schema_id}' is inactive"
                )
            raise ArchiveConfigError(
                msg=f"schema_id '{schema_id}' not found"
            )
        if len(rows) > 1:
            raise ArchiveConfigError(
                msg=f"duplicate schema_id '{schema_id}'"
            )
        _validate_template_row(rows[0])
        return rows
    else:
        df = spark.sql(
            f"SELECT * FROM {schema_templates_table} WHERE is_active = true"
        )
        rows = [row_to_dict(r) for r in df.collect()]
        seen = set()
        for d in rows:
            cat = d.get("source_catalog")
            sch = d.get("source_schema")
            if cat is None or sch is None:
                raise ArchiveConfigError(
                    msg="template row missing source_catalog or source_schema"
                )
            key = (cat, sch)
            if key in seen:
                raise ArchiveConfigError(
                    msg=f"duplicate (source_catalog, source_schema): {key}"
                )
            seen.add(key)
        for d in rows:
            _validate_template_row(d)
        return rows


def get_active_tables(configs) -> list[dict]:
    """
    Description: Filters table configs to those marked active.
    Parameters: configs: list of table configuration dicts
    Return: list of configs where is_active is True
    """
    return [c for c in configs if c.get("is_active") is True]


def merge_settings(global_settings, table_config) -> dict:
    """
    Description: Copies table config and fills default retention and timezone from global settings when missing.
    Parameters: global_settings: global settings dict; table_config: per-table configuration dict
    Return: merged configuration dict
    """
    out = dict(table_config)
    if out.get("retention_years") is None:
        out["retention_years"] = global_settings["default_retention_years"]
    if out.get("timezone") is None:
        out["timezone"] = global_settings.get("timezone", "UTC")
    return out
