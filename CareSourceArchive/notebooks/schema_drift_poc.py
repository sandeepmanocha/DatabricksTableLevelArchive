# Databricks notebook source
# MAGIC %md
# MAGIC # Schema-drift POC — `--force-merge-schema-for` + INT → BIGINT
# MAGIC
# MAGIC **Run cells one at a time, top to bottom.** Each case is its own cell so you
# MAGIC can capture the output and inspect the archive between steps.
# MAGIC
# MAGIC Cases share state on disk — case N assumes case N-1 ran. Re-run the **Reset
# MAGIC path** cell to start over.
# MAGIC
# MAGIC Mirrors how `table_based_archive/src/archiver.py` writes:
# MAGIC `CREATE TABLE delta.`{path}` ... AS SELECT ...` then `INSERT INTO delta.`{path}` ...`.
# MAGIC
# MAGIC | # | Drift | mergeSchema=false | mergeSchema=true |
# MAGIC |---|---|---|---|
# MAGIC | 1 | Baseline `id INT, name STRING, event_ts TIMESTAMP` (CREATE) | OK | n/a |
# MAGIC | 2 | `id INT → BIGINT` | FAIL | OK (needs `delta.enableTypeWidening` + DBR 15.4 LTS+) |
# MAGIC | 3 | Add column `region STRING` | FAIL | OK |
# MAGIC | 4 | Drop column `name` | n/a | OK (target keeps col, new rows NULL) |
# MAGIC | 5 | Narrowing `id BIGINT → INT` | FAIL | FAIL (Delta refuses) |
# MAGIC | 6 | Incompatible `event_ts TIMESTAMP → STRING` | FAIL | FAIL (Delta refuses) |

# COMMAND ----------
# MAGIC %md
# MAGIC ## Setup — widgets, path, helpers
# MAGIC Run once per session. Re-run if you change widgets.

# COMMAND ----------
dbutils.widgets.dropdown("target_mode", "volume", ["volume", "external"], "Target")
dbutils.widgets.text(
    "volume_path",
    "/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol",
    "Volume base path",
)
dbutils.widgets.text(
    "external_path",
    "s3://one-env-uc-external-location/sm-field-demo/caresource_archive_folder",
    "External bucket base path (e.g. s3://bucket/prefix). Required when target=external.",
)
dbutils.widgets.text(
    "external_volume",
    "sandeep_manocha.caresource_archive.caresource_archive_external_vol",
    "External volume UC name (catalog.schema.volume). Used by 'Create external volume' cell.",
)
dbutils.widgets.text("test_table", "schema_drift_poc", "Test table folder name")
dbutils.widgets.text(
    "rehydrate_view",
    "sandeep_manocha.caresource_rehyderate.schema_drift_poc_view",
    "Rehydrate view (catalog.schema.view)",
)

TARGET_MODE = dbutils.widgets.get("target_mode").strip().lower()
VOLUME_BASE = dbutils.widgets.get("volume_path").strip().rstrip("/")
EXTERNAL_BASE = dbutils.widgets.get("external_path").strip().rstrip("/")
EXTERNAL_VOLUME = dbutils.widgets.get("external_volume").strip()
TEST_TABLE = dbutils.widgets.get("test_table").strip()
REHYDRATE_VIEW = dbutils.widgets.get("rehydrate_view").strip()

if TARGET_MODE == "volume":
    BASE = VOLUME_BASE
elif TARGET_MODE == "external":
    BASE = EXTERNAL_BASE
else:
    raise ValueError("target_mode must be 'volume' or 'external'")

if not BASE:
    raise ValueError(f"{TARGET_MODE}_path is required (non-empty)")
if not TEST_TABLE:
    raise ValueError("test_table is required (non-empty)")

