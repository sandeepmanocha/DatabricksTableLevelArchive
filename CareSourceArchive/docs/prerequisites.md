# CareSource Delta Table Archive — Prerequisites

What must be in place before development and deployment can begin. Detailed procedures are in the [runbooks/](runbooks/) folder.

---

## Quick-Start Checklist

### Day 1 — unblocks development

- [ ] Dev workspace URL and my user account added
- [ ] Databricks CLI auth working (PAT or OAuth)
- [ ] SQL warehouse available (Pro or Serverless — warehouse ID known)
- [ ] Source catalog/schema with sample Delta tables (read access)
- [ ] Config catalog + schema created (e.g. `dev_archive_operations.config`) with write access — this is where all 7 tables live (4 config + 3 audit/log)
- [ ] External Volume provisioned for archive storage (e.g. `caresource-dev-archive` backed by `s3://caresource-dev-archive/`) — see [Per-Environment Infrastructure](#per-environment-infrastructure)
- [ ] Storage credential created in Unity Catalog (grants Databricks access to the underlying S3/ABFS bucket)
- [ ] External Location registered in Unity Catalog (maps the storage path)
- [ ] External Volume created in Unity Catalog at the desired catalog/schema (e.g. `CREATE EXTERNAL VOLUME archive_config.volumes.dev_archive LOCATION 's3://caresource-dev-archive/'`)
- [ ] `WRITE VOLUME` + `READ VOLUME` on the External Volume for my user
- [ ] Git repo with push access to a dev branch
- [ ] Python 3.10+, uv >= 0.6, and Databricks CLI >= 0.250.0 locally
- [ ] Dependencies installed from repo root: `uv sync`

### Week 1 — unblocks configuration and full testing

- [ ] Rehydration target catalog + schema with `CREATE TABLE` / `CREATE VIEW` grants
- [ ] Pilot table list (3-5 tables) with: watermark columns, retention periods, exclusion rules, approx row counts per year
- [ ] Environment topology decision: single workspace (catalog separation) vs. multiple workspaces

### Before QA deploy

- [ ] Service principal created at account level (e.g. `archive-qa`; see [Service Principals](runbooks/service-principals.md))
- [ ] SP added to QA workspace with **User** role
- [ ] UC grants applied to SP (see Step 3 in [Service Principals](runbooks/service-principals.md))
- [ ] QA infrastructure provisioned (config catalog, archive storage, external location)
- [ ] CI/CD pipeline configured
- [ ] Pipeline YAML implementing three-stage deploy (PR validation → dev deploy → promote)
- [ ] Full QA walkthrough: [QA Environment Runbook](runbooks/qa-environment.md)

### Before Prod deploy

- [ ] Service principal `archive-prod` created and added to prod workspace
- [ ] UC grants applied to prod SP (same pattern as QA, prod resource names)
- [ ] Prod infrastructure provisioned (config catalog, archive storage, external location)
- [ ] Grants verified: `SHOW GRANTS` on all securables
- [ ] Manual approval gate in CI/CD for prod deploys
- [ ] Data criticality assumption (A5) confirmed — drives rollback rigor

---

## Per-Environment Infrastructure

Repeat for each of **Dev**, **QA**, **Stage**, **Prod**:

| Resource | Example (prod) |
|---|---|
| Service principal | `archive-prod` (authenticates via DABs `run_as`) |
| Config catalog + schema | `prod_archive_operations.config` (holds 7 tables: global_settings, schema_templates, table_configs, table_configs_staging, scanner_log, archive_audit_log, rehydration_audit_log) |
| Archive cloud storage bucket | `s3://caresource-prod-archive/` (provisioned by Cloud Admin) |
| Storage credential in UC | Grants Databricks access to the underlying S3/ABFS bucket |
| External location in UC | Maps the storage path for Unity Catalog governance |
| **External Volume in UC** | `CREATE EXTERNAL VOLUME archive_config.volumes.prod_archive LOCATION 's3://caresource-prod-archive/'` — this is the path used in all `archive_base_path` fields |
| SQL warehouse (Pro or Serverless) | Used by jobs for SQL execution |

---

## Source Table Information Needed

| Item | When |
|---|---|
| Candidate tables for archiving (start with 3-5) | Day 1 |
| **Watermark column** per table (e.g. `claim_date`, `inserted_at`, `service_date`) — must be **monotonically increasing** (never back-filled or updated). This is a hard constraint: if the column can decrease or be updated, the table cannot be safely archived incrementally | Day 1 |
| Retention policy per table (3 / 5 / 7 years) | Week 1 |
| Exclusion rules (e.g. "don't archive if `status_flag = 'Active'`") | Week 1 |
| NULL watermark column prevalence (watermark column is treated as required — NULLs are logged as errors) | Week 2 |
| Approximate row counts per year | Week 1 |

---

## Who Needs to Do What

| Admin Role | Tasks |
|---|---|
| **Cloud Admin** (AWS/Azure) | Create archive storage buckets per env; configure IAM roles for Databricks access |
| **UC Metastore Admin** | Create catalogs, schemas, storage credentials, external locations, **External Volumes**; grant UC permissions (including `WRITE VOLUME` / `READ VOLUME`) |
| **Workspace Admin** | Add users/SPs to workspaces; provision SQL warehouses |
| **Identity / Security Admin** | Create service principals; add SPs to workspaces |
| **CI/CD Admin** | Set up pipeline; store SP credentials as secrets; configure triggers and approval gates |
| **Data / Business Stakeholders** | Confirm pilot tables, retention periods, exclusion rules, data criticality; decide environment topology |
| **Me (developer)** | Application code, config, DABs bundles, documentation |

---

## Runbook Index

| Runbook | Purpose |
|---|---|
| [Service Principals](runbooks/service-principals.md) | Create SPs, add to workspaces |
| ~~UC Permissions~~ | Merged into [Service Principals](runbooks/service-principals.md) Step 3 |
| [QA Environment](runbooks/qa-environment.md) | End-to-end copy-paste walkthrough for QA |

---

## Change Log

| Date | Change |
|---|---|
| 2026-04-12 | Split into overview + 4 runbooks; fixed config catalog naming (was "audit catalog"); fixed "NULL date column" → "NULL watermark column"; added Runbook Index |
| 2026-04-08 | Updated storage model: External Volumes replace direct External Locations as archive paths; added monotonic watermark column requirement; updated permissions to WRITE VOLUME / READ VOLUME |
| 2026-04-03 | Added QA Environment Runbook; fixed all CLI commands to match v0.295 positional arg syntax; added CREATE VIEW grant for rehydration |
| 2026-03-27 | Simplified and restructured for readability |
| 2026-03-26 | Initial prerequisites document created |
