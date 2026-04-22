"""
Shared utilities used across the archive pipeline.

This module is the common toolbox: safe SQL literal quoting, identifier
validation, small Spark/Delta helpers, archive path construction, logging
setup, and the shared run-context types. Keeping these helpers in one place
ensures every module builds SQL and reads rows the same way.
"""

import datetime as datetime_module
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from src.exceptions import ArchiveConfigError, ArchiveError

try:
    from pyspark.sql.utils import AnalysisException as _AnalysisException  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - fallback when pyspark unavailable in tests
    _AnalysisException = Exception


class SettingsDict(TypedDict, total=False):
    audit_catalog: str
    audit_schema: str
    default_retention_years: Any
    archive_base_path_prefix: str
    schema_templates_table: str
    table_configs_table: str
    timezone: str


class JobContextDict(TypedDict, total=False):
    job_id: str
    run_id: str
    task_key: str
    job_name: str


_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_safe_identifier(name: str) -> bool:
    """
    Description: Return whether the string is a safe unquoted SQL identifier token.
    Parameters: name: candidate identifier string
    Return: True if the name matches the safe pattern, else False
    """
    return isinstance(name, str) and bool(_SAFE_IDENT_RE.fullmatch(name))


def validate_identifier(name: str, *, field: str = "identifier", table_id: str | None = None) -> str:
    """
    Description: Ensure a single identifier is safe, raising ArchiveConfigError if not.
    Parameters: name: identifier to validate; field: error field label; table_id: optional table id for errors
    Return: The validated identifier string unchanged
    """
    if not is_safe_identifier(name):
        raise ArchiveConfigError(field=field, table_id=table_id)
    return name


def validate_fq_identifier(fq: str, *, field: str = "table", table_id: str | None = None) -> str:
    """
    Description: Validate a dotted (possibly backtick-quoted) catalog.schema.table string.
    Parameters: fq: fully qualified name string; field: error field label; table_id: optional table id for errors
    Return: The validated fq string unchanged
    """
    if not isinstance(fq, str) or not fq.strip():
        raise ArchiveConfigError(field=field, table_id=table_id)
    parts = fq.split(".")
    if not (1 <= len(parts) <= 3):
        raise ArchiveConfigError(field=field, table_id=table_id)
    for p in parts:
        inner = p[1:-1] if len(p) >= 2 and p.startswith("`") and p.endswith("`") else p
        if not is_safe_identifier(inner):
            raise ArchiveConfigError(field=field, table_id=table_id)
    return fq


@dataclass
class RunContext:
    settings: SettingsDict
    job_context: JobContextDict
    archive_run_id: str


def generate_archive_run_id() -> str:
    """
    Description: Create a new unique id string for an archive run.
    Parameters: none
    Return: A new UUID4 string
    """
    return str(uuid.uuid4())


def build_archive_path(base_path, table_name, year) -> str:
    """
    Description: Join base path, table name, and year into an archive folder path.
    Parameters: base_path: root path string; table_name: table segment; year: year segment
    Return: Normalized archive path string
    """
    return f"{base_path.rstrip('/')}/{table_name}/year_{year}"


def build_full_table_name(catalog, schema, table) -> str:
    """
    Description: Build a three-part fully qualified table name from parts.
    Parameters: catalog: catalog name; schema: schema name; table: table name
    Return: Dot-separated catalog.schema.table string
    """
    return f"{catalog}.{schema}.{table}"


def create_schema_if_not_exists(spark, catalog, schema) -> None:
    """
    Description: Run CREATE SCHEMA IF NOT EXISTS for a validated catalog and schema.
    Parameters: spark: Spark session; catalog: catalog name; schema: schema name
    Return: None
    """
    validate_identifier(catalog, field="catalog")
    validate_identifier(schema, field="schema")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")


def archive_folder_exists(dbutils, base_path, table_name, year) -> bool:
    """
    Description: Return whether the archive year folder exists on the filesystem.
    Parameters: dbutils: workspace file system API; base_path: archive root; table_name: table; year: year
    Return: True if the path lists successfully, False if missing, else re-raises
    """
    path = build_archive_path(base_path, table_name, year)
    try:
        dbutils.fs.ls(path)
        return True
    except Exception as exc:
        if ArchiveError.is_not_found(exc):
            return False
        raise


def row_value(row, field, default=None) -> Any:
    """Safe field extraction from a Spark Row, dict, or similar object."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(field, default)
    if hasattr(row, "asDict"):
        return row.asDict().get(field, default)
    try:
        return row[field]
    except (KeyError, IndexError):
        return default


def row_to_dict(row) -> dict:
    """Convert a Spark Row (or dict) to a plain dict."""
    if isinstance(row, dict):
        return row
    if hasattr(row, "asDict"):
        return row.asDict()
    return {}


def collect_column(spark, sql, field) -> list:
    """Run a SQL query and return a list of values from a single column."""
    rows = spark.sql(sql).collect()
    return [row_value(r, field) for r in rows]


def ensure_table_exists(spark, fq_table) -> None:
    """Verify a Delta table exists via DESCRIBE TABLE; raise if not."""
    validate_fq_identifier(fq_table, field="table")
    try:
        spark.sql(f"DESCRIBE TABLE {fq_table}").collect()
    except _AnalysisException as exc:
        raise ArchiveConfigError(
            msg=f"Table '{fq_table}' does not exist or is inaccessible."
        ) from exc


_HANDLER_NAME = "caresource_archive"


class ArchiveRunFilter(logging.Filter):
    def __init__(self, archive_run_id) -> None:
        """
        Description: Attach a fixed archive run id for downstream log format fields.
        Parameters: archive_run_id: string id stored on the filter instance
        Return: None
        """
        super().__init__()
        self.archive_run_id = archive_run_id

    def filter(self, record) -> bool:
        """
        Description: Enrich the log record with table and archive_run_id for formatting.
        Parameters: record: logging LogRecord to mutate
        Return: True so the record is always emitted
        """
        record.table = getattr(record, "table", "")
        record.archive_run_id = self.archive_run_id
        return True


def configure_logging(archive_run_id) -> None:
    """
    Description: Configure root logging with a stream handler and archive run filter.
    Parameters: archive_run_id: id passed into ArchiveRunFilter for log lines
    Return: None
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for existing in list(root.handlers):
        if getattr(existing, "name", None) == _HANDLER_NAME:
            root.removeHandler(existing)
    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(archive_run_id)s] [%(table)s] %(message)s"
        )
    )
    handler.addFilter(ArchiveRunFilter(archive_run_id))
    root.addHandler(handler)



