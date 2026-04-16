# Bug report: duplicate names in `all_matched_columns` (false ambiguous match)

## Summary

For some Unity Catalog / Delta tables, the scanner records `all_matched_columns` with **duplicate** entries for a single logical column (e.g. `["event_date", "event_date"]`) and sets `match_status` to **ambiguous**, even though the table has only **one** `event_date` field. This is a **scanner introspection bug**, not a real multi-column pattern match.

## Resolved

Column discovery switched from `DESCRIBE TABLE` (which repeats partition columns in extended output) to `{catalog}.information_schema.columns` — a single authoritative metadata source with one row per column, ordered by `ordinal_position`. The `ensure_scanner_log_table` existence check was also migrated from `DESCRIBE TABLE` to `information_schema.tables`. See [2026-04-06-information-schema-column-discovery-plan.md](superpowers/specs/2026-04-06-information-schema-column-discovery-plan.md) for the full design.

## Repro (Databricks)

| Item | Value |
|------|--------|
| **Scanner log table** | `sandeep_manocha.caresource_audit.scanner_log` |
| **Scan run id** | `b4d95c88-861f-44c0-8b8d-503b23cdee93` |
| **Table id** | `sandeep_manocha.source_data_samples.bronze_table_lineage` |

### Query (profile: `DEFAULT`)

```sql
SELECT
  scan_run_id,
  table_id,
  match_status,
  matched_column,
  matched_pattern,
  ambiguity_detail,
  all_matched_columns
FROM sandeep_manocha.caresource_audit.scanner_log
WHERE scan_run_id = 'b4d95c88-861f-44c0-8b8d-503b23cdee93'
  AND table_id = 'sandeep_manocha.source_data_samples.bronze_table_lineage';
```

### Observed row

| Field | Value |
|--------|--------|
| `match_status` | `ambiguous` |
| `matched_pattern` | `event_date` |
| `ambiguity_detail` | `pattern event_date matched [event_date, event_date]` |
| `all_matched_columns` | `["event_date", "event_date"]` |

## Evidence from `DESCRIBE TABLE`

`DESCRIBE TABLE sandeep_manocha.source_data_samples.bronze_table_lineage` returns **one** `event_date` in the main column list, then metadata rows (`# Clustering Information`, `# col_name`, …), then **`event_date` again** (partition column metadata). The physical table does not have two distinct `event_date` columns; Spark repeats the partition column in the extended DESCRIBE output.

Databricks CLI (`databricks experimental aitools tools discover-schema`, profile `DEFAULT`) also lists `event_date: date` **twice** in the column summary for this table, consistent with duplicate names in schema introspection.

## Root cause (code)

In `src/scanner.py`, `_list_table_columns` builds the column list from `DESCRIBE TABLE`:

```399:420:src/scanner.py
def _list_table_columns(spark, catalog, schema, table):
    fq = build_full_table_name(catalog, schema, table)
    df = spark.sql(f"DESCRIBE TABLE {fq}")
    out = []
    for row in df.collect():
        d = row.asDict() if hasattr(row, "asDict") else {}
        name = d.get("col_name")
        if name is None and hasattr(row, "__getitem__"):
            try:
                name = row[0]
            except Exception:
                name = None
        if name is None:
            continue
        name = str(name).strip()
        if not name or name.startswith("#"):
            continue
        lname = name.lower()
        if "partition" in lname and "information" in lname:
            break
        out.append(name)
    return out
```

- Rows whose `col_name` starts with `#` (e.g. `# Clustering Information`) are **skipped** but do **not** stop the loop.
- The intended stop is a row whose name contains both **partition** and **information** (e.g. `# Partition Information` in some Spark outputs). For this table, the section header is **`# Clustering Information`**, so the **break never runs**.
- After that section, DESCRIBE can **repeat** partition columns such as `event_date`, so the same name is **appended twice** to `out`.

`match_date_column` then does an exact match for pattern `event_date`:

```43:48:src/scanner.py
def match_date_column(table_columns, patterns):
    for pattern in patterns:
        exact = [c for c in table_columns if c == pattern]
        if len(exact) == 1:
            return exact[0], pattern, list(exact), "matched"
        if len(exact) > 1:
            return None, pattern, exact, "ambiguous"
```

With `table_columns == ["event_date", "event_date"]`, `len(exact) > 1` → **false ambiguous**.

## Why a `set` alone is not enough

Deduplicating with a `set` would remove duplicate strings and could turn this case into a **matched** outcome, but:

- It would also collapse **genuine** duplicate column names if they ever appeared as distinct list entries (unusual but possible in malformed introspection).
- It does not fix **incorrect column lists** for downstream use (e.g. anything that assumes one entry per physical column).

Preferred directions:

1. **Stop parsing** after DESCRIBE metadata sections: treat `# ...` headers as section boundaries and `break` (or skip subsequent data rows until end), including **Clustering** / **Partition** style sections, aligned with Spark/Databricks DESCRIBE behavior.
2. Optionally **dedupe preserving order** (`dict.fromkeys` / ordered unique) as a safety net after fixing section handling.
3. Alternative: use **`information_schema.columns`** or a UC API that returns **one row per column** without partition repetition.

## Analysis method

Findings were confirmed with Databricks CLI (default skill path: experimental AI tools) using **`--profile DEFAULT`**:

- `databricks experimental aitools tools query "<SQL>" --profile DEFAULT`
- `databricks experimental aitools tools discover-schema sandeep_manocha.source_data_samples.bronze_table_lineage --profile DEFAULT`

## Status

**Resolved.** `_list_table_columns` now queries `information_schema.columns`; `ensure_scanner_log_table` queries `information_schema.tables`. No `DESCRIBE TABLE` remains in `src/scanner.py` for metadata introspection.
