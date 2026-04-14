# CareSource Delta Table Archive — High-Level Architecture

## Solution Overview

The CareSource Delta Table Archive is a configuration-driven framework for systematically archiving aged data from Unity Catalog Delta tables into cost-effective external storage, with on-demand rehydration when historical data is needed again.

All configuration lives in Delta tables — no JSON files, no code changes between environments. Each workspace has its own config tables with environment-specific values, and a single job parameter bootstraps the entire process.

---

## 1. System Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

graph TD
    subgraph CONFIG["⚙️ Configuration Layer — Delta Tables"]
        direction LR
        GS["<b>Global Settings</b><br/>Bootstrap entry point<br/><i>1 row — pointers to all config</i>"]
        ST["<b>Schema Templates</b><br/>Per-schema archive rules<br/><i>date patterns · retention · thresholds</i>"]
        TC["<b>Table Configs</b><br/>Per-table archive settings<br/><i>date column · conditions · paths</i>"]
    end

    subgraph DISCOVERY["🔍 Auto-Discovery"]
        SCANNER["<b>Schema Scanner</b><br/>Discover tables in Unity Catalog<br/>Match date columns · Check sizes<br/>Detect ambiguity"]
        STAGING["<b>Staging Table</b><br/>Scanner output"]
    end

    subgraph ENGINE["🏗️ Core Processing"]
        ARCHIVE["<b>Archive Engine</b><br/>Evaluate retention & conditions<br/>Write year partitions<br/>Verify counts · Delete from source"]
        REHYDRATE["<b>Rehydration Engine</b><br/>Create zero-copy external tables<br/>Build unified views"]
    end

    subgraph STORAGE["📦 Archive Storage — External Volumes"]
        direction LR
        Y1["year_2020/<br/>Delta External Table<br/>+ metadata.json"]
        Y2["year_2021/<br/>Delta External Table<br/>+ metadata.json"]
        YN["year_NNNN/<br/>..."]
    end

    subgraph TARGET["🎯 Rehydrated Data"]
        direction LR
        EXT["External Tables<br/><i>Zero-copy pointers</i>"]
        VIEW["Unified View<br/><i>Current + Historical</i>"]
    end

    AUDIT["<b>📋 Audit Log</b><br/>Every action tracked<br/>Resume · Concurrency guards"]

    GS --> ST & TC
    ST --> SCANNER
    SCANNER --> STAGING
    STAGING -->|"MERGE"| TC
    GS & TC --> ARCHIVE
    ARCHIVE --> Y1 & Y2 & YN
    Y1 & Y2 & YN --> REHYDRATE
    REHYDRATE --> EXT --> VIEW
    ARCHIVE --> AUDIT
    REHYDRATE --> AUDIT

    style CONFIG fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style DISCOVERY fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style ENGINE fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style STORAGE fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style TARGET fill:#ffffff,stroke:#94a3b8,color:#1a1a2e

    style GS fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style ST fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style TC fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style SCANNER fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style STAGING fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style ARCHIVE fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style REHYDRATE fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style Y1 fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style Y2 fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style YN fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style EXT fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style VIEW fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style AUDIT fill:#b07d3a,stroke:#8a5f20,color:#ffffff
```

---

## 2. End-to-End Data Flow

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

graph LR
    subgraph ACTIVE["Active Data — Unity Catalog"]
        SRC["<b>Source Tables</b><br/>All current data<br/>healthcare.claims.*<br/>healthcare.members.*"]
    end

    subgraph PROCESS["Archive Processing"]
        direction TB
        EVAL["Evaluate<br/>Retention Rules"]
        COND["Apply Exclusion<br/>Conditions"]
        WRITE["Write to<br/>External Volume"]
        VERIFY["Verify Counts<br/>& Ownership"]
        DEL["Delete from<br/>Source"]
        EVAL --> COND --> WRITE --> VERIFY --> DEL
    end

    subgraph VOL["External Volume Storage"]
        A20["year_2020/"]
        A21["year_2021/"]
        A22["year_2022/"]
    end

    subgraph REHYD["On-Demand Rehydration"]
        EXT["External Tables<br/><i>Zero-copy</i>"]
        UV["Unified View<br/><i>Live + Historical</i>"]
        EXT --> UV
    end

    SRC -->|"Aged records"| EVAL
    DEL -->|"Archive files"| A20 & A21 & A22
    A20 & A21 & A22 -->|"Rehydrate"| EXT
    SRC -->|"Live data"| UV

    style ACTIVE fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style PROCESS fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style VOL fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style REHYD fill:#ffffff,stroke:#94a3b8,color:#1a1a2e

    style SRC fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style EVAL fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style COND fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style WRITE fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style VERIFY fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style DEL fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style A20 fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style A21 fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style A22 fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style EXT fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style UV fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
```

