# Catalog Setup

How to bootstrap (or rebuild) the Unity Catalog objects the bundle needs for a given target — catalog, schemas, external volume, grants.

Run as **metastore admin** or **catalog owner**.

For service-principal creation, workspace binding, and the grant template, see [`service-principals.md`](./service-principals.md). This runbook only covers the UC-object layer.

---

## Prerequisites

- An existing **UC storage credential** for the target bucket.
- An **external location** covering the bucket (or at minimum every path the catalog + volumes will write to).

  In the dev sandbox this already exists:

  | Name | URL | Credential |
  |---|---|---|
  | `manocha-ext-role-332745928618-zavxf2-el-48kul7` | `s3://manocha-ext-s3-332745928618-zavxf2/` | `manocha-ext-role-332745928618-zavxf2--48kul7` |

  A bucket-wide external location means you don't need a second external location for the volume path.

---

## Step 1 — Create the catalog

```sql
CREATE CATALOG <catalog_name>
MANAGED LOCATION 's3://<bucket>/<catalog_prefix>'
COMMENT '<description>';
```

Example:

```sql
CREATE CATALOG dev2_archive
MANAGED LOCATION 's3://manocha-ext-s3-332745928618-zavxf2/dev2_archive'
COMMENT 'CareSource archive — dev (managed, external storage)';
```

Verify:

```sql
DESCRIBE CATALOG EXTENDED <catalog_name>;
```

> **Changing location later is not possible while the catalog holds data.** `ALTER CATALOG ... SET MANAGED LOCATION` and `DROP CATALOG` both require the catalog to be empty of user schemas. If you hit `Catalog '<name>' is not empty`, drop and recreate: `DROP CATALOG IF EXISTS <name> CASCADE;`

---

## Step 2 — Create schemas

All managed — they inherit the catalog's managed location.

```sql
CREATE SCHEMA IF NOT EXISTS <catalog>.metadata
  COMMENT 'Config + audit tables (global_settings, table_configs, archive_audit_log, scanner_log, rehydration_audit_log)';

CREATE SCHEMA IF NOT EXISTS <catalog>.source_data_samples
  COMMENT 'Sample source tables consumed by scanner/archive jobs';

CREATE SCHEMA IF NOT EXISTS <catalog>.source_data_samples_archive
  COMMENT 'Archive landing schema — holds the external volume for archived parquet';

CREATE SCHEMA IF NOT EXISTS <catalog>.rehydrated
  COMMENT 'Default rehydration target schema (per-year views)';
```

Verify:

```sql
SHOW SCHEMAS IN <catalog>;
```

---

## Step 3 — Create the external volume

The archive volume must live at a path that is:

1. **Outside the catalog's managed root** — UC rejects external tables/volumes inside a catalog's managed storage.
2. **Covered by an existing external location** — the bucket-wide one is fine.

Convention: sibling prefix named `<catalog>_volumes/<volume_name>/`.

```sql
CREATE EXTERNAL VOLUME IF NOT EXISTS
  <catalog>.source_data_samples_archive.sample_data_archive_ext_vol
LOCATION 's3://<bucket>/<catalog>_volumes/sample_data_archive_ext_vol'
COMMENT 'External volume — archived parquet files for source_data_samples';
```

Verify:

```sql
DESCRIBE VOLUME <catalog>.source_data_samples_archive.sample_data_archive_ext_vol;
SHOW VOLUMES IN <catalog>.source_data_samples_archive;
```

> The folder names (`<catalog>_volumes`, `sample_data_archive_ext_vol`) are **convention, not a UC rule** — UC stores the path as an opaque pointer. Anything not under the managed root works.

---

## Step 4 — Grant permissions to the job service principal

See [`service-principals.md` → Step 3 (UC permissions)](./service-principals.md#step-3--grant-uc-permissions) for the full SQL template, the rationale for `ANY FILE`, and common failure modes. Apply it with the catalog/schema/volume names from this runbook and the SP's **Application ID** UUID.

Dev SP (sandbox): `44edd08d-b71a-4e29-a01b-4881be31a144` (`caresource-archive-dev`).

---

## Step 5 — Smoke test via the bundle

```bash
databricks bundle deploy -t <target> --profile <workspace-profile>
databricks bundle run setup_config_tables -t <target> --profile <workspace-profile>
```

Success criterion: the run's `create_tables` task terminates `SUCCESS` and `SHOW TABLES IN <catalog>.metadata` lists all seven config tables (`global_settings`, `schema_templates`, `table_configs`, `table_configs_staging`, `archive_audit_log`, `scanner_log`, `rehydration_audit_log`).

If deploy fails on `run_as`, or the run fails with a UC `PERMISSION_DENIED`, go to [`service-principals.md` → Step 4 (verify end-to-end)](./service-principals.md#step-4--verify-end-to-end).

---

## Rebuild shortcut (dev only, destroys data)

When the catalog is in a bad state and you just want a clean slate:

```sql
DROP CATALOG IF EXISTS <catalog> CASCADE;
```

Then re-run Steps 1 → 4, then Step 5.
