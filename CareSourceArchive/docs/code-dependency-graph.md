# CareSource Archive — Code Dependency Graph

## Current State

| Module | Depends On | Status | Tests |
|--------|-----------|--------|-------|
| src/exceptions.py | — | Complete | 14/14 passing |
| src/utils.py | exceptions.py | Complete | 20/20 passing |
| src/audit.py | exceptions.py | Complete | 48/48 passing |
| src/config.py | utils.py, exceptions.py | Complete | 19/19 passing |
| src/conditions.py | exceptions.py | Complete | 17/17 passing |
| src/rehydrator.py | utils.py, audit.py, exceptions.py | Complete | 15/15 passing |
| src/archiver.py | utils.py, audit.py, config.py, conditions.py | Complete | 22/22 passing |
| src/scanner.py | config.py, utils.py, exceptions.py | Complete | 23/23 passing |

## Notebooks & Jobs

| File | Depends On | Status |
|------|-----------|--------|
| notebooks/setup_config_tables.py | F13 | Code Complete |
| notebooks/generate_parameters.py | F3, F1 | Code Complete |
| notebooks/run_archive.py | F6, F2, F1 | Code Complete |
| notebooks/run_rehydrate.py | F7, F2, F1 | Code Complete |
| notebooks/run_scanner.py | F11, F3 | Code Complete |
| notebooks/manual/validate_config.py | F3 | Code Complete |
| notebooks/manual/validate_archives.py | F6, F3 | Code Complete |
| resources/setup_job.yml | — | Code Complete |
| resources/setup_job_serverless.yml | — | Code Complete |
| resources/archive_job.yml | — | Code Complete |
| resources/archive_job_serverless.yml | — | Code Complete |
| resources/scanner_job.yml | — | Code Complete |
| resources/scanner_job_serverless.yml | — | Code Complete |
| databricks.yml | — | Code Complete |

## Dependency Graph

```
F13 (exceptions) ✅ ──┬── F1 (utils) ✅ ──┬── F3 (config) ✅ ── F4 (conditions) ✅ ── F6+F5 (archiver) ✅
                      │                   │        │                                         │
                      │   F2 (audit) ✅ ──┤        └── F11 (scanner) ✅                     ├── F8 (notebooks) ✅
                      │                   │                    │                              │
                      │                   └── F7 (rehydrator) ✅── F9 (notebook) ✅          └── F5 (dry-run) ✅
                      │                                                                 
                      └── F14 (pyproject.toml) ✅              F12 (scanner notebook) ✅
                           F15 (setup notebook) ✅
```

## Legend
- ✅ Complete

## Total: 295 unit tests collected across 8 test files (294 passing, 1 pre-existing failure in test_scanner.py)
