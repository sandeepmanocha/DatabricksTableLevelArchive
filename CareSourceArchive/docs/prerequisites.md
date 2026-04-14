# CareSource Delta Table Archive — Prerequisites

What must be in place before development and deployment can begin.

---

## Quick-Start Checklist

### Day 1 — unblocks development

- [ ] Dev workspace URL and my user account added
- [ ] Databricks CLI auth working (PAT or OAuth)
- [ ] SQL warehouse available (Pro or Classic — warehouse ID known)
- [ ] Source catalog/schema with sample Delta tables (read access)
- [ ] Audit catalog + schema created (e.g. `dev_archive.audit`) with write access
- [ ] External Volume provisioned for archive storage (e.g. `caresource-dev-archive` backed by `s3://caresource-dev-archive/`) — see [Per-Environment Infrastructure](#per-environment-infrastructure)
- [ ] Storage credential created in Unity Catalog (grants Databricks access to the underlying S3/ABFS bucket)
- [ ] External Location registered in Unity Catalog (maps the storage path)
- [ ] External Volume created in Unity Catalog at the desired catalog/schema (e.g. `CREATE EXTERNAL VOLUME archive_config.volumes.dev_archive LOCATION 's3://caresource-dev-archive/'`)
- [ ] `WRITE VOLUME` + `READ VOLUME` on the External Volume for my user
- [ ] Git repo with push access to a dev branch
- [ ] Python 3.10+, uv >= 0.6, and Databricks CLI >= 0.250.0 locally
- [ ] Dependencies installed from repo root: `uv sync`

### Week 1 — unblocks configuration and full testing

- [ ] Secret scope `archive-dev` with `warehouse_id` populated; my user has `READ` ACL
- [ ] Rehydration target catalog + schema with `CREATE TABLE` / `CREATE VIEW` grants
- [ ] Pilot table list (3-5 tables) with: date columns, retention periods, exclusion rules, approx row counts per year
- [ ] Environment topology decision: single workspace (catalog separation) vs. multiple workspaces

### Before QA deploy

- [ ] Service principal created at account level (e.g. `caresource-archive-qa` or `archive-qa`; see [Service Principals](#service-principals))
- [ ] SP added to QA workspace with **User** role
- [ ] OAuth M2M secret generated → `client_id` and `client_secret` saved
- [ ] UC grants applied to SP (see [Granting UC permissions](#granting-uc-permissions-to-the-sp))
- [ ] QA infrastructure provisioned (audit catalog, archive storage, external location)
- [ ] Secret scope `archive-qa` created with `warehouse_id`, `client_id`, `client_secret` (see [Secret Scopes](#secret-scopes-and-secrets))
- [ ] SP granted `READ` ACL on `archive-qa` scope
- [ ] CI/CD pipeline with SP credentials stored as pipeline secrets
- [ ] Pipeline YAML implementing three-stage deploy (PR validation → dev deploy → promote)

### Before Prod deploy

- [ ] Service principal `archive-prod` created and added to prod workspace
- [ ] OAuth M2M secret generated for prod SP
- [ ] UC grants applied to prod SP (same pattern as QA, prod resource names)
- [ ] Prod infrastructure provisioned (audit catalog, archive storage, external location)
- [ ] Secret scope `archive-prod` created and populated (`warehouse_id`, `client_id`, `client_secret`)
- [ ] SP granted `READ` ACL on `archive-prod` scope
- [ ] Grants verified: `SHOW GRANTS` on all securables
- [ ] Manual approval gate in CI/CD for prod deploys
- [ ] Data criticality assumption (A5) confirmed — drives rollback rigor

---

## Service Principals

A service principal (SP) is a machine identity used for automated jobs and CI/CD. Each non-dev environment (QA, Stage, Prod) gets its own SP so jobs run with a dedicated identity rather than a human's credentials.

### Step 1 — Create the SP

SPs are created at the **account level** by an account admin, then added to workspaces.

**Option A — Databricks Account Console (UI):**

1. Go to **Account Console** → **User management** → **Service principals**
2. Click **Add service principal**
3. Set display name: `archive-{env}` (e.g. `archive-qa`, `archive-prod`)
4. Copy the **Application ID** (UUID) — you'll need it for grants and secrets

**Option B — Databricks CLI (account-level):**

The user you authenticate with must be an **account admin** on that Databricks account. Workspace admin or metastore admin is not enough — if you see *API disabled for users without account admin status*, use **Option A** or ask an account admin to run the CLI (or grant you account admin if your organization allows it).

Account-level commands do **not** use your normal workspace profile. The profile must point at the **Account Console** host and include your **Databricks account ID** (numeric UUID from **Account Console** → **Settings**).

Add a dedicated stanza to `~/.databrickscfg` (name it e.g. `account-admin`):

```ini
[account-admin]
host       = https://accounts.cloud.databricks.com
account_id = <your-account-uuid>
```

Authenticate that profile (OAuth is typical):

```bash
databricks auth login --profile account-admin
```

Create the SP:

```bash
databricks account service-principals create \
  --display-name "caresource-archive-qa" \
  --profile account-admin
```

Use `-o json` if you want machine-readable output. The response includes `application_id` and `id` — save both.

**Common CLI errors**

| Message | Cause | What to do |
|--------|--------|------------|
| `invalid Databricks Account configuration - host incorrect or account_id missing` | Profile `host` is a **workspace** URL (e.g. `https://adb-….azuredatabricks.net` or `*.cloud.databricks.com` workspace) or `account_id` is missing | Use a profile whose `host` is `https://accounts.cloud.databricks.com` and set `account_id` |
| `A new access token could not be retrieved… refresh token is invalid` | Stale or expired auth on the account profile | Run `databricks auth login --profile account-admin` (or re-create a PAT if you use token auth) |
| `This API is disabled for users without account admin status` | Authenticated user is not an **account admin** for that `account_id` (common on shared/demo accounts) | Use **Option A** in the Account Console, or have an account admin run the same CLI command; confirm identity with `databricks auth describe --profile <account-profile>` |

### Step 2 — Add SP to the workspace

**Account Console:**

1. **Workspaces** → select your workspace → **Permissions** tab
2. **Add permissions** → search for the SP by name → assign **User** role
3. Repeat for each workspace the SP needs access to

**CLI (workspace-level):**

```bash
# Adds the SP to the workspace (uses the application_id from Step 1)
databricks service-principals create \
  --application-id "<application-id-uuid>" \
  --display-name "archive-qa" \
  --active \
  --profile qa-workspace
```

`--active` is a boolean flag: use `--active` alone. Passing `--active true` sends `true` as a positional argument and fails with `accepts 0 arg(s), received 1`.

### Step 3 — Generate OAuth M2M credentials

Each SP needs a `client_id` + `client_secret` pair for authentication. The `client_id` is the SP's **Application ID** from Step 1. The `client_secret` is an OAuth secret you generate.

**Account Console:**

1. **User management** → **Service principals** → select the SP
2. **OAuth secrets** tab → **Generate secret**
3. Copy the secret value immediately — it is shown only once
4. Lifetime: up to 730 days (2 years). Set a rotation reminder.

**CLI (account-level):**

```bash
databricks account service-principal-secrets create <service-principal-id> \
  --profile account-admin
```

The response includes `secret` (the `client_secret` value) — save it securely.

**What you now have per SP:**

| Value | Where it comes from | Where it goes |
|---|---|---|
| `client_id` | SP's Application ID (UUID) | Secret scope key `client_id` |
| `client_secret` | Generated OAuth secret | Secret scope key `client_secret` |

---

## Required Permissions

Grants needed per environment for both interactive users (dev) and service principals (higher envs).

| Permission | Target | Purpose |
|---|---|---|
| `USE CATALOG`, `USE SCHEMA`, `SELECT` | Source catalogs/schemas/tables | Read source data |
| `MODIFY` | Source tables | Delete archived records |
| `USE CATALOG`, `USE SCHEMA`, `CREATE TABLE` | Audit catalog/schema | Write audit logs |
| `WRITE VOLUME`, `READ VOLUME` | Archive External Volume | Write and read year folders via the `/Volumes/` path. Grant at the Volume level — not at the External Location level |
| `CREATE TABLE`, `CREATE VIEW` | Rehydration target catalog | Create external tables and unified views |
| `READ` (secret scope ACL) | `archive-{env}` scope | Access warehouse ID and credentials at runtime |

### Granting UC permissions to the SP

Run these as a **metastore admin** or **catalog owner**, replacing `<sp-name>` with the SP display name (e.g. `archive-qa`) and substituting environment-specific catalog/path names.

```sql
-- 1. Source data access (read + delete archived rows)
GRANT USE CATALOG ON CATALOG healthcare       TO `<sp-name>`;
GRANT USE SCHEMA  ON SCHEMA  healthcare.claims TO `<sp-name>`;
GRANT SELECT      ON SCHEMA  healthcare.claims TO `<sp-name>`;
GRANT MODIFY      ON SCHEMA  healthcare.claims TO `<sp-name>`;

-- Repeat for each source schema the archive job touches

-- 2. Audit catalog (write audit logs + config tables)
GRANT USE CATALOG   ON CATALOG qa_archive       TO `<sp-name>`;
GRANT USE SCHEMA    ON SCHEMA  qa_archive.audit  TO `<sp-name>`;
GRANT CREATE TABLE  ON SCHEMA  qa_archive.audit  TO `<sp-name>`;
GRANT SELECT        ON SCHEMA  qa_archive.audit  TO `<sp-name>`;
GRANT MODIFY        ON SCHEMA  qa_archive.audit  TO `<sp-name>`;

-- 3. Archive External Volume (write year folders, read for rehydration)
-- The External Volume wraps the underlying storage — use VOLUME grants, not EXTERNAL LOCATION grants
GRANT WRITE VOLUME ON VOLUME archive_config.volumes.qa_archive TO `<sp-name>`;
GRANT READ VOLUME  ON VOLUME archive_config.volumes.qa_archive TO `<sp-name>`;

-- 4. Rehydration target (create external tables + unified views)
GRANT USE CATALOG   ON CATALOG qa_archive           TO `<sp-name>`;
GRANT USE SCHEMA    ON SCHEMA  qa_archive.rehydrated TO `<sp-name>`;
GRANT CREATE TABLE  ON SCHEMA  qa_archive.rehydrated TO `<sp-name>`;
GRANT CREATE VIEW   ON SCHEMA  qa_archive.rehydrated TO `<sp-name>`;
```

**CLI equivalent** (one grant at a time):

```bash
databricks grants update catalog healthcare \
  --json '{"changes": [{"principal": "archive-qa", "add": ["USE_CATALOG"]}]}' \
  --profile qa-workspace
```

### Verifying grants

```sql
SHOW GRANTS ON CATALOG healthcare;
SHOW GRANTS `<sp-name>` ON SCHEMA healthcare.claims;
```

Or via CLI:

```bash
databricks grants get catalog healthcare --profile qa-workspace
```

---

## Secret Scopes and Secrets

The application loads credentials at runtime via `dbutils.secrets.get()` from a scope named in the global settings table (`secret_scope` column). Convention: `archive-{env}`.

### What the code expects

The `load_secrets()` function in `src/utils.py` reads these keys from the scope:

| Key | Required? | Purpose |
|---|---|---|
| `warehouse_id` | Yes (all envs) | SQL warehouse ID for `spark.sql()` execution |
| `client_id` | No (dev) / Yes (QA+) | SP Application ID for OAuth M2M |
| `client_secret` | No (dev) / Yes (QA+) | SP OAuth secret for M2M auth |

Missing keys return `None` — dev environments work without `client_id`/`client_secret` because the developer's interactive credentials are used instead.

### Step 1 — Create the secret scope

One scope per environment per workspace.

```bash
# Dev
databricks secrets create-scope archive-dev --profile dev-workspace

# QA
databricks secrets create-scope archive-qa --profile qa-workspace

# Prod
databricks secrets create-scope archive-prod --profile prod-workspace
```

### Step 2 — Populate secrets

```bash
# Warehouse ID (all environments)
databricks secrets put-secret archive-qa warehouse_id \
  --string-value "<sql-warehouse-id>" \
  --profile qa-workspace

# SP credentials (QA and higher — not needed for dev)
databricks secrets put-secret archive-qa client_id \
  --string-value "<sp-application-id>" \
  --profile qa-workspace

databricks secrets put-secret archive-qa client_secret \
  --string-value "<sp-oauth-secret>" \
  --profile qa-workspace
```

### Step 3 — Set ACLs

Grant the SP `READ` on its own scope so the job can access secrets at runtime. Grant workspace admins `MANAGE` for rotation.

```bash
# SP gets READ
databricks secrets put-acl archive-qa "archive-qa" READ \
  --profile qa-workspace

# Admins get MANAGE (for secret rotation)
databricks secrets put-acl archive-qa admins MANAGE \
  --profile qa-workspace
```

For dev, grant the developer (or a group) `READ`:

```bash
databricks secrets put-acl archive-dev "sandeep.manocha@caresource.com" READ \
  --profile dev-workspace
```

### Step 4 — Verify

```bash
# List scopes
databricks secrets list-scopes --profile qa-workspace

# List keys in scope (values are never shown)
databricks secrets list-secrets archive-qa --profile qa-workspace

# List ACLs
databricks secrets list-acls archive-qa --profile qa-workspace
```

### Matching the global settings table

The `secret_scope` column in the global settings table must match exactly:

| Environment | `secret_scope` value | Keys populated |
|---|---|---|
| Dev | `archive-dev` | `warehouse_id` |
| QA | `archive-qa` | `warehouse_id`, `client_id`, `client_secret` |
| Stage | `archive-stage` | `warehouse_id`, `client_id`, `client_secret` |
| Prod | `archive-prod` | `warehouse_id`, `client_id`, `client_secret` |

---

## QA Environment Runbook

Copy-paste commands for standing up the `archive-qa` service principal end-to-end.

### Placeholders — fill in before you start

| Placeholder | Where it comes from | Example |
|---|---|---|
| `<account-admin-profile>` | `~/.databrickscfg` profile whose `host` is `https://accounts.cloud.databricks.com` | `caresource-account` |
| `<qa-workspace-profile>` | `~/.databrickscfg` profile for the QA workspace | `caresource-qa` |

### 1. Create the service principal (account level)

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
| `application_id` | `<sp-application-id>` (UUID — also used as `client_id`) |
| `id` | `<sp-id>` (use exact value from JSON — needed for optional secret generation later) |

### 2. Add the SP to the QA workspace

```bash
# PROFILE: workspace-level
databricks service-principals create \
  --application-id "<sp-application-id>" \
  --display-name "archive-qa" \
  --active \
  --profile <qa-workspace-profile>
```

### 3. Grant UC permissions

Run the notebook `notebooks/manual/grant_sp_permissions.py` in the QA workspace, or execute the SQL below manually as a **metastore admin** or **catalog owner**. Adjust catalog, schema, and external location names to match your QA environment.

**Notebook parameters for QA:**

| Parameter | Value |
|---|---|
| `sp_name` | `archive-qa` |
| `source_catalog` | `healthcare` |
| `source_schemas` | `claims` |
| `audit_catalog` | `qa_archive` |
| `audit_schema` | `audit` |
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

-- Audit catalog (write audit logs + config tables)
GRANT USE CATALOG   ON CATALOG qa_archive       TO `archive-qa`;
GRANT USE SCHEMA    ON SCHEMA  qa_archive.audit  TO `archive-qa`;
GRANT CREATE TABLE  ON SCHEMA  qa_archive.audit  TO `archive-qa`;
GRANT SELECT        ON SCHEMA  qa_archive.audit  TO `archive-qa`;
GRANT MODIFY        ON SCHEMA  qa_archive.audit  TO `archive-qa`;

-- Archive External Volume (write year folders, read for rehydration)
GRANT WRITE VOLUME ON VOLUME archive_config.volumes.qa_archive TO `archive-qa`;
GRANT READ VOLUME  ON VOLUME archive_config.volumes.qa_archive TO `archive-qa`;

-- Rehydration target (create external tables + unified views)
GRANT USE CATALOG   ON CATALOG qa_archive           TO `archive-qa`;
GRANT USE SCHEMA    ON SCHEMA  qa_archive.rehydrated TO `archive-qa`;
GRANT CREATE TABLE  ON SCHEMA  qa_archive.rehydrated TO `archive-qa`;
GRANT CREATE VIEW   ON SCHEMA  qa_archive.rehydrated TO `archive-qa`;
```

CLI alternative (one grant at a time):

```bash
# PROFILE: workspace-level
databricks grants update catalog healthcare \
  --json '{"changes": [{"principal": "archive-qa", "add": ["USE_CATALOG"]}]}' \
  --profile <qa-workspace-profile>
```

### 4. Verify SP and grants

```sql
-- Run in QA workspace SQL editor or notebook
SHOW GRANTS `archive-qa` ON CATALOG healthcare;
SHOW GRANTS `archive-qa` ON SCHEMA  healthcare.claims;
SHOW GRANTS `archive-qa` ON CATALOG qa_archive;
SHOW GRANTS `archive-qa` ON SCHEMA  qa_archive.audit;
SHOW GRANTS `archive-qa` ON SCHEMA  qa_archive.rehydrated;
SHOW GRANTS `archive-qa` ON VOLUME archive_config.volumes.qa_archive;
```

### Optional — OAuth M2M credentials and secret scopes

These steps are needed when the QA jobs run under SP identity via OAuth M2M authentication (e.g. from CI/CD pipelines). Skip if the SP will authenticate via workspace-level token or if jobs run interactively for now.

#### O1. Generate OAuth M2M secret (account level)

```bash
# PROFILE: account-level
databricks account service-principal-secrets create <sp-id> \
  --profile <account-admin-profile>
```

Save the `secret` field from the response as `<sp-oauth-secret>`. It is shown only once.

| Value | Source | Placeholder |
|---|---|---|
| `client_id` | `application_id` from Step 1 | `<sp-application-id>` |
| `client_secret` | `secret` from this step | `<sp-oauth-secret>` |

**Rotation reminder:** secrets expire after up to 730 days. Set a calendar reminder.

#### O2. Create the secret scope

```bash
# PROFILE: workspace-level
databricks secrets create-scope archive-qa \
  --profile <qa-workspace-profile>
```

#### O3. Populate secrets

Also requires `<sql-warehouse-id>` — the QA SQL warehouse ID from the workspace SQL Warehouses page.

```bash
# PROFILE: workspace-level

# Warehouse ID
databricks secrets put-secret archive-qa warehouse_id \
  --string-value "<sql-warehouse-id>" \
  --profile <qa-workspace-profile>

# SP client_id (= application_id from Step 1)
databricks secrets put-secret archive-qa client_id \
  --string-value "<sp-application-id>" \
  --profile <qa-workspace-profile>

# SP client_secret (= OAuth secret from O1)
databricks secrets put-secret archive-qa client_secret \
  --string-value "<sp-oauth-secret>" \
  --profile <qa-workspace-profile>
```

#### O4. Set scope ACLs

```bash
# PROFILE: workspace-level

# SP gets READ so the job can access its own credentials
databricks secrets put-acl archive-qa "archive-qa" READ \
  --profile <qa-workspace-profile>

# Admins get MANAGE for secret rotation
databricks secrets put-acl archive-qa admins MANAGE \
  --profile <qa-workspace-profile>
```

#### O5. Verify secrets and ACLs

```bash
# PROFILE: workspace-level

# Secret scope exists
databricks secrets list-scopes --profile <qa-workspace-profile>

# Keys are populated (values are never shown)
databricks secrets list-secrets archive-qa \
  --profile <qa-workspace-profile>

# ACLs are correct
databricks secrets list-acls archive-qa \
  --profile <qa-workspace-profile>
```

---

## Per-Environment Infrastructure

Repeat for each of **Dev**, **QA**, **Stage**, **Prod**:

| Resource | Example (prod) |
|---|---|
| Service principal | `archive-prod` with OAuth M2M credentials |
| Audit catalog + schema | `prod_archive.audit` |
| Archive cloud storage bucket | `s3://caresource-prod-archive/` (provisioned by Cloud Admin) |
| Storage credential in UC | Grants Databricks access to the underlying S3/ABFS bucket |
| External location in UC | Maps the storage path for Unity Catalog governance |
| **External Volume in UC** | `CREATE EXTERNAL VOLUME archive_config.volumes.prod_archive LOCATION 's3://caresource-prod-archive/'` — this is the path used in all `archive_base_path` fields |
| SQL warehouse (Pro or Classic) | Warehouse ID goes into the secret scope |
| Secret scope `archive-{env}` | Contains `warehouse_id`, `client_id`, `client_secret` |

---

## Source Table Information Needed

| Item | When |
|---|---|
| Candidate tables for archiving (start with 3-5) | Day 1 |
| **Watermark column** per table (e.g. `claim_date`, `inserted_at`, `service_date`) — must be **monotonically increasing** (never back-filled or updated). This is a hard constraint: if the column can decrease or be updated, the table cannot be safely archived incrementally | Day 1 |
| Retention policy per table (3 / 5 / 7 years) | Week 1 |
| Exclusion rules (e.g. "don't archive if `status_flag = 'Active'`") | Week 1 |
| NULL date column prevalence (date column is treated as required — NULLs are logged as errors) | Week 2 |
| Approximate row counts per year | Week 1 |

---

## Who Needs to Do What

| Admin Role | Tasks |
|---|---|
| **Cloud Admin** (AWS/Azure) | Create archive storage buckets per env; configure IAM roles for Databricks access |
| **UC Metastore Admin** | Create catalogs, schemas, storage credentials, external locations, **External Volumes**; grant UC permissions (including `WRITE VOLUME` / `READ VOLUME`) |
| **Workspace Admin** | Add users/SPs to workspaces; provision SQL warehouses; create + populate secret scopes with ACLs |
| **Identity / Security Admin** | Create service principals; generate OAuth M2M credentials; add SPs to workspaces |
| **CI/CD Admin** | Set up pipeline; store SP credentials as secrets; configure triggers and approval gates |
| **Data / Business Stakeholders** | Confirm pilot tables, retention periods, exclusion rules, data criticality; decide environment topology |
| **Me (developer)** | Application code, config, DABs bundles, documentation |

---

## Change Log

| Date | Change |
|---|---|
| 2026-04-08 | Updated storage model: External Volumes replace direct External Locations as archive paths; added monotonic watermark column requirement; updated permissions to WRITE VOLUME / READ VOLUME |
| 2026-04-03 | Added QA Environment Runbook; fixed all CLI commands to match v0.295 positional arg syntax; added CREATE VIEW grant for rehydration |
| 2026-03-27 | Simplified and restructured for readability |
| 2026-03-26 | Initial prerequisites document created |
