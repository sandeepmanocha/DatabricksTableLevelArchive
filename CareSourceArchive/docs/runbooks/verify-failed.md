# VERIFY_FAILED runbook

An archive run halted with status `VERIFY_FAILED` because the archived row count
does not match a fresh count of the source under the same retention predicate.

This typically means the source table drifted between ARCHIVE and the RESUME_DELETE
phase — new rows were written, rows were back-dated, or the watermark column
changed semantics.

## Triage
1. Inspect the audit row:
   ```
   SELECT * FROM <audit_catalog>.<audit_schema>.archive_audit
   WHERE table_name = '<table>' AND year = <year>
   ORDER BY created_at DESC LIMIT 5;
   ```
2. Compare archived row count vs. fresh source count:
   ```
   SELECT COUNT(*) FROM delta.`<archive_path>/year_<year>`;
   SELECT COUNT(*) FROM <source_fq>
   WHERE YEAR(<watermark_column>) = <year>;
   ```
3. Decide: re-archive the delta, discard the archive, or escalate.

## Do NOT
- Do not simply retry the job — the code will refuse to APPEND on top of a
  VERIFY_FAILED row until the reconcile step below is done.

## Reconcile
(Operator-specific — populate per your recovery policy.)
