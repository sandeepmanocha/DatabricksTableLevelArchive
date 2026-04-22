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
            "orphan_folder": (
                "{table} year {year}: Archive folder exists at {path} but no "
                "successful archive is recorded in the audit table. This may be "
                "from a failed prior write with partial data.\n"
                "1. Inspect the folder: SELECT COUNT(*) FROM delta.`{path}`\n"
                "2. If partial/corrupt: DELETE the folder from cloud storage\n"
                "3. Re-run the archive job"
            ),
            "count_mismatch": (
                "{table} year {year}: Archive count {actual} does not match "
                "expected {expected}. "
                "Inspect: SELECT COUNT(*) FROM delta.`{path}`"
            ),
            "operation_failure": (
                "{table} year {year}: {operation} failed — {error}"
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
    def __init__(self, msg, *, table=None, year=None, expected=None, actual=None, reason=None) -> None:
        """
        Description: Initialize a verification mismatch error with expected and actual values.
        Parameters: msg: error message; table: table name; year: archive year; expected: expected value; actual: actual value; reason: failure reason
        Return: None
        """
        super().__init__(msg)
        self.table = table
        self.year = year
        self.expected = expected
        self.actual = actual
        self.reason = reason
