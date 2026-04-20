# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC # Generate Test Data — CareSource Archive
# MAGIC
# MAGIC Creates 3 healthcare source tables for end-to-end archive testing:
# MAGIC
# MAGIC | Table | Watermark Column | Rows | Years |
# MAGIC |-------|-----------------|------|-------|
# MAGIC | `claims` | `event_date` | ~5,000 | 2018-2025 |
# MAGIC | `members` | `start_date` | ~3,000 | 2019-2025 |
# MAGIC | `providers` | `effective_date` | ~1,000 | 2020-2025 |
# MAGIC
# MAGIC **Idempotent** — safe to re-run. Uses `CREATE TABLE IF NOT EXISTS` + `INSERT OVERWRITE`.
# MAGIC
# MAGIC **Fixed seed** (42) — produces identical data every run.
# MAGIC
# MAGIC See: `docs/superpowers/specs/2026-04-07-test-data-generation-design.md`

# COMMAND ----------
# MAGIC %pip install faker
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
import random
from datetime import date, timedelta
from decimal import Decimal

from faker import Faker
from pyspark.sql import Row
from pyspark.sql.types import (
    BooleanType,
    DateType,
    DecimalType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

SEED = 42
fake = Faker()
Faker.seed(SEED)
random.seed(SEED)

# COMMAND ----------
dbutils.widgets.text("catalog", "dev2_archive", "Catalog")
dbutils.widgets.text("schema", "source_data_samples", "Schema")

CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()

if not CATALOG or not SCHEMA:
    raise ValueError("Both catalog and schema are required.")

print(f"Target: {CATALOG}.{SCHEMA}")

# COMMAND ----------
# Verify catalog exists; create schema if needed
_cat_rows = spark.sql(
    f"SELECT 1 FROM system.information_schema.catalogs "
    f"WHERE catalog_name = '{CATALOG}'"
).collect()
if not _cat_rows:
    raise ValueError(
        f"Catalog '{CATALOG}' does not exist. Create it before running this notebook."
    )

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")
print(f"✓ {CATALOG}.{SCHEMA} exists")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Claims (~5,000 rows)

# COMMAND ----------
CLAIMS_TABLE = f"`{CATALOG}`.`{SCHEMA}`.`claims`"
CLAIMS_COUNT = 5000
CLAIMS_NULL_DATES = 15
CLAIMS_YEARS = list(range(2018, 2026))  # 2018-2025

CLAIM_TYPES = ["Medical", "Dental", "Pharmacy", "Vision"]
CLAIM_TYPE_WEIGHTS = [0.55, 0.15, 0.20, 0.10]

DIAGNOSIS_CODES = [
    "J06.9", "E11.9", "I10", "M54.5", "K21.0",
    "J45.909", "E78.5", "G43.909", "F41.1", "N39.0",
    "Z23", "R10.9", "M79.3", "J02.9", "L30.9",
]

STATUS_FLAGS = ["Closed", "Active", "Pending"]
STATUS_WEIGHTS = [0.80, 0.15, 0.05]

claims_schema = StructType([
    StructField("claim_id", StringType(), False),
    StructField("member_id", StringType(), False),
    StructField("provider_id", StringType(), False),
    StructField("claim_type", StringType(), False),
    StructField("diagnosis_code", StringType(), False),
    StructField("amount", DecimalType(10, 2), False),
    StructField("status_flag", StringType(), False),
    StructField("event_date", DateType(), True),
    StructField("load_timestamp", TimestampType(), True),
])

def _random_date_in_year(yr):
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))

claims_rows = []
rows_per_year = CLAIMS_COUNT // len(CLAIMS_YEARS)

