# Databricks notebook source

# COMMAND ----------
# Parameters (defaults match sandeep_manocha dev layout)
dbutils.widgets.text("config_catalog", "sandeep_manocha", "Config Catalog")
dbutils.widgets.text("config_schema", "caresource_audit", "Config Schema")
dbutils.widgets.text("source_schema", "source_data_samples", "Source Schema (UC schema to scan)")

CONFIG_CATALOG = dbutils.widgets.get("config_catalog").strip()
CONFIG_SCHEMA = dbutils.widgets.get("config_schema").strip()
SOURCE_SCHEMA = dbutils.widgets.get("source_schema").strip()

if not CONFIG_CATALOG or not CONFIG_SCHEMA or not SOURCE_SCHEMA:
    raise ValueError("config_catalog, config_schema, and source_schema are required (non-empty).")

# PK for schema_templates — human-readable unique id (matches DDL schema_id)
SCHEMA_TEMPLATE_ID = f"{CONFIG_CATALOG}__{SOURCE_SCHEMA}"


def _fq(table: str) -> str:
    return f"`{CONFIG_CATALOG}`.`{CONFIG_SCHEMA}`.`{table}`"


# COMMAND ----------
# Seed global_settings + schema_templates (DDL matches setup_config_tables.py).
# Archive paths use /Volumes/{config_catalog}/caresource_archive/caresource_archive_vol/...
# ── 1. Ensure the config schema exists ───────────────────────────────────────
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CONFIG_CATALOG}`.`{CONFIG_SCHEMA}`")

# COMMAND ----------
# ── 2. Seed global_settings (same column order as DDL_GLOBAL_SETTINGS) ──────
gs_count = spark.sql(f"SELECT COUNT(1) AS c FROM {_fq('global_settings')}").collect()[0]["c"]

if gs_count == 0:
    spark.sql(f"""
        INSERT INTO {_fq('global_settings')} (
          audit_catalog,
          audit_schema,
          default_retention_years,
          dry_run_default,
          timezone,
          schema_templates_table,
          table_configs_table,
          archive_base_path_prefix,
          modified_by,
          modified_at,
          change_reason
        ) VALUES (
          '{CONFIG_CATALOG}',
          '{CONFIG_SCHEMA}',
          0,
          true,
          'America/New_York',
          '{CONFIG_CATALOG}.{CONFIG_SCHEMA}.schema_templates',
          '{CONFIG_CATALOG}.{CONFIG_SCHEMA}.table_configs',
          '/Volumes/{CONFIG_CATALOG}/source_data_samples_archive/sample_data_archive_ext_vol',
          current_user(),
          current_timestamp(),
          'Testing: set retention to 0 to make all data eligible'
        )
    """)
    print("✓ global_settings seeded")
else:
    print(f"⊘ global_settings already has {gs_count} row(s) — skipped")

# COMMAND ----------
# ── 3. Seed schema_templates (same column order as DDL_SCHEMA_TEMPLATES) ─────
st_count = spark.sql(
    f"SELECT COUNT(1) AS c FROM {_fq('schema_templates')} "
    f"WHERE schema_id = '{SCHEMA_TEMPLATE_ID}'"
).collect()[0]["c"]

if st_count == 0:
    spark.sql(f"""
        INSERT INTO {_fq('schema_templates')} (
          schema_id,
          source_catalog,
          source_schema,
          watermark_column_patterns,
          default_retention_years,
          archive_base_path,
          delete_after_archive,
          min_table_size_gb,
          exclude_tables,
          description,
          is_active,
          modified_by,
          modified_at,
          change_reason
        ) VALUES (
          '{SCHEMA_TEMPLATE_ID}',
          '{CONFIG_CATALOG}',
          '{SOURCE_SCHEMA}',
          ARRAY('event_date', 'start_time', 'query_date'),
          0,
          '/Volumes/{CONFIG_CATALOG}/source_data_samples_archive/sample_data_archive_ext_vol/{SOURCE_SCHEMA}',
          false,
          CAST(0.0 AS DOUBLE),
          CAST(NULL AS ARRAY<STRING>),
          'Dev sample data for scanner testing',
          true,
          current_user(),
          current_timestamp(),
          'Dev seed for scanner testing'
        )
    """)
    print(f"✓ schema_templates seeded (schema_id={SCHEMA_TEMPLATE_ID})")
else:
    print(f"⊘ schema_templates already has schema_id={SCHEMA_TEMPLATE_ID} — skipped")

# COMMAND ----------
# ── 4. Verify ───────────────────────────────────────────────────────────────
display(spark.sql(f"SELECT * FROM {_fq('global_settings')}"))

# COMMAND ----------
display(spark.sql(f"SELECT * FROM {_fq('schema_templates')}"))
