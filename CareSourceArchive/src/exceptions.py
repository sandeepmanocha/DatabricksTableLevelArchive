"""
Exception types for the archive pipeline.

Defines a small hierarchy of errors raised by the scanner, archiver,
rehydrator, and audit layers, along with standardized diagnostic messages
that point operators at the right runbook or audit query when something
goes wrong.
"""


class ArchiveError(Exception):

    _DIAGNOSTIC_TEMPLATES = {
        "SKIPPED_CONCURRENT": {
            "concurrent_skip": (
                "{table} year {year}: Skipped — concurrent run {foreign_run_id} "
                "has STARTED ({age_hours:.1f}h ago, threshold {stale_threshold_hours}h). "
                "Re-run after that job completes."
            ),
        },
        "VERIFY_FAILED": {
            "source_drift": (
                "{table} year {year}: Archive count {archive_count} does not match "
                "fresh source count {source_count}. Run has halted to prevent data loss. "
                "See docs/runbooks/verify-failed.md."
            ),
        },
        "FAILED": {
            "cannot_determine_incremental_position": (
                "{table} year {year}: Cannot determine incremental append position — "
                "no watermark in audit and archive folder has no resolvable MAX watermark. "
                "Inspect archive delta and audit rows before retrying."
            ),
            "ownership": (
                "{table} year {year}: Delete blocked — this run ({archive_run_id}) "
                "does not own the ARCHIVED row. "
                "Check: SELECT archive_run_id, status FROM {audit_table} "
                "WHERE table_name = '{table}' AND year = {year} "
                "AND status = 'ARCHIVED' ORDER BY created_at DESC LIMIT 1"
            ),
            "missing_folder_after_delete": (
                "{table} year {year}: Archive folder missing at {path} but audit "
                "shows ARCHIVED_AND_DELETED — source rows already removed. "
                "Data may be lost.\n"
                "1. Check cloud storage recycle bin\n"
                "2. Check Delta time travel: DESCRIBE HISTORY delta.`{path}`\n"
                "3. Contact storage admin if unrecoverable"
            ),
            "archive_folder_missing": (
                "{table} year {year}: Archive path {path} is missing or empty — "
                "no Delta log at this location. Re-run the archive job after "
                "confirming storage and permissions, or run delete_archived_slice "
                "for this table/year to clear stale audit state if the slice was "
                "removed on purpose. See docs/runbooks/recovery.md."
            ),
            "archive_folder_orphan": (
                "{table} year {year}: Archive path {path} has data files but no "
                "valid Delta transaction log (orphan or corrupted folder). "
                "Use delete_archived_slice to wipe the orphan path, then re-run "
                "the archive job. See docs/runbooks/recovery.md."
            ),
            "not_eligible_for_delete": (
                "{table} year {year}: Not eligible for delete — {reason_code}. "
                "Last success status: {last_status}. "
                "See docs/runbooks/delete-source-after-archive.md."
            ),
            "concurrent_run_on_table": (
                "{table} year {year}: Skipped — concurrent run {foreign_run_id} "
                "has STARTED on year {foreign_year} ({age_hours:.1f}h ago, "
                "threshold {stale_threshold_hours}h). Re-run this delete job "
                "after that run completes."
            ),
            "count_mismatch": (
                "{table} year {year}: Source row count {actual} for the year+watermark+"
                "exclusion window does not match rows-this-run {expected} "
                "(archived_total minus committed_rows). May indicate a concurrent "
                "insert/delete on the source between the write and the verify, or "
                "(post 2026-04-27) a custom_sql exclusion change between runs. "
                "Inspect the source slice with the same WHERE used by the write; "
                "archive folder is at delta.`{path}`."
            ),
            "operation_failure": (
                "{table} year {year}: {operation} failed — {error}"
            ),
            "version_capture_failed": (
                "{table} year {year}: Archive write succeeded at {path} but the "
                "Delta version probe returned None. Run halted to prevent writing "
                "an audit row with NULL archive_delta_version (which would block "
                "rollback). Inspect storage access and Delta history at the path, "
                "then re-run. NULL archive_delta_version on a previously-written "
                "audit row can also indicate an ArchiveVerificationError raised "
                "before the version stamp — see docs/runbooks/recovery.md."
            ),
            "reason_required": (
                "{table} year {year}: Live delete_archived_slice requires a reason "
                "argument for audit traceability. Rerun with reason=\"...\"."
            ),
            "target_status_not_rollbackable": (
                "{table} year {year}: Target audit row {target_audit_id} has "
                "status {target_status}. Only ARCHIVED or "
                "RECOVERY_ARCHIVE_ROLLED_BACK rows are valid rollback targets — "
                "an ARCHIVED_AND_DELETED (main-flow source-delete), "
                "RECOVERY_ARCHIVE_DELETED (recovery wipe), FAILED, DRY_RUN, or "
                "other non-success row cannot be used as a target because "
                "restoring would either resurrect data that was intentionally "
                "deleted or jump to an inconsistent state. "
                "See docs/runbooks/recovery.md."
            ),
            "delete_archived_after_source_delete_refused": (
                "{table} year {year}: delete_archived_slice refused — a newer "
                "ARCHIVED_AND_DELETED audit row ({blocker_audit_id}) exists for "
                "this slice (main-flow source-delete already happened). Inspect "
                "the audit history before deleting the archive. "
                "See docs/runbooks/recovery.md."
            ),
            "delete_archived_target_missing": (
                "{table} year {year}: No archive slice found at {path} (missing "
                "or orphan). delete_archived_slice refuses to write a "
                "RECOVERY_ARCHIVE_DELETED audit row when there is nothing to "
                "wipe. If you intended to clear stale audit state only, inspect "
                "the audit table directly. See docs/runbooks/recovery.md."
            ),
            "rollback_after_source_delete_refused": (
                "{table} year {year}: rollback_archived_slice refused — a newer "
                "ARCHIVED_AND_DELETED audit row ({blocker_audit_id}) exists "
                "after target row {target_audit_id}. Rolling back would "
                "resurrect data that was intentionally deleted by the main-flow "
                "source-delete. See docs/runbooks/recovery.md."
            ),
            "target_missing_created_at": (
                "{table} year {year}: Target audit row {target_audit_id} has NULL "
                "created_at — we cannot compare timestamps against newer "
                "ARCHIVED_AND_DELETED rows safely. Inspect the audit row and fix "
                "created_at before retrying. See docs/runbooks/recovery.md."
            ),
            "target_missing_version": (
                "{table} year {year}: Cannot roll back — target audit row "
                "{target_audit_id} has NULL archive_delta_version. Either the "
                "row pre-dates the version-capture fix or it was written by a "
                "path that does not mutate the archive Delta (e.g. "
                "RESUME_DELETE, delete_source_after_archive). Use "
                "delete_archived_slice instead of rollback_archived_slice. See "
                "docs/runbooks/recovery.md."
            ),
            "target_missing_year": (
                "{table} year {year}: Target audit row {target_audit_id} is "
                "missing the year column (NULL). Cannot validate against the "
                "user-supplied year {year}. Inspect the audit row. "
                "See docs/runbooks/recovery.md."
            ),
            "target_version_unavailable": (
                "{table} year {year}: Cannot read Delta history at {path} to "
                "verify version {target_version} for audit id {target_audit_id}. "
                "The folder may be missing, orphan, or corrupted, or there may "
                "be an infra issue. Inspect storage and rerun after "
                "fixing access or path. See docs/runbooks/recovery.md."
            ),
            "target_version_vacuumed": (
                "{table} year {year}: Target version {target_version} for audit "
                "id {target_audit_id} is recorded in audit but is no longer "
                "present in Delta history at {path} (likely VACUUM or retention). "
                "Cannot roll back. Consider delete_archived_slice and re-archive. "
                "See docs/runbooks/recovery.md."
            ),
            "target_wrong_year": (
                "{table} year {year}: User-supplied year {year} does not match "
                "target audit row year {target_year} (audit id "
                "{target_audit_id}). Recovery aborted to prevent wiping the wrong "
                "slice. See docs/runbooks/recovery.md."
            ),
        },
    }

    _NOT_FOUND_FRAGMENTS = (
        "java.io.FileNotFoundException",
        "FileNotFoundException",
        "No such file or directory",
        "PATH_NOT_FOUND",
        "does not exist",
        "cannot be found",
    )

    _PERMISSION_FRAGMENTS = (
        "PERMISSION_DENIED",
        "ACCESS_DENIED",
        "permission denied",
        "403",
    )

    @staticmethod
    def is_not_found(exc: Exception) -> bool:
        """True when *exc* signals a missing path rather than an access or infra error.

        Databricks ``dbutils.fs`` wraps Java exceptions in a generic Python
        ``Exception``.  There is no typed ``FileNotFoundError``; the only
        reliable signal is the message text.  We check against known fragments
        that appear across classic and serverless runtimes.
        """
        msg = str(exc)
        if any(f in msg for f in ArchiveError._PERMISSION_FRAGMENTS):
            return False
        return any(f in msg for f in ArchiveError._NOT_FOUND_FRAGMENTS)

    @staticmethod
    def diagnostic_message(status, reason, **kwargs) -> str:
        """
        Description: Build a formatted diagnostic string for a status and reason.
        Parameters: status: archive status key; reason: diagnostic reason key; kwargs: template format values
        Return: Formatted message string, or a fallback when no template matches.
        """
        reasons = ArchiveError._DIAGNOSTIC_TEMPLATES.get(status, {})
        template = reasons.get(reason)
        if template:
            try:
                return template.format(**kwargs)
            except (KeyError, TypeError, ValueError):
                return f"{status} ({reason}): {kwargs}"
        return f"{status} ({reason}): {kwargs}"


