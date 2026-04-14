import json
import numbers
from zoneinfo import ZoneInfo

from src.exceptions import ArchiveConfigError
from src.utils import row_to_dict, sql_quote

_REQUIRED_SETTINGS_KEYS = (
    "audit_catalog",
    "audit_schema",
    "default_retention_years",
    "archive_base_path_prefix",
    "secret_scope",
    "schema_templates_table",
    "table_configs_table",
)

_REQUIRED_TABLE_CONFIG_KEYS = (
    "source_catalog",
    "source_schema",
    "source_table",
    "archive_base_path",
    "table_id",
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


def _validate_timezone_string(tz_value, *, field="timezone", table_id=None):
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


def _require_non_empty_str(d, key, *, table_id=None):
    v = d.get(key)
    if v is None or not isinstance(v, str) or not v.strip():
        raise ArchiveConfigError(field=key, table_id=table_id)


def _to_condition_dict(item):
    """Convert a Spark Row, dict, or similar to a plain dict with canonical keys."""
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


def validate_exclusion_conditions(raw, *, table_id=None):
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
        parsed = [_to_condition_dict(c) for c in raw]
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


def validate_table_config_dict(d):
    tid = d.get("table_id")
    for key in _REQUIRED_TABLE_CONFIG_KEYS:
        _require_non_empty_str(d, key, table_id=tid)
    if "timezone" in d:
        _validate_timezone_string(d.get("timezone"), field="timezone", table_id=tid)
    validate_exclusion_conditions(d.get("exclusion_conditions"), table_id=tid)


def _assert_unique_table_ids(rows):
    seen = set()
    for r in rows:
        tid = r["table_id"]
        if tid in seen:
            raise ArchiveConfigError(field="table_id", table_id=tid)
        seen.add(tid)


def load_table_configs(
    spark, table_configs_table, active_only=True, filter_expr=None
):
    q = f"SELECT * FROM {table_configs_table}"
    parts = []
    if active_only:
        parts.append("is_active = true")
    if filter_expr:
        parts.append(f"({filter_expr})")
    if parts:
        q += " WHERE " + " AND ".join(parts)
    df = spark.sql(q)
    rows = [row_to_dict(r) for r in df.collect()]
    _assert_unique_table_ids(rows)
    for r in rows:
        validate_table_config_dict(r)
    return rows


def load_settings(spark, config_table):
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


def _validate_template_row(d):
    """Validate min_table_size_gb on a single template row dict."""
    if "min_table_size_gb" not in d or d["min_table_size_gb"] is None:
        raise ArchiveConfigError(field="min_table_size_gb")
    v = d["min_table_size_gb"]
    if not isinstance(v, numbers.Real) or isinstance(v, bool):
        raise ArchiveConfigError(field="min_table_size_gb")
    if v < 0:
        raise ArchiveConfigError(field="min_table_size_gb")


def load_schema_templates(spark, schema_templates_table, schema_id=None):
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


def get_active_tables(configs):
    return [c for c in configs if c.get("is_active") is True]


def merge_settings(global_settings, table_config):
    out = dict(table_config)
    if out.get("retention_years") is None:
        out["retention_years"] = global_settings["default_retention_years"]
    return out
