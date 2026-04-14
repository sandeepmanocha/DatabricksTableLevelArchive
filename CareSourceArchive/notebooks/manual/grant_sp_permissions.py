# Databricks notebook source

# COMMAND ----------
# Parameters — adjust defaults per environment
dbutils.widgets.text("sp_name", "", "Service Principal Name")
dbutils.widgets.text("source_catalog", "", "Source Catalog")
dbutils.widgets.text("source_schemas", "", "Source Schemas (comma-separated)")
dbutils.widgets.text("audit_catalog", "", "Audit Catalog")
dbutils.widgets.text("audit_schema", "", "Audit Schema")
dbutils.widgets.text("rehydration_schema", "", "Rehydration Schema")
dbutils.widgets.text("external_location", "", "Archive External Location")
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"], "Dry Run (show SQL only)")

sp_name = dbutils.widgets.get("sp_name").strip()
source_catalog = dbutils.widgets.get("source_catalog").strip()
source_schemas = [s.strip() for s in dbutils.widgets.get("source_schemas").split(",") if s.strip()]
audit_catalog = dbutils.widgets.get("audit_catalog").strip()
audit_schema = dbutils.widgets.get("audit_schema").strip()
rehydration_schema = dbutils.widgets.get("rehydration_schema").strip()
external_location = dbutils.widgets.get("external_location").strip()
dry_run = dbutils.widgets.get("dry_run") == "true"

# COMMAND ----------
required = {
    "sp_name": sp_name,
    "source_catalog": source_catalog,
    "source_schemas": source_schemas,
    "audit_catalog": audit_catalog,
    "audit_schema": audit_schema,
    "rehydration_schema": rehydration_schema,
    "external_location": external_location,
}
missing = [k for k, v in required.items() if not v]
if missing:
    raise ValueError(f"Required parameters are empty: {', '.join(missing)}")

print(f"SP:                  {sp_name}")
print(f"Source:              {source_catalog}.({', '.join(source_schemas)})")
print(f"Audit:               {audit_catalog}.{audit_schema}")
print(f"Rehydration:         {audit_catalog}.{rehydration_schema}")
print(f"External location:   {external_location}")
print(f"Mode:                {'DRY RUN' if dry_run else 'APPLY'}")

# COMMAND ----------
def _build_grant_statements(
    sp: str,
    src_catalog: str,
    src_schemas: list[str],
    aud_catalog: str,
    aud_schema: str,
    rehyd_schema: str,
    ext_location: str,
) -> list[tuple[str, str]]:
    """Return (category, sql) pairs for all required grants."""
    stmts = []

    # Source catalog
    stmts.append(("source", f"GRANT USE CATALOG ON CATALOG `{src_catalog}` TO `{sp}`"))
    for schema in src_schemas:
        fq = f"`{src_catalog}`.`{schema}`"
        stmts.append(("source", f"GRANT USE SCHEMA ON SCHEMA {fq} TO `{sp}`"))
        stmts.append(("source", f"GRANT SELECT ON SCHEMA {fq} TO `{sp}`"))
        stmts.append(("source", f"GRANT MODIFY ON SCHEMA {fq} TO `{sp}`"))

    # Audit catalog + schema
    stmts.append(("audit", f"GRANT USE CATALOG ON CATALOG `{aud_catalog}` TO `{sp}`"))
    aud_fq = f"`{aud_catalog}`.`{aud_schema}`"
    stmts.append(("audit", f"GRANT USE SCHEMA ON SCHEMA {aud_fq} TO `{sp}`"))
    stmts.append(("audit", f"GRANT CREATE TABLE ON SCHEMA {aud_fq} TO `{sp}`"))
    stmts.append(("audit", f"GRANT SELECT ON SCHEMA {aud_fq} TO `{sp}`"))
    stmts.append(("audit", f"GRANT MODIFY ON SCHEMA {aud_fq} TO `{sp}`"))

    # Archive external location
    stmts.append(("external_location", f"GRANT WRITE FILES ON EXTERNAL LOCATION `{ext_location}` TO `{sp}`"))
    stmts.append(("external_location", f"GRANT READ FILES ON EXTERNAL LOCATION `{ext_location}` TO `{sp}`"))

    # Rehydration target (same catalog as audit)
    rehyd_fq = f"`{aud_catalog}`.`{rehyd_schema}`"
    stmts.append(("rehydration", f"GRANT USE SCHEMA ON SCHEMA {rehyd_fq} TO `{sp}`"))
    stmts.append(("rehydration", f"GRANT CREATE TABLE ON SCHEMA {rehyd_fq} TO `{sp}`"))
    stmts.append(("rehydration", f"GRANT CREATE VIEW ON SCHEMA {rehyd_fq} TO `{sp}`"))

    return stmts


grants = _build_grant_statements(
    sp_name, source_catalog, source_schemas,
    audit_catalog, audit_schema, rehydration_schema, external_location,
)

# COMMAND ----------
# Execute or preview grants
results = []
for category, sql in grants:
    if dry_run:
        print(f"[DRY RUN] {sql}")
        results.append((category, sql, "dry_run"))
    else:
        try:
            spark.sql(sql)
            print(f"[OK]      {sql}")
            results.append((category, sql, "ok"))
        except Exception as e:
            print(f"[FAILED]  {sql}\n          {e}")
            results.append((category, sql, f"FAILED: {e}"))

# COMMAND ----------
# Verify grants (runs regardless of dry_run — shows current state)
securables = [
    ("CATALOG", source_catalog),
    *[(f"SCHEMA  `{source_catalog}`.`{s}`", None) for s in source_schemas],
    ("CATALOG", audit_catalog),
    (f"SCHEMA  `{audit_catalog}`.`{audit_schema}`", None),
    (f"SCHEMA  `{audit_catalog}`.`{rehydration_schema}`", None),
    (f"EXTERNAL LOCATION `{external_location}`", None),
]

html = [
    "<h3>UC Grants for <code>{sp}</code></h3>".format(sp=sp_name),
    "<p>Mode: <b>{mode}</b></p>".format(mode="DRY RUN" if dry_run else "APPLIED"),
]

for securable, name in securables:
    if name:
        query = f"SHOW GRANTS `{sp_name}` ON {securable} `{name}`"
    else:
        query = f"SHOW GRANTS `{sp_name}` ON {securable}"
    try:
        df = spark.sql(query)
        rows = df.collect()
        privileges = ", ".join(sorted(r["privilege"] for r in rows)) if rows else "<none>"
    except Exception as e:
        privileges = f"(query failed: {e})"

    label = f"{securable} {name}" if name else securable
    html.append(f"<p><code>{label}</code> &rarr; {privileges}</p>")

displayHTML("".join(html))