class ArchiveConfigError(ArchiveError):
    def __init__(self, msg=None, *, field=None, table_id=None, source=None) -> None:
        """
        Description: Initialize a configuration error with a custom or built message.
        Parameters: msg: optional full message; field: config field name; table_id: table identifier; source: config source label
        Return: None
        """
        if msg is not None:
            super().__init__(msg)
            return
        parts = [f"Config error: field='{field}'"]
        if table_id:
            parts.append(f"table_id='{table_id}'")
        if source:
            parts.append(f"source='{source}'")
        super().__init__(", ".join(parts))


class ArchiveOperationError(ArchiveError):
    def __init__(self, msg, *, table=None, year=None, operation=None, reason=None) -> None:
        """
        Description: Initialize an archive operation failure with context attributes.
        Parameters: msg: error message; table: table name; year: archive year; operation: operation name; reason: failure reason
        Return: None
        """
        super().__init__(msg)
        self.table = table
        self.year = year
        self.operation = operation
        self.reason = reason


class ArchiveVerificationError(ArchiveError):
    def __init__(self, msg, *, table=None, year=None, expected=None, actual=None, reason=None, already_logged=False) -> None:
        """
        Description: Initialize a verification mismatch error with expected and actual values.
        Parameters: msg: error message; table: table name; year: archive year; expected: expected value; actual: actual value; reason: failure reason; already_logged: True when a VERIFY_FAILED audit row has already been written and the outer handler must not double-log FAILED
        Return: None
        """
        super().__init__(msg)
        self.table = table
        self.year = year
        self.expected = expected
        self.actual = actual
        self.reason = reason
        self.already_logged = already_logged
