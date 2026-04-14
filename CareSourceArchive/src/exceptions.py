class ArchiveError(Exception):

    _DIAGNOSTIC_TEMPLATES = {
        "SKIPPED_CONCURRENT": {
            "concurrent_skip": (
                "{table} year {year}: Skipped — concurrent run {foreign_run_id} "
                "has STARTED ({age_hours:.1f}h ago, threshold {stale_threshold_hours}h). "
                "Re-run after that job completes."
            ),
        },
        "FAILED": {
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

    @staticmethod
    def diagnostic_message(status, reason, **kwargs):
        reasons = ArchiveError._DIAGNOSTIC_TEMPLATES.get(status, {})
        template = reasons.get(reason)
        if template:
            try:
                return template.format(**kwargs)
            except (KeyError, TypeError, ValueError):
                return f"{status} ({reason}): {kwargs}"
        return f"{status} ({reason}): {kwargs}"


class ArchiveConfigError(ArchiveError):
    def __init__(self, msg=None, *, field=None, table_id=None, source=None):
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
    def __init__(self, msg, *, table=None, year=None, operation=None, reason=None):
        super().__init__(msg)
        self.table = table
        self.year = year
        self.operation = operation
        self.reason = reason


class ArchiveVerificationError(ArchiveError):
    def __init__(self, msg, *, table=None, year=None, expected=None, actual=None, reason=None):
        super().__init__(msg)
        self.table = table
        self.year = year
        self.expected = expected
        self.actual = actual
        self.reason = reason