---

## 3. Configuration Bootstrap

A single job parameter (`config_table`) bootstraps the entire system. The global settings table contains pointers to all other configuration — no environment overlays, no JSON files.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

flowchart LR
    PARAM["<b>Job Parameter</b><br/>config_table =<br/>catalog.schema.global_settings"]
    GS["<b>Global Settings</b><br/><i>1 row — Delta table</i><br/>audit location · concurrency<br/>dry-run · secret scope<br/>archive path prefix"]
    ST["<b>Schema Templates</b><br/><i>1 row per schema</i><br/>date patterns · retention<br/>size thresholds · exclusions"]
    TC["<b>Table Configs</b><br/><i>1 row per table</i><br/>date column · retention<br/>conditions · archive path"]
    SEC["<b>Secret Scope</b><br/>warehouse_id<br/>service principal creds"]
    RC["<b>Runtime Context</b><br/>settings + secrets +<br/>job metadata + run ID"]

    PARAM -->|"Read"| GS
    GS -->|"pointer"| ST
    GS -->|"pointer"| TC
    GS -->|"scope name"| SEC
    GS & SEC --> RC

    style PARAM fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style GS fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style ST fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style TC fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style SEC fill:#7a2a4a,stroke:#5c1a3a,color:#ffffff
    style RC fill:#2d6a4f,stroke:#1b4332,color:#ffffff
```

---

## 4. Schema Scanner — Auto-Discovery

The scanner automatically discovers tables in Unity Catalog, matches date columns using configurable patterns, evaluates table sizes, and onboards tables into the archive configuration — eliminating manual table-by-table setup.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

flowchart TD
    START(["Run Scanner"])
    TEMPLATES["Read Schema Templates<br/><i>active templates only</i>"]
    SCAN["Query Unity Catalog<br/>List all tables in schema"]
    EXCLUDE["Remove excluded tables"]
    MATCH{"Match date<br/>columns against<br/>patterns"}

    SINGLE["✅ Single Match<br/>Date column resolved"]
    AMBIG["⚠️ Ambiguous<br/>Multiple columns match<br/><i>flagged for review</i>"]
    NONE["❌ No Match<br/><i>flagged inactive</i>"]

    SIZE{"Table size<br/>≥ threshold?"}
    ACTIVE["Generate config row<br/><i>is_active = true</i>"]
    SMALL["Below threshold<br/><i>is_active = false</i>"]

    STAGING["Write to Staging Table"]
    MERGE["MERGE into Table Configs<br/><i>add new · update scanner-managed<br/>preserve manual edits</i>"]
    DONE(["Report Summary"])

    START --> TEMPLATES --> SCAN --> EXCLUDE --> MATCH
    MATCH -->|"1 column"| SINGLE
    MATCH -->|"multiple"| AMBIG
    MATCH -->|"none"| NONE
    SINGLE --> SIZE
    SIZE -->|"Yes"| ACTIVE
    SIZE -->|"No"| SMALL
    ACTIVE --> STAGING
    SMALL --> STAGING
    AMBIG --> STAGING
    NONE --> STAGING
    STAGING --> MERGE --> DONE

    style START fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style TEMPLATES fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style SCAN fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style EXCLUDE fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style MATCH fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style SINGLE fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style AMBIG fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style NONE fill:#8c2a2a,stroke:#6b1d1d,color:#ffffff
    style SIZE fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style ACTIVE fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style SMALL fill:#64748b,stroke:#475569,color:#ffffff
    style STAGING fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style MERGE fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style DONE fill:#2d6a4f,stroke:#1b4332,color:#ffffff
```

