# 16 — Validate Archives

**Goal:** Use the `validate_archives` notebook to check all archive folders exist for eligible years.

**Depends on:** 05_archive_live_create
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/16R_validate_archives_results.md` (below the H1 title). Do not overwrite previous runs.

## Execution Rules

> **DO NOT FIX CODE.** If a step fails or produces unexpected results, do **not** modify source code, notebooks, or SQL logic to make it pass. Instead:
>
> 1. **Record** the exact error, unexpected output, or deviation from expected behavior in the results file.
> 2. **Log** the issue with enough detail for a developer to reproduce (command run, actual vs expected output, full error messages).
> 3. **Continue** with remaining steps if possible (unless a failure makes subsequent steps meaningless).
> 4. **Summarize** at the end of the results file under a `## What Happened` section — plain-English description of everything that occurred.
> 5. **Recommend next steps** under a `## Next Steps` section — what the developer should investigate or fix, which test to re-run after the fix, and any manual actions needed.
> 6. Mark each step as **PASS**, **FAIL**, or **SKIP** (skipped due to prior failure) in the results.
> 7. Add a **TL;DR** (max 3 lines) right below the run header summarizing what happened and the outcome.
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Check:
>    - **Audit log:** All active tables must have `ARCHIVED` entries for their eligible years (from test 05). The validator cross-references audit entries against volume folders.
>    - **Archive volume:** Year folders should exist for all ARCHIVED entries. If any folders were deleted by prior tests (e.g. test 09 delete-after, test 12 orphan test), the validator will report them as missing — note which ones.
>    - **table_configs:** All test tables (`claims`, `members`, `providers`) should have correct `archive_base_path` pointing to the volume. If `archive_base_path` was broken by test 11, restore it first.
>    - **Notebook:** Verify `notebooks/manual/validate_archives.py` exists in the workspace.

---

## Steps

### 1. Run validate_archives notebook

**Manual step** — open `notebooks/manual/validate_archives.py` in the workspace and run it with:

- `config_table` = `sandeep_manocha.caresource_audit.global_settings`

### 2. Check results

**Expect (all archives present):**
- `valid = True`
- `missing = []`

### 3. Test with a missing folder

**Manual step** — delete one archive folder:

```python
dbutils.fs.rm("/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples/claims/year_2020", recurse=True)
```

Re-run the notebook.

**Expect:**
- `valid = False`
- `missing` list contains the claims year=2020 entry with path
