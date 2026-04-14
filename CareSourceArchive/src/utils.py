import datetime as datetime_module
import logging
import uuid
from dataclasses import dataclass

from src.exceptions import ArchiveConfigError


@dataclass
class RunContext:
    settings: dict
    secrets: dict
    job_context: dict
    archive_run_id: str


def generate_archive_run_id():
    return str(uuid.uuid4())


def build_archive_path(base_path, table_name, year):
    return f"{base_path.rstrip('/')}/{table_name}/year_{year}"


def build_full_table_name(catalog, schema, table):
    return f"{catalog}.{schema}.{table}"


def create_schema_if_not_exists(spark, catalog, schema):
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")


def archive_folder_exists(dbutils, base_path, table_name, year):
    path = build_archive_path(base_path, table_name, year)
    try:
        dbutils.fs.ls(path)
        return True
    except Exception:
        return False


def row_value(row, field, default=None):
    """Safe field extraction from a Spark Row, dict, or similar object."""
    if isinstance(row, dict):
        return row.get(field, default)
    if hasattr(row, "asDict"):
        return row.asDict().get(field, default)
    try:
        return row[field]
    except (KeyError, IndexError, TypeError):
        return default


def row_to_dict(row):
    """Convert a Spark Row (or dict) to a plain dict."""
    if isinstance(row, dict):
        return row
    if hasattr(row, "asDict"):
        return row.asDict()
    return {}


def collect_column(spark, sql, field):
    """Run a SQL query and return a list of values from a single column."""
    rows = spark.sql(sql).collect()
    return [row_value(r, field) for r in rows]


def ensure_table_exists(spark, fq_table):
    """Verify a Delta table exists via DESCRIBE TABLE; raise if not."""
    try:
        spark.sql(f"DESCRIBE TABLE {fq_table}").collect()
    except Exception as exc:
        raise ArchiveConfigError(
            msg=f"Table '{fq_table}' does not exist or is inaccessible."
        ) from exc


def configure_logging(archive_run_id):
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    class ArchiveRunFilter(logging.Filter):
        def filter(self, record):
            record.table = getattr(record, 'table', '')
            record.archive_run_id = archive_run_id
            return True

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(archive_run_id)s] [%(table)s] %(message)s"
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(ArchiveRunFilter())
    root.addHandler(handler)


_KNOWN_SECRET_KEYS = ("warehouse_id", "client_id", "client_secret")


def load_secrets(settings, dbutils):
    if "secret_scope" not in settings:
        raise ArchiveConfigError(field="secret_scope")
    scope = settings["secret_scope"]
    out = {}
    for key in _KNOWN_SECRET_KEYS:
        try:
            out[key] = dbutils.secrets.get(scope, key)
        except Exception:
            out[key] = None
    return out


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
    return f"DATE '{val.isoformat()}'"


def sql_int(val: int) -> str:
    """Cast to int for SQL."""
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
    cols = ", ".join(columns)
    vals = ", ".join(values)
    return f"INSERT INTO {table} ({cols}) VALUES ({vals})"


def source_fq_from_config(table_config):
    """Build fully-qualified source table name from a config dict."""
    return build_full_table_name(
        table_config["source_catalog"],
        table_config["source_schema"],
        table_config["source_table"],
    )


def archive_path_from_config(table_config, year):
    """Build archive Delta path from a config dict and year."""
    return build_archive_path(
        table_config["archive_base_path"], table_config["source_table"], year
    )


def ensure_table_with_setup_message(spark, fq_table, label="Table"):
    """Verify a table exists; raise with a setup-specific message if not."""
    try:
        ensure_table_exists(spark, fq_table)
    except ArchiveConfigError as exc:
        raise ArchiveConfigError(
            msg=f"{label} {fq_table} does not exist. "
                "Run the setup_config_tables job first."
        ) from exc


def spark_count(spark, sql):
    """Run a SQL that returns a single row with a ``count`` column and return the int result."""
    return int(spark.sql(sql).first()["count"])


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
    cols = ", ".join(columns)
    row_strs = [f"({', '.join(row)})" for row in rows]
    return f"INSERT INTO {table} ({cols}) VALUES {', '.join(row_strs)}"