---

## 5. Archive Process — Per Table

Each table is processed independently (parallel via Lakeflow ForEach). The engine supports resumability, concurrency guards, incremental appends, dry-run mode, and count verification before any deletes.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

flowchart TD
    START(["Archive Table"])
    YEARS["Calculate eligible years<br/><i>based on retention policy</i>"]
    RESUME{"Previous run<br/>crashed after<br/>archive?"}
    RESUMEDEL["Resume: delete<br/>from source"]

    FOLDER{"Year folder<br/>already exists?"}
    WM{"New records<br/>above watermark?"}
    SKIPWM["Skip — no new data"]

    CLAIM["Claim: write STARTED<br/>to audit log"]
    CONC{"Concurrent<br/>run detected?"}
    SKIPCONC["Skip — concurrent"]

    COND["Apply exclusion conditions<br/><i>protect relevant records</i>"]
    DRY{"Dry run?"}
    REPORT["Report what WOULD<br/>happen — no changes"]

    EXIST{"Year folder<br/>exists?"}
    CREATE["Write new Delta<br/>External table"]
    APPEND["Append newly-eligible<br/>records"]

    META["Write metadata.json<br/><i>with watermark</i>"]
    VERIFY{"Counts match?"}
    FAIL(["Abort — no delete"])

    DELQ{"Delete from<br/>source?"}
    DEL["Remove archived<br/>records"]
    KEEP["Source unchanged"]
    LOG["Log to audit"]
    DONE(["Done"])

    START --> YEARS --> RESUME
    RESUME -->|"Yes"| RESUMEDEL --> LOG
    RESUME -->|"No"| FOLDER
    FOLDER -->|"No"| CLAIM
    FOLDER -->|"Yes"| WM
    WM -->|"No new data"| SKIPWM --> LOG
    WM -->|"New data"| CLAIM
    CLAIM --> CONC
    CONC -->|"Yes"| SKIPCONC --> LOG
    CONC -->|"No"| COND --> DRY
    DRY -->|"Yes"| REPORT --> LOG
    DRY -->|"No"| EXIST
    EXIST -->|"No"| CREATE
    EXIST -->|"Yes"| APPEND
    CREATE --> META
    APPEND --> META
    META --> VERIFY
    VERIFY -->|"No"| FAIL
    VERIFY -->|"Yes"| DELQ
    DELQ -->|"Yes"| DEL --> LOG
    DELQ -->|"No"| KEEP --> LOG
    LOG --> DONE

    style START fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style YEARS fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style RESUME fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style RESUMEDEL fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style FOLDER fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style WM fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style SKIPWM fill:#64748b,stroke:#475569,color:#ffffff
    style CLAIM fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style CONC fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style SKIPCONC fill:#64748b,stroke:#475569,color:#ffffff
    style COND fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style DRY fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style REPORT fill:#64748b,stroke:#475569,color:#ffffff
    style EXIST fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style CREATE fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style APPEND fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style META fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style VERIFY fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style FAIL fill:#8c2a2a,stroke:#6b1d1d,color:#ffffff
    style DELQ fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style DEL fill:#8c2a2a,stroke:#6b1d1d,color:#ffffff
    style KEEP fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style LOG fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style DONE fill:#2d6a4f,stroke:#1b4332,color:#ffffff
```

---

## 6. Rehydration — On-Demand Restore

Rehydration creates zero-copy external tables over archive folders and a unified view combining live and historical data. No data is copied — external tables point directly to archive storage.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

flowchart LR
    REQ["<b>Rehydrate Request</b><br/>source table · years<br/>target catalog/schema"]
    SCHEMA["Create target<br/>schema if needed"]

    subgraph LOOP["For Each Requested Year"]
        direction TB
        CHECK{"Archive<br/>exists?"}
        CREATE["Create external table<br/><i>zero-copy pointer</i>"]
        SKIP["Skip year"]
        CHECK -->|"Yes"| CREATE
        CHECK -->|"No"| SKIP
    end

    VIEW["Create unified view<br/><b>Live + All Archived Years</b>"]
    LOG["Log to audit"]

    REQ --> SCHEMA --> LOOP --> VIEW --> LOG

    style LOOP fill:#ffffff,stroke:#94a3b8,color:#1a1a2e

    style REQ fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style SCHEMA fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style CHECK fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style CREATE fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style SKIP fill:#64748b,stroke:#475569,color:#ffffff
    style VIEW fill:#5b3e96,stroke:#3d2670,color:#ffffff
    style LOG fill:#b07d3a,stroke:#8a5f20,color:#ffffff
```

