# CareSource Archive — Prompt Document

## Original Prompt (as provided)

```
@CareSourceArchivePOC  This was POC design, now we are going to do actual implementation
Some requirements are
1. Config based design
2. Each table should have a configuration to define the datetime column we can use
   and a new fields which will define some conditions, for example record might be
   old but based on Status_flag and Recent_claim date we should not move this to archive
3. This condition column can be a simple check within same table or It can expand to
   other tables as sub-queries, we can say if condition scope is within table or run
   a sub-query. think on these lines
4. Activate and Deactivate the archive for specific tables
5. Rehydration process to specific catalog and schema, driven by the process when we
   execute for example we may rehydrate same table for two different users for one
   year 2020, 2021 and for other year 2024, they both can be in different catalogs
   and schemas, so its better to have them as run-time parameters rather defined in config

Output
1. Lets first brainstorm these ideas and write a requirements document. Then later
   we will move to coding phase
2. Design Document which will explain the architecture, use Mermaid Charts for now
3. Feature list, something LLM agents can iterate for development in parallel
4. I am writing this prompt, so lets write a prompt md file as well with original
   and a best alternative
5. Tracker for things we have and features we have developed, tested so far
```

---

## Refined Prompt (best alternative)

```
## Context

We built a POC for Delta table archiving in `CareSourceArchivePOC/`. It works but is
parameter-driven with no business rules, no dry-run, and hardcoded year selection.
Now we need a production-grade implementation.

## Requirements

Build a config-driven archive and rehydration system for Databricks Delta tables:

### Configuration (Three-Tier, Delta tables, per-environment)
- **Global settings**: single-row Delta table — audit location, defaults, concurrency,
  pointers to other config tables. Job receives this table name as a parameter.
- **Schema templates**: Delta table, one row per schema — onboarding config with
  date column patterns (ARRAY<STRING>), default retention, archive paths,
  exclude lists (ARRAY<STRING>). Scanner reads from this table.
- **Table configs**: Delta table, one row per table. The archive job reads this
  table as primary input. Supports optional filter expressions for subset
  processing. Each row has: source location, date column, retention, archive
  path, active flag, exclusion conditions (ARRAY<STRUCT>).
- Exclusion conditions per table:
  - `same_table` scope: structured column/operator/value checks
  - `custom_sql` scope: freeform SQL with placeholders for cross-table lookups
  - OR logic: if ANY condition matches, the record is excluded from archiving
- Schema scanner onboards new catalogs/schemas: scan Unity Catalog, match
  date columns via patterns, generate table config files. Preserves manual
  overrides on re-scan.

### Archive Process
- Rolling retention window determines eligible years automatically
- Year-based archive folders — each year is a self-contained Delta table
- Dry-run mode (default: ON) — reports what would be archived with per-condition
  exclusion counts before touching any data
- Verify archive count matches expected before deleting from source
- Skip years already archived (idempotent)

### Rehydration Process
- All parameters at runtime (not config): target catalog, schema, years, prefix
- Supports multiple concurrent rehydrations of the same source table
  to different target locations
- Zero-copy only: external tables (LOCATION) or SHALLOW CLONE
- Optional unified view combining main table + rehydrated years

### Architecture
- Modular Python package (src/) with thin Databricks notebook wrappers
- No code duplication — shared utilities module
- Unit tests for config/conditions (no Spark needed)
- Integration tests for archive/rehydrate (requires Spark)
- DABs job definitions for orchestration

## Deliverables (before coding)
1. Requirements document with numbered requirement IDs
2. Design document with Mermaid architecture and flow diagrams
3. Feature list decomposed for parallel LLM agent development
4. This prompt file (original + refined)
5. Feature tracker (what's built, tested, remaining)
```

---

## What Changed and Why

| Aspect | Original | Refined |
|--------|----------|---------|
| Structure | Numbered list, conversational | Grouped by domain (config, archive, rehydrate, architecture) |
| Specificity | "conditions, for example..." | Explicit scope names, operator types, OR logic |
| Config | Single config file implied | Three-tier: global settings + schema templates + table configs |
| Scalability | Not addressed | Scanner for onboarding, flexible table config file organization |
| Architecture | Implied | Explicit: modular package, thin wrappers, shared utils |
| Dry-run | Not mentioned | Specified as default-on with per-condition reporting |
| Safety | Not mentioned | Verify-before-delete, idempotent re-runs |
| Deliverables | Mixed with requirements | Separate section at bottom |
| Ambiguity | "think on these lines" | Concrete decisions stated (same_table + custom_sql scopes) |
