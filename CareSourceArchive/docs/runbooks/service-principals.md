# Service Principals

Each environment (Dev, QA, Stage, Prod) gets its own service principal so jobs run with a dedicated machine identity via DABs `run_as`.

| Environment | SP display name | Status (dev sandbox, 2026-04) |
|---|---|---|
| Dev | `caresource-archive-dev` | Provisioned — app ID `44edd08d-b71a-4e29-a01b-4881be31a144` |
| QA | `caresource-archive-qa` | Aspirational — not yet provisioned |
| Stage | `caresource-archive-stage` | Aspirational — not yet provisioned |
| Prod | `caresource-archive-prod` | Aspirational — not yet provisioned |

Until qa/stage/prod SPs exist, `databricks.yml` intentionally points `run_as` at the dev SP for all four targets. Replace per-target once each env is provisioned.

OAuth M2M credentials (`client_id`/`client_secret`) are **not needed** for `run_as`. They **are** needed for CI/CD (Azure DevOps) to authenticate as a deploying SP — see Step 2d.

### Identifiers you'll need

- **Account ID** — from the account console URL: `https://accounts.cloud.databricks.com/?account_id=<account-id>`
- **Workspace ID** — from any workspace URL: `https://<host>/?o=<workspace-id>`
- **SP application ID** — UUID, used in `run_as`, UC grants, workspace permissions
- **SP SCIM id** — numeric, used in account-level permission APIs (rule sets)

---

## Step 1 — Create the SP (account level)

### UI

1. **Account Console** → **User management** → **Service principals**
2. **Add service principal** → set display name (e.g. `caresource-archive-dev`)
3. Copy the **Application ID** (UUID) — needed for Step 2

### Retrieving the Application ID later

If you already created the SP and didn't copy the ID:

- **UI:** Account Console → User management → Service principals → click the SP name → Application ID is on the detail page
- **CLI:** `databricks account service-principals list --profile account-admin -o json` — find your SP by `display_name` and grab `application_id`

### CLI alternative

Requires **account admin** role. Create a profile with one command (no need to hand-edit `~/.databrickscfg`):

```bash
databricks auth login \
  --host https://accounts.cloud.databricks.com \
  --account-id <your-account-id> \
  --profile account-admin
```

Confirm it's valid:

```bash
databricks auth profiles -o json \
  | python3 -c "import sys,json;print([p for p in json.load(sys.stdin)['profiles'] if p['name']=='account-admin'])"
```

Then create the SP:

```bash
databricks account service-principals create \
  --display-name "caresource-archive-qa" \
  --profile account-admin
```

The list/get response returns two identifiers — don't confuse them:

- `applicationId` — UUID — use this for `run_as`, UC grants, workspace permissions
- `id` — numeric — use this for account-level permission/role APIs (rule sets)

<details>
<summary>Common CLI errors</summary>

| Message | Fix |
|---|---|
| `host incorrect or account_id missing` | Profile must use `https://accounts.cloud.databricks.com` with `account_id` set |
| `refresh token is invalid` | Re-run `databricks auth login --profile account-admin` |
| `API disabled for users without account admin status` | Use the UI instead, or have an account admin run the command |

</details>

---

## Step 2 — Add SP to the workspace

This binds an account-level SP to a specific workspace. It does **not** create a new SP.

### UI (recommended)

1. Open the workspace → top-right gear → **Settings**
2. **Identity and access** (left nav) → **Service principals** → **Manage**
3. **Add service principal** → **Add existing**
4. Paste the SP **Application ID** (UUID) → confirm → role **User** is sufficient

> Note: the Account Console also has a "Workspaces → Permissions" view, but that flow grants **workspace-level roles** (e.g. workspace admin) to an already-bound principal. It is not the primary path to bind an unbound SP to a workspace.

### CLI

```bash
databricks service-principals create \
  --application-id "<application-id-uuid>" \
  --display-name "caresource-archive-dev" \
  --active \
  --profile <workspace-profile>
```

Despite the name, this acts as "bind the existing account SP to this workspace" when the application ID already exists at the account.

> `--active` is a boolean flag — use it alone. `--active true` fails with `accepts 0 arg(s), received 1`.

> If the SP is already bound you'll get a 409 conflict. Safe to ignore — verify via the list command below.

### Verify

**UI:** Workspace → **Admin Settings** → **Identity and access** → **Service principals** — confirm the SP appears and shows **Active**.

**CLI:**

```bash
databricks service-principals list --profile <workspace-profile> -o json \
  | python3 -c "
import sys, json
for sp in json.load(sys.stdin):
    if 'caresource' in sp.get('displayName', '').lower():
        print(f\"{sp['displayName']:40s} {sp['applicationId']}\")
"
```