def sql_quote(s: str) -> str:
    """Quote a string value for SQL, escaping single quotes."""
    return "'" + str(s).replace("'", "''") + "'"


def sql_str_or_null(val) -> str:
    """Quote a string value or return NULL."""
    if val is None:
        return "NULL"
    return sql_quote(str(val))


def sql_date_or_null(val) -> str:
    """Format a date or datetime value as a SQL DATE literal, or NULL."""
    if val is None:
        return "NULL"
    if isinstance(val, datetime_module.datetime):
        val = val.date()
    if not isinstance(val, datetime_module.date):
        raise TypeError(f"sql_date_or_null expected date/datetime/None, got {type(val).__name__}")
    return f"DATE '{val.isoformat()}'"


def sql_int(val: int) -> str:
    """Cast to int for SQL.

    Raises TypeError when given a bool (bool is an int subclass in Python but
    booleans should not be silently coerced into numeric SQL literals).
    Floats are accepted and truncated — callers that want strict integer
    handling should cast upstream.
    """
    if isinstance(val, bool):
        raise TypeError("sql_int does not accept bool")
    return str(int(val))


def sql_int_or_null(val) -> str:
    """Cast to int or return NULL."""
    if val is None:
        return "NULL"
    return str(int(val))


def sql_bool(b: bool) -> str:
    """SQL boolean literal."""
    return "true" if b else "false"


def sql_expr(expr: str) -> str:
    """Passthrough for fixed internal SQL expressions like current_timestamp().
    Not for user-supplied input — no escaping is applied."""
    return expr


def build_insert_values_sql(table: str, columns: list[str], values: list[str]) -> str:
    """Build a single-row INSERT INTO ... VALUES (...) statement.

    Args:
        table: Fully-qualified table name.
        columns: Column names.
        values: Already-quoted SQL value strings (same length as columns).

    Raises:
        ValueError: If columns and values have different lengths, or if either is empty.
    """
    if not columns:
        raise ValueError("columns must not be empty")
    if len(columns) != len(values):
        raise ValueError(f"columns ({len(columns)}) and values ({len(values)}) length mismatch")
    validate_fq_identifier(table, field="table")
    for col in columns:
        validate_identifier(col, field="column")
    cols = ", ".join(columns)
    vals = ", ".join(values)
    return f"INSERT INTO {table} ({cols}) VALUES ({vals})"


def source_fq_from_config(table_config) -> str:
    """Build fully-qualified source table name from a config dict."""
    return build_full_table_name(
        table_config["source_catalog"],
        table_config["source_schema"],
        table_config["source_table"],
    )


def archive_path_from_config(table_config, year) -> str:
    """Build archive Delta path from a config dict and year."""
    return build_archive_path(
        table_config["archive_base_path"], table_config["source_table"], year
    )


def ensure_table_with_setup_message(spark, fq_table, label="Table") -> None:
    """Verify a table exists; raise with a setup-specific message if not."""
    try:
        ensure_table_exists(spark, fq_table)
    except ArchiveConfigError as exc:
        raise ArchiveConfigError(
            msg=f"{label} {fq_table} does not exist. "
                "Run the setup_config_tables job first."
        ) from exc


def spark_count(spark, sql, column_name: str = "count") -> int:
    """Run a SQL that returns a single row and return the int result.

    The column name defaults to ``count``; pass a custom name when the caller
    aliases to something else. Raises ArchiveError if the query returns no
    rows.
    """
    row = spark.sql(sql).first()
    if row is None:
        raise ArchiveError(f"count query returned no rows: {sql}")
    return int(row[column_name])


def build_multi_insert_values_sql(table: str, columns: list[str], rows: list[list[str]]) -> str:
    """Build a multi-row INSERT INTO ... VALUES (...), (...) statement.

    Args:
        table: Fully-qualified table name.
        columns: Column names.
        rows: List of value lists, each already-quoted (same length as columns).

    Raises:
        ValueError: If rows is empty, columns is empty, or any row length mismatches columns.
    """
    if not columns:
        raise ValueError("columns must not be empty")
    if not rows:
        raise ValueError("rows must not be empty")
    for i, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError(f"row {i} has {len(row)} values, expected {len(columns)}")
    validate_fq_identifier(table, field="table")
    for col in columns:
        validate_identifier(col, field="column")
    cols = ", ".join(columns)
    row_strs = [f"({', '.join(row)})" for row in rows]
    return f"INSERT INTO {table} ({cols}) VALUES {', '.join(row_strs)}"
