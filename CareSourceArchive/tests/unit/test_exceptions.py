import pytest

from src.exceptions import (
    ArchiveConfigError,
    ArchiveError,
    ArchiveOperationError,
    ArchiveVerificationError,
)

_CONCRETE_EXCEPTIONS = (
    ArchiveConfigError,
    ArchiveOperationError,
    ArchiveVerificationError,
)


class TestExceptionHierarchy:
    """ERR-01: Custom exception hierarchy with common base."""

    @pytest.mark.parametrize("exc_class", _CONCRETE_EXCEPTIONS)
    def test_archive_error_is_base(self, exc_class):
        assert issubclass(exc_class, ArchiveError)

    def test_all_are_exceptions(self):
        assert issubclass(ArchiveError, Exception)

    def test_concrete_types_are_distinct(self):
        assert not issubclass(ArchiveConfigError, ArchiveOperationError)
        assert not issubclass(ArchiveOperationError, ArchiveVerificationError)
        assert not issubclass(ArchiveConfigError, ArchiveVerificationError)

    @pytest.mark.parametrize("exc_class", _CONCRETE_EXCEPTIONS)
    def test_catch_base_catches_all(self, exc_class):
        with pytest.raises(ArchiveError):
            raise exc_class("bad config")

    @pytest.mark.parametrize("exc_class", _CONCRETE_EXCEPTIONS)
    def test_plain_string_message(self, exc_class):
        err = exc_class("boom")
        assert "boom" in str(err)


class TestArchiveConfigError:
    """F13.2 + ERR-04: Config validation errors with structured messages."""

    def test_message_includes_field_name(self):
        err = ArchiveConfigError(
            field="watermark_column",
            table_id="claims_member",
        )
        msg = str(err)
        assert "watermark_column" in msg
        assert "claims_member" in msg

    def test_message_includes_source(self):
        err = ArchiveConfigError(
            field="retention_years",
            table_id="claims_member",
            source="table_configs",
        )
        msg = str(err)
        assert "retention_years" in msg
        assert "table_configs" in msg

    def test_raises_with_kwargs(self):
        with pytest.raises(ArchiveConfigError) as exc_info:
            raise ArchiveConfigError(
                field="source_catalog", table_id="t1"
            )
        assert "source_catalog" in str(exc_info.value)


class TestArchiveOperationError:
    """F13.3 + ERR-04: Runtime failure errors with structured messages."""

    def test_message_includes_table_year_operation(self):
        err = ArchiveOperationError(
            "health.claims.member year 2020: archive_write failed — Delta write failed: path not accessible",
            table="health.claims.member",
            year=2020,
            operation="archive_write",
            reason="Delta write failed: path not accessible",
        )
        msg = str(err)
        assert "health.claims.member" in msg
        assert "2020" in msg
        assert "archive_write" in msg
        assert "Delta write failed" in msg

    def test_wraps_root_cause(self):
        root = ValueError("bad value")
        err = ArchiveOperationError(
            "health.claims.member year 2020: custom_sql failed — bad value",
            table="health.claims.member",
            year=2020,
            operation="custom_sql",
            reason=str(root),
        )
        msg = str(err)
        assert "bad value" in msg
        assert "custom_sql" in msg

    def test_stores_attributes(self):
        err = ArchiveOperationError(
            "test msg",
            table="t1",
            year=2021,
            operation="delete",
            reason="ownership",
        )
        assert err.table == "t1"
        assert err.year == 2021
        assert err.operation == "delete"
        assert err.reason == "ownership"


class TestArchiveVerificationError:
    """F13.4 + ERR-04: Count mismatch and concurrency detection errors."""

    def test_message_includes_expected_vs_actual(self):
        err = ArchiveVerificationError(
            "health.claims.member year 2020: Archive count 49998 does not match expected 50000. Inspect: SELECT COUNT(*) FROM delta.`/path`",
            table="health.claims.member",
            year=2020,
            expected=50000,
            actual=49998,
        )
        msg = str(err)
        assert "health.claims.member" in msg
        assert "2020" in msg
        assert "50000" in msg
        assert "49998" in msg

    def test_concurrent_run_detection(self):
        err = ArchiveVerificationError(
            "concurrent run detected with different archive_run_id",
            table="health.claims.member",
            year=2020,
            expected=50000,
            actual=50000,
            reason="concurrent run detected with different archive_run_id",
        )
        msg = str(err)
        assert "concurrent" in msg

    def test_stores_attributes(self):
        err = ArchiveVerificationError(
            "test msg",
            table="t1",
            year=2021,
            expected=100,
            actual=99,
            reason="count_mismatch",
        )
        assert err.table == "t1"
        assert err.year == 2021
        assert err.expected == 100
        assert err.actual == 99
        assert err.reason == "count_mismatch"


