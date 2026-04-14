# CareSource Delta Table Archive — Class & Module Diagram

## Module Hierarchy

![Module Hierarchy](images/module-hierarchy.svg)

> Source: [`images/module-hierarchy.dot`](images/module-hierarchy.dot) — regenerate with `dot -Tsvg module-hierarchy.dot -o module-hierarchy.svg`

---

## Test-Driven Development Framework

All production code was developed test-first. Unit tests run locally with **zero Databricks dependencies** — Spark, dbutils, and Delta tables are replaced by `unittest.mock.MagicMock` fixtures.

![TDD Framework](images/tdd-framework.svg)

> Source: [`images/tdd-framework.dot`](images/tdd-framework.dot) — regenerate with `dot -Tsvg tdd-framework.dot -o tdd-framework.svg`

---

## Color Legend

| Color | Meaning |
|-------|---------|
| ![#1a3a5c](https://placehold.co/16x16/1a3a5c/1a3a5c) **Dark Blue** | Configuration — settings, table configs, schema templates |
| ![#2d6a4f](https://placehold.co/16x16/2d6a4f/2d6a4f) **Dark Green** | Discovery — scanner, shared fixtures |
| ![#8c5a2a](https://placehold.co/16x16/8c5a2a/8c5a2a) **Amber** | Core Processing — ArchiveEngine |
| ![#1a7a5c](https://placehold.co/16x16/1a7a5c/1a7a5c) **Teal** | Rehydration — RehydrationEngine |
| ![#b07d3a](https://placehold.co/16x16/b07d3a/b07d3a) **Gold** | Audit — AuditLogger |
| ![#5b3e96](https://placehold.co/16x16/5b3e96/5b3e96) **Purple** | Foundation — RunContext, utils, mocks |
| ![#7a2a4a](https://placehold.co/16x16/7a2a4a/7a2a4a) **Maroon** | Conditions — exclusion SQL builders |
| ![#8c2a2a](https://placehold.co/16x16/8c2a2a/8c2a2a) **Red** | Exceptions — error hierarchy |

---

## Key Design Points

| Aspect | Approach |
|--------|----------|
| **Classes** | Only 3: `ArchiveEngine`, `RehydrationEngine`, `AuditLogger` — everything else is functions |
| **Data carrier** | `RunContext` dataclass — settings, secrets, job context in one object |
| **Exception hierarchy** | Single base `ArchiveError` with 3 domain-specific subclasses |
| **Testability** | Constructor injection of `ctx`, `audit`, `spark` — no hidden dependencies |
| **Test isolation** | `MagicMock` replaces Spark entirely — tests run in < 1 second with no cluster |
| **Coverage** | 1:1 test file per source module, 8 test files total |
