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

    def test_orphan_folder_formats_correctly(self):
        msg = ArchiveError.diagnostic_message(
            "FAILED", "orphan_folder",
            table="claims", year=2020, path="/archive/claims/2020",
        )
        assert "claims" in msg
        assert "2020" in msg
        assert "/archive/claims/2020" in msg
        assert "no successful archive is recorded" in msg

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
            "ownership", "missing_folder_after_delete", "orphan_folder",
            "count_mismatch", "operation_failure", "cannot_determine_incremental_position",
        }


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
