# QA Environment Runbook

Copy-paste commands for standing up the QA environment end-to-end. Combines steps from [Service Principals](service-principals.md) and [Secret Scopes](secret-scopes.md) into a single linear walkthrough. UC permissions are covered in Service Principals Step 3.

---

## Placeholders — fill in before you start

| Placeholder | Where it comes from | Example |
|---|---|---|
| `<account-admin-profile>` | `~/.databrickscfg` profile whose `host` is `https://accounts.cloud.databricks.com` | `caresource-account` |
| `<qa-workspace-profile>` | `~/.databrickscfg` profile for the QA workspace | `caresource-qa` |

---

## 1. Create the service principal (account level)

Requires **account admin** privileges. This creates the SP in the Databricks account, not yet in any workspace.

```bash
# PROFILE: account-level
databricks account service-principals create \
  --display-name "archive-qa" \
  --profile <account-admin-profile>
```

Save from the JSON response:

| Response field | Save as |
|---|---|
| `application_id` | `<sp-application-id>` (UUID — needed for workspace add and grants) |

## 2. Add the SP to the QA workspace

```bash
# PROFILE: workspace-level
databricks service-principals create \
  --application-id "<sp-application-id>" \
  --display-name "archive-qa" \
  --active \
  --profile <qa-workspace-profile>
```

## 3. Grant UC permissions

Run the notebook `notebooks/manual/grant_sp_permissions.py` in the QA workspace, or execute the SQL below manually as a **metastore admin** or **catalog owner**. Adjust catalog, schema, and external location names to match your QA environment.

**Notebook parameters for QA:**

| Parameter | Value |
|---|---|
| `sp_name` | `archive-qa` |
| `source_catalog` | `healthcare` |
| `source_schemas` | `claims` |
| `config_catalog` | `qa_archive_operations` |
| `config_schema` | `config` |
| `rehydration_schema` | `rehydrated` |
| `external_location` | `caresource-qa-archive` |
| `dry_run` | `true` (preview first, then `false` to apply) |

**Equivalent SQL (for reference):**

```sql
-- Source data access (read + delete archived rows)
GRANT USE CATALOG ON CATALOG healthcare       TO `archive-qa`;
GRANT USE SCHEMA  ON SCHEMA  healthcare.claims TO `archive-qa`;
GRANT SELECT      ON SCHEMA  healthcare.claims TO `archive-qa`;
GRANT MODIFY      ON SCHEMA  healthcare.claims TO `archive-qa`;
-- Repeat for each additional source schema

-- Config catalog (config tables + audit logs + scanner log — 7 tables)
GRANT USE CATALOG   ON CATALOG qa_archive_operations       TO `archive-qa`;
GRANT USE SCHEMA    ON SCHEMA  qa_archive_operations.config TO `archive-qa`;
GRANT CREATE TABLE  ON SCHEMA  qa_archive_operations.config TO `archive-qa`;
GRANT SELECT        ON SCHEMA  qa_archive_operations.config TO `archive-qa`;
GRANT MODIFY        ON SCHEMA  qa_archive_operations.config TO `archive-qa`;

-- Archive External Volume (write year folders, read for rehydration)
GRANT WRITE VOLUME ON VOLUME archive_config.volumes.qa_archive TO `archive-qa`;
GRANT READ VOLUME  ON VOLUME archive_config.volumes.qa_archive TO `archive-qa`;

-- Rehydration target (create external tables + unified views)
GRANT USE CATALOG   ON CATALOG qa_archive_operations            TO `archive-qa`;
GRANT USE SCHEMA    ON SCHEMA  qa_archive_operations.rehydrated TO `archive-qa`;
GRANT CREATE TABLE  ON SCHEMA  qa_archive_operations.rehydrated TO `archive-qa`;
GRANT CREATE VIEW   ON SCHEMA  qa_archive_operations.rehydrated TO `archive-qa`;
```

CLI alternative (one grant at a time):

```bash
# PROFILE: workspace-level
databricks grants update catalog healthcare \
  --json '{"changes": [{"principal": "archive-qa", "add": ["USE_CATALOG"]}]}' \
  --profile <qa-workspace-profile>
```

## 4. Verify SP and grants

```sql
-- Run in QA workspace SQL editor or notebook
SHOW GRANTS `archive-qa` ON CATALOG healthcare;
SHOW GRANTS `archive-qa` ON SCHEMA  healthcare.claims;
SHOW GRANTS `archive-qa` ON CATALOG qa_archive_operations;
SHOW GRANTS `archive-qa` ON SCHEMA  qa_archive_operations.config;
SHOW GRANTS `archive-qa` ON SCHEMA  qa_archive_operations.rehydrated;
SHOW GRANTS `archive-qa` ON VOLUME archive_config.volumes.qa_archive;
```

