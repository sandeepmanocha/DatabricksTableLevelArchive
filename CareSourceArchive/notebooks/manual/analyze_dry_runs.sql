-- Databricks notebook source

-- COMMAND ----------
-- MAGIC %md
-- MAGIC # Dry-Run Analysis
-- MAGIC
-- MAGIC **How to use:** Edit the values in the first cell, then run all cells.
-- MAGIC - Set `v_run_id` to a specific `archive_run_id` to inspect that run.
-- MAGIC - Leave `v_run_id` as `''` to automatically target the most recent dry run.

-- COMMAND ----------
-- DBTITLE 1,Configuration — edit these values
DECLARE OR REPLACE v_catalog = 'sandeep_manocha';
DECLARE OR REPLACE v_schema  = 'caresource_audit';
DECLARE OR REPLACE v_table   = 'archive_audit_log';
DECLARE OR REPLACE v_run_id  = '';  -- leave blank = latest dry run, or paste a specific archive_run_id

-- COMMAND ----------
-- DBTITLE 1,Resolve Run ID
-- If v_run_id is blank, auto-select the most recent dry run.
SET VAR v_run_id = CASE
  WHEN v_run_id = '' THEN (
    SELECT archive_run_id
    FROM   IDENTIFIER(v_catalog || '.' || v_schema || '.' || v_table)
    WHERE  status = 'DRY_RUN'
    ORDER  BY created_at DESC
    LIMIT  1
  )
  ELSE v_run_id
END;

SELECT v_run_id AS active_run_id;

-- COMMAND ----------
-- MAGIC %md
-- MAGIC ## 1 · Per-Table Summary
-- MAGIC Row counts for each table in the selected dry run.

-- COMMAND ----------
-- DBTITLE 1,1 · Per-Table Summary
SELECT
  table_name,
  year,
  CAST(get_json_object(conditions_applied, '$.total_eligible') AS BIGINT)    AS total_eligible,
  CAST(get_json_object(conditions_applied, '$.would_archive')  AS BIGINT)    AS would_archive,
  CAST(get_json_object(conditions_applied, '$.total_eligible') AS BIGINT)
    - CAST(get_json_object(conditions_applied, '$.would_archive') AS BIGINT) AS protected_by_conditions,
  COALESCE(null_date_count, '0')                                             AS null_date_count,
  archive_run_id,
  created_at
FROM   IDENTIFIER(v_catalog || '.' || v_schema || '.' || v_table)
WHERE  status       = 'DRY_RUN'
  AND  archive_run_id = v_run_id
ORDER  BY table_name, year;

-- COMMAND ----------
-- MAGIC %md
-- MAGIC ## 2 · Run-Level Rollup
-- MAGIC Aggregate totals across all dry runs — useful for tracking trends over time.

-- COMMAND ----------
-- DBTITLE 1,2 · Run-Level Rollup (all dry runs)
SELECT
  archive_run_id,
  MIN(created_at)                                                                AS run_started_at,
  COUNT(DISTINCT table_name)                                                     AS tables_scanned,
  SUM(CAST(get_json_object(conditions_applied, '$.total_eligible') AS BIGINT))  AS total_eligible_rows,
  SUM(CAST(get_json_object(conditions_applied, '$.would_archive')  AS BIGINT))  AS total_would_archive,
  SUM(CAST(get_json_object(conditions_applied, '$.total_eligible') AS BIGINT))
    - SUM(CAST(get_json_object(conditions_applied, '$.would_archive') AS BIGINT)) AS total_protected,
  archived_by
FROM   IDENTIFIER(v_catalog || '.' || v_schema || '.' || v_table)
WHERE  status = 'DRY_RUN'
GROUP  BY archive_run_id, archived_by
ORDER  BY run_started_at DESC;

-- COMMAND ----------
-- MAGIC %md
-- MAGIC ## 3 · Drift Detection
-- MAGIC Compares the selected dry run against the previous one per table/year.
-- MAGIC Large deltas may indicate new data loads, schema changes, or config drift.