---

## 7. Safety & Rollback Layers

No separate rollback mechanism is needed. Safety is built into the process at every layer.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

flowchart TD
    L1["<b>① Dry Run</b><br/>See blast radius before<br/>touching any data"]
    L2["<b>② Archive-Only Mode</b><br/>Archive without deleting —<br/>data exists in both places"]
    L3["<b>③ Verify Before Delete</b><br/>Count match required —<br/>abort on mismatch"]
    L4["<b>④ Delta Time Travel</b><br/>30-day recovery window<br/>for deleted records"]
    L5["<b>⑤ Rehydrate to Restore</b><br/>Restore archived data<br/>to any catalog/schema"]

    L1 --> L2 --> L3 --> L4 --> L5

    style L1 fill:#1a7a5c,stroke:#0d5c3f,color:#ffffff
    style L2 fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style L3 fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style L4 fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style L5 fill:#1a3a5c,stroke:#0d253f,color:#ffffff
```

| Layer | Purpose | When to Use |
|-------|---------|-------------|
| **Dry Run** | Preview archive impact with zero data changes | Before every production run |
| **Archive-Only** | Write archive but keep source data intact | First few cycles for new tables |
| **Verify Before Delete** | Abort delete if archived count ≠ source count | Every delete (automatic) |
| **Delta Time Travel** | Recover deleted records via `VERSION AS OF` | Emergency — within 30 days |
| **Rehydrate** | Restore archived data to any location | When historical data is needed again |

---

## 8. Audit & Observability

Every archive and rehydration action is logged to Delta audit tables with a durable correlation key (`archive_run_id`) that links all artifacts from a single job run.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

stateDiagram-v2
    direction LR

    [*] --> STARTED : claim table + year
    [*] --> DRY_RUN : dry_run = true
    [*] --> NO_DATA : no records for year
    [*] --> SKIPPED : no new data above watermark
    [*] --> SKIPPED_CONCURRENT : another run owns this

    STARTED --> ARCHIVED : write/append succeeded
    STARTED --> FAILED : write failed
    STARTED --> SKIPPED_CONCURRENT : concurrent run detected

    ARCHIVED --> ARCHIVED_AND_DELETED : delete succeeded
    ARCHIVED --> FAILED : delete failed
    ARCHIVED --> ARCHIVED : delete_after_archive = false
    ARCHIVED --> STARTED : next run appends new records
```

| Status | Meaning |
|--------|---------|
| **STARTED** | Archive in progress — used for concurrency detection |
| **DRY_RUN** | Preview completed, no data touched |
| **ARCHIVED** | Records written to archive storage |
| **ARCHIVED_AND_DELETED** | Records archived and removed from source |
| **SKIPPED** | No new data above watermark |
| **SKIPPED_CONCURRENT** | Another run already processing this table |
| **FAILED** | Operation failed — error details in audit log |
| **NO_DATA** | No records found for the year |

---

## 9. CI/CD & Multi-Environment Deployment

Same code promotes through all environments via Databricks Asset Bundles (DABs). Each environment has its own config Delta tables, secret scope, and service principal.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