# COMMAND ----------
from datetime import datetime
from pyspark.sql import Row
from pyspark.sql.types import (
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

YEAR = datetime.now().year
PATH = f"{BASE}/{TEST_TABLE}/year_{YEAR}"

_view_parts = REHYDRATE_VIEW.split(".")
if len(_view_parts) != 3 or not all(p.strip() for p in _view_parts):
    raise ValueError(f"rehydrate_view must be 'catalog.schema.view' (got: {REHYDRATE_VIEW!r})")
VIEW_CATALOG, VIEW_SCHEMA, VIEW_NAME = _view_parts
VIEW = f"`{VIEW_CATALOG}`.`{VIEW_SCHEMA}`.`{VIEW_NAME}`"

print(f"target = {TARGET_MODE}")
print(f"path   = {PATH}")
print(f"view   = {REHYDRATE_VIEW}")

# COMMAND ----------
def write_create(df, path: str) -> None:
    """CREATE TABLE delta.`{path}` ... — same shape as ArchiveEngine._create_year.
    Sets type-widening + column-mapping at first write so later cases can exercise them.
    """
    df.createOrReplaceTempView("_poc_src")
    spark.sql(
        f"CREATE TABLE delta.`{path}` "
        "USING DELTA "
        "TBLPROPERTIES ("
        "  'delta.enableTypeWidening' = 'true', "
        "  'delta.minReaderVersion' = '3', "
        "  'delta.minWriterVersion' = '7', "
        "  'delta.columnMapping.mode' = 'name'"
        ") "
        "AS SELECT * FROM _poc_src"
    )


def write_append(df, path: str, *, merge_schema: bool) -> None:
    """Append into the year-partitioned Delta with mergeSchema toggled per `--force-merge-schema-for`.
    Uses DataFrame writer instead of SQL INSERT because serverless / shared compute
    blocks session-level `spark.databricks.delta.schema.autoMerge.enabled`. The
    write-time `.option("mergeSchema", ...)` is the supported equivalent.
    """
    (
        df.write.format("delta")
        .mode("append")
        .option("mergeSchema", "true" if merge_schema else "false")
        .save(path)
    )


def run_case(label: str, expected: str, fn) -> None:
    """Print header / expected / actual OK or FAIL. Inspection happens inline below the call."""
    print(f"=== {label}")
    print(f"  expected: {expected}")
    try:
        fn()
        print("  actual  : OK")
    except Exception as exc:
        print(f"  actual  : FAIL — {type(exc).__name__}: {str(exc).splitlines()[0][:300]}")


# COMMAND ----------
# MAGIC %md
# MAGIC ## Create external volume (run when `target_mode=external`)
# MAGIC Registers the S3 prefix in `external_path` as a Unity Catalog external volume
# MAGIC named by the `external_volume` widget. Idempotent — uses `IF NOT EXISTS`.
# MAGIC Skip this cell when `target_mode=volume`.

# COMMAND ----------
_vol_parts = EXTERNAL_VOLUME.split(".")
if len(_vol_parts) != 3 or not all(p.strip() for p in _vol_parts):
    raise ValueError(
        f"external_volume must be 'catalog.schema.volume' (got: {EXTERNAL_VOLUME!r})"
    )
VOL_CATALOG, VOL_SCHEMA, VOL_NAME = _vol_parts

if not EXTERNAL_BASE:
    raise ValueError("external_path is required to create an external volume")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VOL_CATALOG}`.`{VOL_SCHEMA}`")
spark.sql(
    f"CREATE EXTERNAL VOLUME IF NOT EXISTS "
    f"`{VOL_CATALOG}`.`{VOL_SCHEMA}`.`{VOL_NAME}` "
    f"LOCATION '{EXTERNAL_BASE}'"
)
print(f"external volume ready: {EXTERNAL_VOLUME} -> {EXTERNAL_BASE}")
display(spark.sql(f"DESCRIBE VOLUME `{VOL_CATALOG}`.`{VOL_SCHEMA}`.`{VOL_NAME}`"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Reset path
# MAGIC Run this to delete the archive folder and start case 1 from a clean slate.

# COMMAND ----------
print(f"removing {PATH}")
try:
    dbutils.fs.rm(PATH, True)
    print("  done")
except Exception as exc:
    print(f"  skipped — {exc}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 1 — CREATE baseline
# MAGIC `id INT, name STRING, event_ts TIMESTAMP`. Expected: **OK**.

# COMMAND ----------
def _case_1():
    schema = StructType([
        StructField("id", IntegerType(), True),
        StructField("name", StringType(), True),
        StructField("event_ts", TimestampType(), True),
    ])
    df = spark.createDataFrame(
        [
            Row(id=1, name="alice", event_ts=datetime(YEAR, 1, 15, 9, 0, 0)),
            Row(id=2, name="bob", event_ts=datetime(YEAR, 2, 20, 10, 30, 0)),
        ],
        schema=schema,
    )
    write_create(df, PATH)


run_case("1. CREATE baseline (id INT, name STRING, event_ts TIMESTAMP)", "OK", _case_1)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 2a — INT → BIGINT, mergeSchema=false
# MAGIC Expected: **FAIL**. Default Delta write rejects the type change.

# COMMAND ----------
def _widened_df():
    schema = StructType([
        StructField("id", LongType(), True),
        StructField("name", StringType(), True),
        StructField("event_ts", TimestampType(), True),
    ])
    return spark.createDataFrame(
        [Row(id=3_000_000_000, name="carol", event_ts=datetime(YEAR, 3, 10, 11, 0, 0))],
        schema=schema,
    )


run_case(
    "2a. APPEND id BIGINT (mergeSchema=false)",
    "FAIL",
    lambda: write_append(_widened_df(), PATH, merge_schema=False),
)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 2b — INT → BIGINT, mergeSchema=true
# MAGIC Expected: **OK** when `delta.enableTypeWidening=true` and DBR 15.4 LTS+.
# MAGIC On older DBR this lands as **FAIL** — that's the runtime gate.

# COMMAND ----------
print("--- schema BEFORE ---")
display(spark.sql(f"DESCRIBE delta.`{PATH}`"))

run_case(
    "2b. APPEND id BIGINT (mergeSchema=true)",
    "OK on DBR 15.4 LTS+, otherwise FAIL",
    lambda: write_append(_widened_df(), PATH, merge_schema=True),
)

print("--- schema AFTER ---")
display(spark.sql(f"DESCRIBE delta.`{PATH}`"))

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 3a — Add column `region`, mergeSchema=false
# MAGIC Expected: **FAIL**.

# COMMAND ----------
def _added_col_df():
    schema = StructType([
        StructField("id", LongType(), True),
        StructField("name", StringType(), True),
        StructField("event_ts", TimestampType(), True),
        StructField("region", StringType(), True),
    ])
    return spark.createDataFrame(
        [Row(id=4, name="dave", event_ts=datetime(YEAR, 4, 5, 12, 0, 0), region="US-OH")],
        schema=schema,
    )


run_case(
    "3a. APPEND + new col `region` (mergeSchema=false)",
    "FAIL",
    lambda: write_append(_added_col_df(), PATH, merge_schema=False),
)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 3b — Add column `region`, mergeSchema=true
# MAGIC Expected: **OK**. Old rows backfill `region` as NULL.

# COMMAND ----------
print("--- schema BEFORE ---")
display(spark.sql(f"DESCRIBE delta.`{PATH}`"))

run_case(
    "3b. APPEND + new col `region` (mergeSchema=true)",
    "OK",
    lambda: write_append(_added_col_df(), PATH, merge_schema=True),
)

print("--- schema AFTER ---")
display(spark.sql(f"DESCRIBE delta.`{PATH}`"))

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 4 — Source drops column `name`, mergeSchema=true
# MAGIC Expected: **OK**. Target keeps `name`; new rows get NULL for `name`.

# COMMAND ----------
def _dropped_col_df():
    schema = StructType([
        StructField("id", LongType(), True),
        StructField("event_ts", TimestampType(), True),
        StructField("region", StringType(), True),
    ])
    return spark.createDataFrame(
        [Row(id=5, event_ts=datetime(YEAR, 5, 1, 13, 0, 0), region="US-IN")],
        schema=schema,
    )


print("--- schema BEFORE ---")
display(spark.sql(f"DESCRIBE delta.`{PATH}`"))

run_case(
    "4. APPEND drop col `name` (mergeSchema=true)",
    "OK",
    lambda: write_append(_dropped_col_df(), PATH, merge_schema=True),
)

print("--- schema AFTER ---")
display(spark.sql(f"DESCRIBE delta.`{PATH}`"))

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 5a — Narrowing BIGINT → INT, mergeSchema=false
# MAGIC Expected: **FAIL**.

# COMMAND ----------
def _narrowed_df():
    schema = StructType([
        StructField("id", IntegerType(), True),
        StructField("event_ts", TimestampType(), True),
        StructField("region", StringType(), True),
    ])
    return spark.createDataFrame(
        [Row(id=6, event_ts=datetime(YEAR, 6, 1, 14, 0, 0), region="US-KY")],
        schema=schema,
    )


run_case(
    "5a. APPEND id INT narrowing (mergeSchema=false)",
    "FAIL",
    lambda: write_append(_narrowed_df(), PATH, merge_schema=False),
)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 5b — Narrowing BIGINT → INT, mergeSchema=true
# MAGIC Expected: **FAIL** — Delta refuses narrowing even with merge enabled.

# COMMAND ----------
run_case(
    "5b. APPEND id INT narrowing (mergeSchema=true)",
    "FAIL (Delta refuses narrowing)",
    lambda: write_append(_narrowed_df(), PATH, merge_schema=True),
)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 6a — Incompatible TIMESTAMP → STRING, mergeSchema=false
# MAGIC Expected: **FAIL**.

# COMMAND ----------
def _incompatible_df():
    schema = StructType([
        StructField("id", LongType(), True),
        StructField("event_ts", StringType(), True),
        StructField("region", StringType(), True),
    ])
    return spark.createDataFrame(
        [Row(id=7, event_ts="2026-07-01T15:00:00", region="US-WV")],
        schema=schema,
    )


run_case(
    "6a. APPEND event_ts STRING incompatible (mergeSchema=false)",
    "FAIL",
    lambda: write_append(_incompatible_df(), PATH, merge_schema=False),
)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{VIEW_CATALOG}`.`{VIEW_SCHEMA}`")
spark.sql(f"CREATE OR REPLACE VIEW {VIEW} AS SELECT * FROM delta.`{PATH}`")
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Case 6b — Incompatible TIMESTAMP → STRING, mergeSchema=true
# MAGIC Expected: **FAIL** — Delta refuses incompatible type changes even with merge enabled.