-- COMMAND ----------
-- DBTITLE 1,3 · Drift Detection (selected vs previous run)
WITH ranked AS (
  SELECT
    archive_run_id,
    table_name,
    year,
    CAST(get_json_object(conditions_applied, '$.would_archive') AS BIGINT) AS would_archive,
    created_at,
    ROW_NUMBER() OVER (PARTITION BY table_name, year ORDER BY created_at DESC) AS rn
  FROM   IDENTIFIER(v_catalog || '.' || v_schema || '.' || v_table)
  WHERE  status = 'DRY_RUN'
)
SELECT
  curr.table_name,
  curr.year,
  curr.would_archive                                                AS current_count,
  prev.would_archive                                                AS previous_count,
  curr.would_archive - COALESCE(prev.would_archive, 0)              AS delta,
  ROUND(
    100.0 * (curr.would_archive - COALESCE(prev.would_archive, 0))
      / NULLIF(prev.would_archive, 0),
    2)                                                              AS delta_pct,
  curr.archive_run_id                                               AS current_run,
  COALESCE(prev.archive_run_id, '(no previous run)')               AS previous_run
FROM       ranked curr
LEFT JOIN  ranked prev
       ON  curr.table_name = prev.table_name
       AND curr.year       = prev.year
       AND prev.rn         = 2
WHERE  curr.archive_run_id = v_run_id
ORDER  BY ABS(curr.would_archive - COALESCE(prev.would_archive, 0)) DESC;

-- COMMAND ----------
-- MAGIC %md
-- MAGIC ## 4 · Data Quality Flags
-- MAGIC Tables with null dates or rows protected by exclusion conditions — investigate before going live.

-- COMMAND ----------
-- DBTITLE 1,4 · Data Quality Flags (nulls or exclusions)
SELECT
  table_name,
  year,
  CAST(get_json_object(conditions_applied, '$.total_eligible') AS BIGINT)    AS total_eligible,
  CAST(get_json_object(conditions_applied, '$.would_archive')  AS BIGINT)    AS would_archive,
  CAST(get_json_object(conditions_applied, '$.total_eligible') AS BIGINT)
    - CAST(get_json_object(conditions_applied, '$.would_archive') AS BIGINT) AS protected_by_conditions,
  COALESCE(null_date_count, '0')                                             AS null_date_count,
  archive_run_id,
  created_at
FROM   IDENTIFIER(v_catalog || '.' || v_schema || '.' || v_table)
WHERE  status = 'DRY_RUN'
  AND  archive_run_id = v_run_id
  AND  (
         CAST(COALESCE(null_date_count, '0') AS INT) > 0
      OR CAST(get_json_object(conditions_applied, '$.total_eligible') AS BIGINT)
         != CAST(get_json_object(conditions_applied, '$.would_archive') AS BIGINT)
  )
ORDER  BY CAST(COALESCE(null_date_count, '0') AS INT) DESC, table_name;

-- COMMAND ----------
-- MAGIC %md
-- MAGIC ## 5 · Per-Condition Exclusion Breakdown
-- MAGIC Explodes `per_condition_counts` to show how many rows each exclusion rule is protecting.
-- MAGIC Empty if no exclusion conditions are configured.

-- COMMAND ----------
-- DBTITLE 1,5 · Per-Condition Exclusion Breakdown
SELECT
  table_name,
  year,
  condition_name,
  rows_protected,
  archive_run_id,
  created_at
FROM   IDENTIFIER(v_catalog || '.' || v_schema || '.' || v_table)
       LATERAL VIEW EXPLODE(
         from_json(
           get_json_object(conditions_applied, '$.per_condition_counts'),
           'MAP<STRING, BIGINT>'
         )
       ) AS condition_name, rows_protected
WHERE  status       = 'DRY_RUN'
  AND  archive_run_id = v_run_id
  AND  rows_protected > 0
ORDER  BY rows_protected DESC, table_name;