You should see your SP name and application ID in the output.

---

## Step 2b — Grant `servicePrincipal.user` role to deploying users

Any user (or CI/CD identity) that deploys jobs with `run_as` pointing at this SP must hold the **Service Principal User** role on it. Without this, `bundle deploy` fails with:

> *Cannot bind the service principal provided in 'run_as' field … The user creating or updating the job must have 'servicePrincipal.user' role on the service principal.*

This is an **account-level** role, not a workspace-level setting.

### UI

1. **Account Console** (accounts.cloud.databricks.com) → **User management** → **Service principals** tab
2. Click the SP (e.g. `caresource-archive-dev`)
3. **Permissions** tab → **Grant access**
4. Search for the deploying user (e.g. `sandeep.manocha@databricks.com`) → assign role **Service Principal: User**

Repeat for each human or CI/CD SP that will run `bundle deploy` against this target.

> **Manage ≠ Use.** The **Service Principal: Manager** role lets you manage roles on the SP, but it does **not** grant the ability to impersonate it via `run_as`. You must explicitly grant **Service Principal: User** — even if you already have Manager. The Permissions tab in the Account Console shows this warning.

> **Account Console, not workspace.** The workspace Admin Settings also shows service principals, but the role grant must be done at the **Account Console**. Granting at the workspace level does not satisfy the `run_as` check.

### CLI (for CI/CD automation)

Grant is a **rule set** on the SP (use the SP's **numeric SCIM id**, not the UUID):

```bash
# 1. GET rule set → note the etag
databricks account access-control get-rule-set \
  --name "accounts/<account-id>/servicePrincipals/<sp-scim-id>/ruleSets/default" \
  --etag "" --profile account-admin -o json

# 2. PUT it back with servicePrincipal.user added to grant_rules
#    (must include ALL existing grant_rules or you'll wipe them)
databricks account access-control update-rule-set --profile account-admin --json @ruleset.json
```

Where `ruleset.json` contains the name, the etag from step 1, and a `grant_rules` array including the new entry `{"principals":["users/<deployer-scim-id>"],"role":"roles/servicePrincipal.user"}`.

---

## Step 2c — Grant workspace folder access for `run_as`

When jobs use `run_as`, the SP executes notebooks from the bundle's workspace folder (e.g. `/Workspace/Users/<you>/.bundle/...`). The SP needs **CAN_RUN** on that directory.

Workspace permissions inherit from parent → child. Choose the level that fits your needs:

| Grant on | Covers |
|---|---|
| `/Users/<you>/.bundle/caresource-archive/<target>` | Single target only |
| `/Users/<you>/.bundle` | All bundles & targets for this user (recommended) |
| `/Users/<you>` | Everything in your user folder (broadest) |

### CLI

```bash
# 1. Get the object ID of the directory you want to grant on
databricks workspace get-status /Users/<you>/.bundle \
  --profile <workspace-profile>
# → note the object_id from the output

# 2. Grant CAN_RUN (uses application ID, not display name)
databricks permissions update directories <object-id> \
  --json '{"access_control_list": [{"service_principal_name": "<sp-application-id>", "permission_level": "CAN_RUN"}]}' \
  --profile <workspace-profile>
```

### UI alternative

1. **Workspace** sidebar → navigate to the chosen directory
2. Right-click → **Permissions** → add the SP with **Can Run**

> **CAN_READ is not enough.** The SP needs CAN_RUN to execute notebooks, not just CAN_READ.

### CI/CD note

For qa/stage/prod deployed via Azure DevOps, the bundle lands under the **CI/CD SP's home folder** (`/Users/<cicd-sp-uuid>/.bundle/...`), not yours. Grant the env `run_as` SP `CAN_RUN` there — or avoid the coupling entirely by setting a shared path in `databricks.yml`:

```yaml
qa:
  workspace:
    root_path: /Shared/bundles/caresource-archive/qa
```

Then grant `CAN_RUN` once on `/Shared/bundles/caresource-archive`. The CI/CD SP also needs `servicePrincipal.user` on the env SP (Step 2b applied to the CI/CD SP).

---

## Step 3 — Grant UC permissions

Run as **metastore admin** or **catalog owner**.

### Fast rules

- Use the SP **Application ID** UUID in every grant (not display name).
- Run grants per environment using that target's catalog/schema names.
- `CREATE VIEW` is **not** a valid schema-level privilege in modern UC — `CREATE TABLE` covers view creation.
- Ignore `notebooks/manual/grant_sp_permissions.py` — it uses the legacy external-location model and is out of sync with this runbook.

### Minimum grants checklist

| Area | Required grants |
|---|---|
| Config schema | `USE CATALOG`, `USE SCHEMA`, `CREATE TABLE`, `SELECT`, `MODIFY` |
| Config catalog (dev only) | `CREATE SCHEMA` (only if jobs auto-create schemas) |
| Source schemas | `USE CATALOG`, `USE SCHEMA`, `SELECT`, `MODIFY` |
| Archive volume schema | `USE SCHEMA`, `CREATE TABLE` |
| Archive volume | `READ VOLUME`, `WRITE VOLUME` |
| Rehydration schema | `USE CATALOG`, `USE SCHEMA`, `CREATE TABLE` |
| External volume Delta writes | `MODIFY ON ANY FILE`, `SELECT ON ANY FILE` |

> **Why `ANY FILE`?** Writing Delta to an External Volume path goes through Spark's Delta writer, which still checks legacy workspace-level file ACLs in addition to UC `READ/WRITE VOLUME`. Without these two grants the archive job fails with `INSUFFICIENT_PERMISSIONS: MODIFY,SELECT on any file` (verified in test `05R`). Remove once Databricks closes the gap.

### SQL template (copy/paste)

```sql
-- Replace: <sp-app-id> (UUID), and the catalog/schema/volume names.

-- 1) Config
GRANT USE CATALOG  ON CATALOG <config_catalog>                 TO `<sp-app-id>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY
  ON SCHEMA <config_catalog>.<config_schema>                   TO `<sp-app-id>`;
-- Dev only (if jobs auto-create schemas):
-- GRANT CREATE SCHEMA ON CATALOG <config_catalog>             TO `<sp-app-id>`;

-- 2) Source schema(s)
GRANT USE CATALOG ON CATALOG <source_catalog>                  TO `<sp-app-id>`;
GRANT USE SCHEMA, SELECT, MODIFY
  ON SCHEMA <source_catalog>.<source_schema>                   TO `<sp-app-id>`;