class TestDiagnosticMessage:
    """Diagnostic message template system on ArchiveError base class."""

    def test_concurrent_skip_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "SKIPPED_CONCURRENT", "concurrent_skip",
            table="claims", year=2020, foreign_run_id="run-x",
            age_hours=2.5, stale_threshold_hours=4,
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "run-x" in msg
        assert "2.5h" in msg
        assert "4h" in msg

    def test_ownership_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "ownership",
            table="claims", year=2020,
            archive_run_id="run-a", audit_table="archive_audit_log",
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "run-a" in msg
        assert "archive_audit_log" in msg
        assert "Delete blocked" in msg

    def test_missing_folder_after_delete_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "missing_folder_after_delete",
            table="claims", year=2020, path="/archive/claims/2020",
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "/archive/claims/2020" in msg
        assert "Data may be lost" in msg

    def test_archive_folder_missing_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "archive_folder_missing",
            table="claims", year=2020, path="/archive/claims/2020",
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "/archive/claims/2020" in msg
        assert "delete_archived_slice" in msg.lower()
        assert "docs/runbooks/recovery.md" in msg

    def test_archive_folder_orphan_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "archive_folder_orphan",
            table="claims", year=2020, path="/archive/claims/2020",
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "/archive/claims/2020" in msg
        assert "delta" in msg.lower()
        assert "delete_archived_slice" in msg.lower()
        assert "docs/runbooks/recovery.md" in msg
        assert "missing or empty" not in msg.lower()

    def test_count_mismatch_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "count_mismatch",
            table="claims", year=2020, expected=100, actual=99,
            path="/archive/claims/2020",
        )
        assert "claims" in msg
        assert "100" in msg
        assert "99" in msg
        assert "/archive/claims/2020" in msg

    def test_operation_failure_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "operation_failure",
            table="claims", year=2020, operation="archive", error="disk full",
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "archive" in msg
        assert "disk full" in msg

    def test_target_missing_created_at_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "target_missing_created_at",
            table="c.s.t", year=2020, target_audit_id="abc",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "abc" in msg
        assert "created_at" in msg
        assert "ARCHIVED_AND_DELETED" in msg
        assert "docs/runbooks/recovery.md" in msg

    def test_target_missing_year_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "target_missing_year",
            table="c.s.t", year=2020, target_audit_id="abc",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "abc" in msg
        assert "year" in msg.lower()
        assert "docs/runbooks/recovery.md" in msg

    def test_target_wrong_year_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "target_wrong_year",
            table="c.s.t", year=2020, target_audit_id="abc", target_year=2019,
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "abc" in msg
        assert "2019" in msg
        assert "docs/runbooks/recovery.md" in msg

    def test_target_missing_version_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "target_missing_version",
            table="c.s.t", year=2020, target_audit_id="abc",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "abc" in msg
        assert "archive_delta_version" in msg
        assert "delete_archived_slice" in msg.lower()
        assert "docs/runbooks/recovery.md" in msg

    def test_target_version_vacuumed_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "target_version_vacuumed",
            table="c.s.t", year=2020, target_audit_id="abc",
            target_version=42, path="/p/a/t",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "abc" in msg
        assert "42" in msg
        assert "/p/a/t" in msg
        assert "docs/runbooks/recovery.md" in msg

    def test_target_version_unavailable_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "target_version_unavailable",
            table="c.s.t", year=2020, target_audit_id="abc",
            target_version=7, path="/x/y",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "abc" in msg
        assert "7" in msg
        assert "/x/y" in msg
        assert "docs/runbooks/recovery.md" in msg

    def test_delete_archived_target_missing_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "delete_archived_target_missing",
            table="c.s.t", year=2020, path="/z",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "/z" in msg
        assert "delete_archived_slice" in msg.lower()
        assert "docs/runbooks/recovery.md" in msg

    def test_rollback_after_source_delete_refused_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "rollback_after_source_delete_refused",
            table="c.s.t", year=2020,
            target_audit_id="t1", blocker_audit_id="b9",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "t1" in msg
        assert "b9" in msg
        assert "ARCHIVED_AND_DELETED" in msg
        assert "docs/runbooks/recovery.md" in msg

    def test_delete_archived_after_source_delete_refused_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "delete_archived_after_source_delete_refused",
            table="c.s.t", year=2020, blocker_audit_id="b9",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "b9" in msg
        assert "ARCHIVED_AND_DELETED" in msg
        assert "docs/runbooks/recovery.md" in msg

    def test_reason_required_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "reason_required",
            table="c.s.t", year=2020,
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "reason" in msg.lower()
        assert "delete_archived_slice" in msg.lower()

    def test_target_status_not_rollbackable_renders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "target_status_not_rollbackable",
            table="c.s.t", year=2020,
            target_audit_id="abc", target_status="ARCHIVED_AND_DELETED",
        )
        assert "c.s.t" in msg
        assert "2020" in msg
        assert "abc" in msg
        assert "ARCHIVED_AND_DELETED" in msg
        assert "ARCHIVED" in msg
        assert "RECOVERY_ARCHIVE_ROLLED_BACK" in msg
        assert "docs/runbooks/recovery.md" in msg

    def test_unknown_status_returns_fallback(self):
        msg = ArchiveError.diagnostic_message(
            "UNKNOWN_STATUS", "some_reason", table="t1", year=2020,
        )
        assert "UNKNOWN_STATUS" in msg
        assert "some_reason" in msg
        assert "t1" in msg

    def test_known_status_unknown_reason_returns_fallback(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "unknown_reason", table="t1", year=2020,
        )
        assert "FAILED" in msg
        assert "unknown_reason" in msg

    def test_missing_placeholder_returns_fallback(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "ownership",
            table="claims", year=2020,
        )
        assert "FAILED" in msg
        assert "ownership" in msg

    def test_type_error_in_format_returns_fallback(self):
        msg = ArchiveError.diagnostic_message(
            "SKIPPED_CONCURRENT", "concurrent_skip",
            table="claims", year=2020, foreign_run_id="run-x",
            age_hours=None, stale_threshold_hours=4,
        )
        assert "SKIPPED_CONCURRENT" in msg
        assert "concurrent_skip" in msg

    def test_template_keys_match_expected_set(self):
        templates = ArchiveError._DIAGNOSTIC_TEMPLATES
        assert set(templates.keys()) == {"SKIPPED_CONCURRENT", "FAILED", "VERIFY_FAILED"}
        assert set(templates["SKIPPED_CONCURRENT"].keys()) == {"concurrent_skip"}
        assert set(templates["VERIFY_FAILED"].keys()) == {"source_drift"}
        assert set(templates["FAILED"].keys()) == {
            "archive_folder_missing",
            "archive_folder_orphan",
            "cannot_determine_incremental_position",
            "concurrent_run_on_table",
            "count_mismatch",
            "missing_folder_after_delete",
            "not_eligible_for_delete",
            "operation_failure",
            "ownership",
            "version_capture_failed",
            "reason_required",
            "delete_archived_after_source_delete_refused",
            "delete_archived_target_missing",
            "rollback_after_source_delete_refused",
            "target_missing_created_at",
            "target_missing_version",
            "target_missing_year",
            "target_version_unavailable",
            "target_version_vacuumed",
            "target_status_not_rollbackable",
            "target_wrong_year",
        }

    def test_not_eligible_for_delete_renders_all_placeholders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "not_eligible_for_delete",
            table="claims",
            year=2020,
            reason_code="verify_failed_present",
            last_status="VERIFY_FAILED",
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "verify_failed_present" in msg
        assert "VERIFY_FAILED" in msg
        assert "docs/runbooks/delete-source-after-archive.md" in msg

    def test_concurrent_run_on_table_renders_all_placeholders(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "concurrent_run_on_table",
            table="claims",
            year=2020,
            foreign_run_id="run-x-123",
            foreign_year=2019,
            age_hours=1.5,
            stale_threshold_hours=4,
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "run-x-123" in msg
        assert "2019" in msg
        assert "1.5" in msg
        assert "4" in msg


class TestIsNotFound:
    """ArchiveError.is_not_found classifies dbutils.fs exceptions by message text."""

    @pytest.mark.parametrize("msg", [
        "java.io.FileNotFoundException: /Volumes/cat/sch/vol/path",
        "FileNotFoundException: some path",
        "No such file or directory /Volumes/cat/sch/vol",
        "PATH_NOT_FOUND: /some/path",
        "/Volumes/cat/sch/vol does not exist",
        "[SCHEMA_NOT_FOUND] The schema `cat`.`sch` cannot be found.",
    ])
    def test_returns_true_for_not_found(self, msg):
        assert ArchiveError.is_not_found(Exception(msg)) is True

    @pytest.mark.parametrize("msg", [
        "PERMISSION_DENIED: User does not have READ VOLUME",
        "INTERNAL: connection refused",
        "some random error",
        "",
    ])
    def test_returns_false_for_other_errors(self, msg):
        assert ArchiveError.is_not_found(Exception(msg)) is False