# COMMAND ----------
run_case(
    "6b. APPEND event_ts STRING incompatible (mergeSchema=true)",
    "FAIL (Delta refuses incompatible type)",
    lambda: write_append(_incompatible_df(), PATH, merge_schema=True),
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Inspect archive (run anytime)
# MAGIC Inline SELECT / DESCRIBE statements. Re-run after any case to see current state.

# COMMAND ----------
display(spark.sql(f"SELECT * FROM delta.`{PATH}` ORDER BY id"))

# COMMAND ----------
display(spark.sql(f"SELECT * FROM {VIEW} ORDER BY id"))

# COMMAND ----------
display(spark.sql(f"DESCRIBE delta.`{PATH}`"))

# COMMAND ----------
display(spark.sql(f"DESCRIBE TABLE EXTENDED {VIEW}"))

# COMMAND ----------
display(spark.sql(f"DESCRIBE DETAIL delta.`{PATH}`"))

# COMMAND ----------
display(spark.sql(f"DESCRIBE HISTORY delta.`{PATH}`"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Cleanup (manual)
# MAGIC Run this when you're done to drop the rehydrate view + archive folder.

# COMMAND ----------
print(f"dropping view {REHYDRATE_VIEW}")
try:
    spark.sql(f"DROP VIEW IF EXISTS {REHYDRATE_VIEW}")
    print("  done")
except Exception as exc:
    print(f"  skipped — {exc}")

print(f"\nremoving {PATH}")
try:
    dbutils.fs.rm(PATH, True)
    print("  done")
except Exception as exc:
    print(f"  skipped — {exc}")