-- 3) Archive schema + volume
GRANT USE SCHEMA, CREATE TABLE
  ON SCHEMA <archive_catalog>.<archive_schema>                 TO `<sp-app-id>`;
GRANT READ VOLUME, WRITE VOLUME
  ON VOLUME <archive_catalog>.<archive_schema>.<archive_volume> TO `<sp-app-id>`;

-- 4) Rehydration target
GRANT USE CATALOG ON CATALOG <rehydration_catalog>             TO `<sp-app-id>`;
GRANT USE SCHEMA, CREATE TABLE
  ON SCHEMA <rehydration_catalog>.<rehydration_schema>         TO `<sp-app-id>`;

-- 5) Legacy ANY FILE — required (see note above)
GRANT MODIFY ON ANY FILE TO `<sp-app-id>`;
GRANT SELECT ON ANY FILE TO `<sp-app-id>`;
```

### Verify

```sql
SHOW GRANTS `<sp-app-id>` ON CATALOG <config_catalog>;
SHOW GRANTS `<sp-app-id>` ON SCHEMA  <config_catalog>.<config_schema>;
SHOW GRANTS `<sp-app-id>` ON SCHEMA  <source_catalog>.<source_schema>;
SHOW GRANTS `<sp-app-id>` ON SCHEMA  <archive_catalog>.<archive_schema>;
SHOW GRANTS `<sp-app-id>` ON VOLUME  <archive_catalog>.<archive_schema>.<archive_volume>;
SHOW GRANTS `<sp-app-id>` ON SCHEMA  <rehydration_catalog>.<rehydration_schema>;
SHOW GRANTS `<sp-app-id>` ON ANY FILE;
```

### Common failures

- `PRINCIPAL_DOES_NOT_EXIST` — used display name instead of Application ID UUID.
- `PRIVILEGE_NOT_APPLICABLE_TO_ENTITY ... CREATE VIEW` — remove `CREATE VIEW` (use `CREATE TABLE`).
- Volume `PERMISSION_DENIED` despite volume grants — missing `USE SCHEMA` on the parent schema.
- Delta write fails with `MODIFY,SELECT on any file` — missing the ANY FILE grants.

---

## Step 4 — Verify end-to-end

`bundle validate` is **client-side only** — it does not talk to the server about SP binding, `run_as` role, workspace folder ACLs, or UC grants. Real verification requires a deploy + run.

```bash
databricks bundle validate -t <target> --profile <workspace-profile>   # syntax
databricks bundle deploy   -t <target> --profile <workspace-profile>   # exercises run_as role + folder ACL
databricks bundle run setup_config_tables -t <target> --profile <workspace-profile>  # exercises UC grants
```

Open the run URL and confirm **Run as** shows the env SP (e.g. `caresource-archive-dev`), not your user, and status is `Succeeded`. If deploy fails with `Cannot bind the service principal ... run_as`, revisit Step 2b. If the run fails on a UC `PERMISSION_DENIED`, revisit Step 3.