for i in range(CLAIMS_COUNT):
    yr = CLAIMS_YEARS[i % len(CLAIMS_YEARS)]
    if i >= len(CLAIMS_YEARS) * rows_per_year:
        yr = random.choice(CLAIMS_YEARS)

    event_dt = _random_date_in_year(yr)
    load_offset = timedelta(days=random.randint(1, 30))
    from datetime import datetime
    load_ts = datetime.combine(event_dt + load_offset, datetime.min.time().replace(
        hour=random.randint(6, 22),
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
    ))

    claims_rows.append(Row(
        claim_id=f"CLM-{i+1:06d}",
        member_id=f"MBR-{random.randint(1, 3000):05d}",
        provider_id=f"PRV-{random.randint(1, 1000):04d}",
        claim_type=random.choices(CLAIM_TYPES, CLAIM_TYPE_WEIGHTS)[0],
        diagnosis_code=random.choice(DIAGNOSIS_CODES),
        amount=Decimal(f"{random.uniform(10, 50000):.2f}"),
        status_flag=random.choices(STATUS_FLAGS, STATUS_WEIGHTS)[0],
        event_date=event_dt,
        load_timestamp=load_ts,
    ))

null_indices = random.sample(range(CLAIMS_COUNT), CLAIMS_NULL_DATES)
for idx in null_indices:
    row = claims_rows[idx]
    claims_rows[idx] = Row(
        claim_id=row.claim_id,
        member_id=row.member_id,
        provider_id=row.provider_id,
        claim_type=row.claim_type,
        diagnosis_code=row.diagnosis_code,
        amount=row.amount,
        status_flag=row.status_flag,
        event_date=None,
        load_timestamp=row.load_timestamp,
    )

spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {CLAIMS_TABLE} (
        claim_id        STRING      NOT NULL,
        member_id       STRING      NOT NULL,
        provider_id     STRING      NOT NULL,
        claim_type      STRING      NOT NULL,
        diagnosis_code  STRING      NOT NULL,
        amount          DECIMAL(10,2) NOT NULL,
        status_flag     STRING      NOT NULL,
        event_date      DATE,
        load_timestamp  TIMESTAMP
    ) USING DELTA
""")

claims_df = spark.createDataFrame(claims_rows, schema=claims_schema)
claims_df.write.mode("overwrite").insertInto(CLAIMS_TABLE, overwrite=True)

print(f"✓ {CLAIMS_TABLE}: {claims_df.count()} rows written")
display(
    spark.sql(
        f"SELECT YEAR(event_date) AS year, COUNT(*) AS cnt "
        f"FROM {CLAIMS_TABLE} GROUP BY 1 ORDER BY 1"
    )
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Members (~3,000 rows)

# COMMAND ----------
MEMBERS_TABLE = f"`{CATALOG}`.`{SCHEMA}`.`members`"
MEMBERS_COUNT = 3000
MEMBERS_NULL_DATES = 10
MEMBERS_YEARS = list(range(2019, 2026))  # 2019-2025

PLAN_TYPES = ["HMO", "PPO", "EPO", "POS"]
ENROLLMENT_STATUSES = ["Active", "Inactive", "Terminated"]
ENROLLMENT_WEIGHTS = [0.60, 0.25, 0.15]

members_schema = StructType([
    StructField("member_id", StringType(), False),
    StructField("first_name", StringType(), False),
    StructField("last_name", StringType(), False),
    StructField("date_of_birth", DateType(), False),
    StructField("plan_type", StringType(), False),
    StructField("enrollment_status", StringType(), False),
    StructField("start_date", DateType(), True),
])

members_rows = []
rows_per_year = MEMBERS_COUNT // len(MEMBERS_YEARS)

for i in range(MEMBERS_COUNT):
    yr = MEMBERS_YEARS[i % len(MEMBERS_YEARS)]
    if i >= len(MEMBERS_YEARS) * rows_per_year:
        yr = random.choice(MEMBERS_YEARS)

    dob_start = date(1940, 1, 1)
    dob_end = date(2010, 12, 31)
    dob = dob_start + timedelta(days=random.randint(0, (dob_end - dob_start).days))

    members_rows.append(Row(
        member_id=f"MBR-{i+1:05d}",
        first_name=fake.first_name(),
        last_name=fake.last_name(),
        date_of_birth=dob,
        plan_type=random.choice(PLAN_TYPES),
        enrollment_status=random.choices(ENROLLMENT_STATUSES, ENROLLMENT_WEIGHTS)[0],
        start_date=_random_date_in_year(yr),
    ))

null_indices = random.sample(range(MEMBERS_COUNT), MEMBERS_NULL_DATES)
for idx in null_indices:
    row = members_rows[idx]
    members_rows[idx] = Row(
        member_id=row.member_id,
        first_name=row.first_name,
        last_name=row.last_name,
        date_of_birth=row.date_of_birth,
        plan_type=row.plan_type,
        enrollment_status=row.enrollment_status,
        start_date=None,
    )

spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {MEMBERS_TABLE} (
        member_id           STRING  NOT NULL,
        first_name          STRING  NOT NULL,
        last_name           STRING  NOT NULL,
        date_of_birth       DATE    NOT NULL,
        plan_type           STRING  NOT NULL,
        enrollment_status   STRING  NOT NULL,
        start_date          DATE
    ) USING DELTA
""")