flowchart LR
    subgraph PR["Stage 1 — PR Validation"]
        DEPS["Install deps"]
        TEST["Unit tests<br/><i>no cluster needed</i>"]
        VAL["Bundle validate"]
        DEPS --> TEST --> VAL
    end

    subgraph DEV["Stage 2 — Dev Deploy"]
        DEPLOY["Bundle deploy<br/><i>user identity</i>"]
        INT["Integration tests<br/><i>optional</i>"]
        DEPLOY --> INT
    end

    subgraph PROMOTE["Stage 3 — Promote via M2M OAuth"]
        QA["Deploy to QA<br/><i>service principal</i>"]
        GATE["Manual<br/>approval"]
        PROD["Deploy to Prod<br/><i>service principal</i>"]
        QA --> GATE --> PROD
    end

    PR -->|"merge"| DEV -->|"release"| PROMOTE

    style PR fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style DEV fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style PROMOTE fill:#ffffff,stroke:#94a3b8,color:#1a1a2e

    style DEPS fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style TEST fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style VAL fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style DEPLOY fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style INT fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style QA fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
    style GATE fill:#b07d3a,stroke:#8a5f20,color:#ffffff
    style PROD fill:#8c5a2a,stroke:#5c3a1a,color:#ffffff
```

| Environment | Identity | Config Source | Secrets |
|-------------|----------|---------------|---------|
| **Dev** | User identity | `dev_config.config.global_settings` | `archive-dev` scope |
| **QA** | Service principal | `qa_config.config.global_settings` | `archive-qa` scope |
| **Stage** | Service principal | `stage_config.config.global_settings` | `archive-stage` scope |
| **Prod** | Service principal | `prod_config.config.global_settings` | `archive-prod` scope |

---

## 10. Security & Governance

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#1a1a2e',
  'primaryBorderColor': '#d0d5dd',
  'lineColor': '#4a6fa5',
  'secondaryColor': '#ffffff',
  'tertiaryColor': '#ffffff',
  'fontFamily': 'Segoe UI, Roboto, sans-serif'
}}}%%

flowchart TD
    subgraph UC["Unity Catalog Governance"]
        direction TB
        CAT["Catalog-level<br/>access control"]
        SCHEMA["Schema-level<br/>permissions"]
        TABLE["Table-level<br/>grants"]
        VOL["External Volume<br/>write control"]
    end

    subgraph AUTH["Authentication"]
        direction TB
        SP["Service Principal<br/>per environment"]
        OAUTH["M2M OAuth<br/>for CI/CD"]
        SCOPE["Secret Scopes<br/>for credentials"]
    end

    subgraph AUDIT_SEC["Audit Trail"]
        direction TB
        ALOG["Every action logged<br/>with run correlation ID"]
        META["Metadata JSON<br/>per archive folder"]
        TT["Delta time travel<br/>on all tables"]
    end

    UC ~~~ AUTH ~~~ AUDIT_SEC

    style UC fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style AUTH fill:#ffffff,stroke:#94a3b8,color:#1a1a2e
    style AUDIT_SEC fill:#ffffff,stroke:#94a3b8,color:#1a1a2e

    style CAT fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style SCHEMA fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style TABLE fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style VOL fill:#1a3a5c,stroke:#0d253f,color:#ffffff
    style SP fill:#7a2a4a,stroke:#5c1a3a,color:#ffffff
    style OAUTH fill:#7a2a4a,stroke:#5c1a3a,color:#ffffff
    style SCOPE fill:#7a2a4a,stroke:#5c1a3a,color:#ffffff
    style ALOG fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style META fill:#2d6a4f,stroke:#1b4332,color:#ffffff
    style TT fill:#2d6a4f,stroke:#1b4332,color:#ffffff
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Config in Delta tables** (not JSON) | Schema enforcement, time travel, audit columns, no environment overlay complexity |
| **External Volumes** (not direct cloud paths) | Unity Catalog governed, portable across clouds, discoverable |
| **Year-based partitioning** | Natural boundary for retention policies, simple to reason about |
| **Append-only archives** | Delta handles incremental writes natively; no overwrite footgun |
| **Watermark-based incrementals** | Works correctly whether source deletes are enabled or not |
| **Zero-copy rehydration** | External tables point to archive storage — no data duplication |
| **Durable `archive_run_id`** | Survives after Lakeflow job history ages out of system tables |
| **Scanner with manual-edit preservation** | Auto-discovery for scale, manual overrides preserved across re-scans |