members_df = spark.createDataFrame(members_rows, schema=members_schema)
members_df.write.mode("overwrite").insertInto(MEMBERS_TABLE, overwrite=True)

print(f"✓ {MEMBERS_TABLE}: {members_df.count()} rows written")
display(
    spark.sql(
        f"SELECT YEAR(start_date) AS year, COUNT(*) AS cnt "
        f"FROM {MEMBERS_TABLE} GROUP BY 1 ORDER BY 1"
    )
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Providers (~1,000 rows)

# COMMAND ----------
PROVIDERS_TABLE = f"`{CATALOG}`.`{SCHEMA}`.`providers`"
PROVIDERS_COUNT = 1000
PROVIDERS_NULL_DATES = 5
PROVIDERS_YEARS = list(range(2020, 2026))  # 2020-2025

SPECIALTIES = [
    "Cardiology", "Oncology", "Pediatrics", "Orthopedics",
    "Neurology", "Dermatology", "Psychiatry", "Endocrinology",
    "Gastroenterology", "Pulmonology", "Rheumatology", "Nephrology",
]

US_STATES = [
    "OH", "KY", "IN", "WV", "GA", "TX", "FL", "CA",
    "NY", "PA", "IL", "MI", "NC", "VA", "TN", "SC",
]

providers_schema = StructType([
    StructField("provider_id", StringType(), False),
    StructField("provider_name", StringType(), False),
    StructField("specialty", StringType(), False),
    StructField("npi", StringType(), False),
    StructField("effective_date", DateType(), True),
    StructField("state", StringType(), False),
    StructField("is_active", BooleanType(), False),
])

providers_rows = []
rows_per_year = PROVIDERS_COUNT // len(PROVIDERS_YEARS)

for i in range(PROVIDERS_COUNT):
    yr = PROVIDERS_YEARS[i % len(PROVIDERS_YEARS)]
    if i >= len(PROVIDERS_YEARS) * rows_per_year:
        yr = random.choice(PROVIDERS_YEARS)

    providers_rows.append(Row(
        provider_id=f"PRV-{i+1:04d}",
        provider_name=f"Dr. {fake.last_name()}",
        specialty=random.choice(SPECIALTIES),
        npi=f"{random.randint(1000000000, 9999999999)}",
        effective_date=_random_date_in_year(yr),
        state=random.choice(US_STATES),
        is_active=random.random() < 0.85,
    ))

null_indices = random.sample(range(PROVIDERS_COUNT), PROVIDERS_NULL_DATES)
for idx in null_indices:
    row = providers_rows[idx]
    providers_rows[idx] = Row(
        provider_id=row.provider_id,
        provider_name=row.provider_name,
        specialty=row.specialty,
        npi=row.npi,
        effective_date=None,
        state=row.state,
        is_active=row.is_active,
    )

spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {PROVIDERS_TABLE} (
        provider_id     STRING  NOT NULL,
        provider_name   STRING  NOT NULL,
        specialty       STRING  NOT NULL,
        npi             STRING  NOT NULL,
        effective_date  DATE,
        state           STRING  NOT NULL,
        is_active       BOOLEAN NOT NULL
    ) USING DELTA
""")

providers_df = spark.createDataFrame(providers_rows, schema=providers_schema)
providers_df.write.mode("overwrite").insertInto(PROVIDERS_TABLE, overwrite=True)

print(f"✓ {PROVIDERS_TABLE}: {providers_df.count()} rows written")
display(
    spark.sql(
        f"SELECT YEAR(effective_date) AS year, COUNT(*) AS cnt "
        f"FROM {PROVIDERS_TABLE} GROUP BY 1 ORDER BY 1"
    )
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------
for tbl, wm_col in [
    (CLAIMS_TABLE, "event_date"),
    (MEMBERS_TABLE, "start_date"),
    (PROVIDERS_TABLE, "effective_date"),
]:
    total = spark.sql(f"SELECT COUNT(*) AS c FROM {tbl}").collect()[0]["c"]
    nulls = spark.sql(
        f"SELECT COUNT(*) AS c FROM {tbl} WHERE {wm_col} IS NULL"
    ).collect()[0]["c"]
    print(f"{tbl}: {total} rows, {nulls} NULL {wm_col}")

print("\n--- Year distribution (all tables) ---")
display(spark.sql(f"""
    SELECT 'claims' AS table_name, YEAR(event_date) AS year, COUNT(*) AS cnt
    FROM {CLAIMS_TABLE} GROUP BY 1, 2
    UNION ALL
    SELECT 'members', YEAR(start_date), COUNT(*)
    FROM {MEMBERS_TABLE} GROUP BY 1, 2
    UNION ALL
    SELECT 'providers', YEAR(effective_date), COUNT(*)
    FROM {PROVIDERS_TABLE} GROUP BY 1, 2
    ORDER BY table_name, year
"""))

print("\n--- Status distributions ---")
print("Claims status_flag:")
display(spark.sql(f"SELECT status_flag, COUNT(*) AS cnt FROM {CLAIMS_TABLE} GROUP BY 1 ORDER BY 1"))
print("Members enrollment_status:")
display(spark.sql(f"SELECT enrollment_status, COUNT(*) AS cnt FROM {MEMBERS_TABLE} GROUP BY 1 ORDER BY 1"))
print("Providers is_active:")
display(spark.sql(f"SELECT is_active, COUNT(*) AS cnt FROM {PROVIDERS_TABLE} GROUP BY 1 ORDER BY 1"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## What To Do Next
# MAGIC
# MAGIC ### Step 0: Prerequisites
# MAGIC
# MAGIC 1. Deploy the bundle: `databricks bundle deploy -t dev`
# MAGIC 2. Run the setup job if not already done (creates config Delta tables):
# MAGIC    ```
# MAGIC    databricks bundle run setup_config_tables -t dev
# MAGIC    ```
# MAGIC 3. Seed config if not already done: run `seed_config` notebook
# MAGIC 4. Ensure `table_configs` has entries for **claims**, **members**, and **providers** with:
# MAGIC    - `retention_years = 3` (makes years ≤ 2023 eligible for archiving)
# MAGIC    - Correct `watermark_column`: `event_date`, `start_date`, `effective_date`
# MAGIC    - `is_active = true`
# MAGIC 5. Ensure the archive volume path exists and is covered by a UC external location
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 1: Happy Path — Dry Run
# MAGIC
# MAGIC ```
# MAGIC databricks bundle run caresource_archive_run -t dev \
# MAGIC   --params config_table=sandeep_manocha.caresource_audit.global_settings,dry_run=true
# MAGIC ```
# MAGIC
# MAGIC **Check:** Job succeeds. Each table shows eligible years, would-archive counts, NULL date counts.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 2: Happy Path — Live Archive
# MAGIC
# MAGIC ```
# MAGIC databricks bundle run caresource_archive_run -t dev \
# MAGIC   --params config_table=sandeep_manocha.caresource_audit.global_settings,dry_run=false
# MAGIC ```
# MAGIC
# MAGIC **Check:** All 3 tables archive. Audit has STARTED → ARCHIVED per table+year. Archive folders created. Watermarks recorded.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 3: Happy Path — Re-run (Redundant)
# MAGIC
# MAGIC Run Step 2 again.
# MAGIC
# MAGIC **Check:** All years SKIP — no new data above watermark. Audit shows SKIPPED with `archive_mode = SKIP`. Fast.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 4: New Data Arrives (UC-5 — APPEND)
# MAGIC
# MAGIC Insert a few new claims for an already-archived year (e.g., 2022) with `event_date` **after** the current watermark.
# MAGIC Then re-run the archive job (Step 2 command).
# MAGIC
# MAGIC **Check:** Year 2022 gets `archive_mode = APPEND`. New watermark recorded. Other years SKIP.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 5: Multi-Year Failure (UC-1)
# MAGIC
# MAGIC Simulate a failure for one table mid-run (e.g., revoke archive path access for `providers`, or drop the table).
# MAGIC Re-run.
# MAGIC
# MAGIC **Check:** Failed table has STARTED → FAILED in audit with error message. Later years never attempted. Other tables succeed.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 6: Orphan Folder Detection (UC-2)
# MAGIC
# MAGIC Manually create an empty folder at an archive year path that has **no** ARCHIVED audit entry.
# MAGIC Re-run the archive job.
# MAGIC
# MAGIC **Check:** `ArchiveOperationError` with "orphan folder" message and resolution steps.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 7: Fix Orphan Folder (UC-2 Resolution)
# MAGIC
# MAGIC 1. Inspect: `SELECT COUNT(*) FROM delta.\`<path>\``
# MAGIC 2. If corrupt: delete the folder from cloud storage
# MAGIC 3. Re-run the archive job
# MAGIC
# MAGIC **Check:** Year re-archives with CREATE mode.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 8: Re-run After Failure (UC-3)
# MAGIC
# MAGIC Fix the issue from Step 5, then re-run.
# MAGIC
# MAGIC **Check:** INFO log "Previous run FAILED... Retrying." Already-archived years SKIP. Audit: FAILED → STARTED → ARCHIVED.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 9: Parallel ForEach — One Table Fails (UC-4)
# MAGIC
# MAGIC Cause one table (e.g., `providers`) to fail while others succeed.
# MAGIC
# MAGIC **Check:** Job UI shows mixed results. Re-run with filter: `--params table_config_filter="source_table = 'providers'"`.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 10: Stale STARTED Detection (UC-6)
# MAGIC
# MAGIC Insert a fake STARTED audit row with `created_at` = 24 hours ago and a different `archive_run_id`.
# MAGIC Re-run.
# MAGIC
# MAGIC **Check:** WARNING log about stale STARTED. Proceeds to normal flow. No orphan folder → CREATE. Orphan folder → ERROR (UC-2).
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ### Step 11: Rehydration
# MAGIC
# MAGIC ```
# MAGIC databricks bundle run caresource_rehydrate -t dev \
# MAGIC   --params config_table=sandeep_manocha.caresource_audit.global_settings \
# MAGIC   --params source_table=claims \
# MAGIC   --params years=2020 \
# MAGIC   --params target_catalog=sandeep_manocha \
# MAGIC   --params target_schema=caresource_rehydrated
# MAGIC ```
# MAGIC
# MAGIC **Check:** External table created. Unified view combines current + restored data. Rehydration audit logged.
