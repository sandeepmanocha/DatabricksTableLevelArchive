import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.exceptions import ArchiveConfigError, ArchiveOperationError
from src.recovery import (
    _fetch_audit_row_by_id,
    _has_newer_archived_and_deleted,
    delete_archived_slice,
    rollback_archived_slice,
)


def _table_config(**overrides):
    base = {
        "table_id": "healthcare.claims.member",
        "source_catalog": "src_cat",
        "source_schema": "src_sch",
        "source_table": "claims",
        "watermark_column": "claim_date",
        "archive_base_path": "abfss://stor/archive",
        "retention_years": 5,
        "delete_after_archive": True,
        "exclusion_conditions": json.dumps([]),
        "is_active": True,
    }
    base.update(overrides)
    return base


@pytest.fixture
def audit_tbl(mock_audit):
    mock_audit._archive_table.return_value = (
        "`test_catalog`.`audit`.`archive_audit_log`"
    )
    return mock_audit


class _FakeRow:
    def __init__(self, data):
        self._data = data

    def asDict(self):
        return dict(self._data)


class TestRollbackRun:
    def test_missing_target_row_raises_archive_config_error(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = []
        with pytest.raises(ArchiveConfigError, match="target_audit_id"):
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="missing-id",
                dry_run=True,
            )

    def test_g2_null_created_at_raises_target_missing_created_at(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = [
            _FakeRow(
                {
                    "audit_id": "aid-1",
                    "table_name": "healthcare.claims.member",
                    "year": 2020,
                    "created_at": None,
                    "status": "ARCHIVED",
                    "archive_delta_version": 5,
                },
            ),
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_missing_created_at"

    def test_g3_null_year_raises_target_missing_year(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": None,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_missing_year"

    def test_g3_wrong_year_raises_target_wrong_year(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2019,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_wrong_year"

    def test_g1_newer_archived_and_deleted_blocks(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [_FakeRow({"audit_id": "blocker-99"})],
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "rollback_after_source_delete_refused"

    def test_g4_null_version_raises_target_missing_version(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": None,
                    },
                ),
            ],
            [],
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_missing_version"

    @patch("src.recovery.get_delta_history_versions_strict")
    def test_g5_history_unreadable_raises_target_version_unavailable(
        self, mock_hist, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        inner = ArchiveOperationError(
            "inner",
            table="claims",
            year=2020,
            operation="get_delta_history_versions_strict",
            reason="archive_folder_missing",
        )
        mock_hist.side_effect = inner
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_version_unavailable"
        assert ei.value.__cause__ is inner

    @patch("src.recovery.get_delta_history_versions_strict")
    def test_g5_history_orphan_raises_target_version_unavailable(
        self, mock_hist, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        inner = ArchiveOperationError(
            "inner",
            table="claims",
            year=2020,
            operation="get_delta_history_versions_strict",
            reason="archive_folder_orphan",
        )
        mock_hist.side_effect = inner
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_version_unavailable"
        assert ei.value.__cause__ is inner

    @patch("src.recovery.get_delta_history_versions_strict")
    def test_g5_version_not_in_history_raises_target_version_vacuumed(
        self, mock_hist, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        mock_hist.return_value = [10, 9, 8]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_version_vacuumed"

    @patch("src.recovery.get_delta_history_versions_strict")
    def test_dry_run_emits_preview_no_audit_write(
        self, mock_hist, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        mock_hist.return_value = [6, 5, 4]
        out = rollback_archived_slice(
            mock_spark,
            audit_tbl,
            mock_run_context,
            _table_config(),
            year=2020,
            target_audit_id="aid-1",
            dry_run=True,
        )
        assert out == {
            "action": "WOULD_RECOVERY_ARCHIVE_ROLLED_BACK",
            "target_version": 5,
            "table": "healthcare.claims.member",
            "year": 2020,
            "dry_run": True,
        }
        audit_tbl.log_archive.assert_not_called()

    @patch("src.recovery.archive_row_count")
    @patch("src.recovery.get_archive_delta_version")
    @patch("src.recovery.get_delta_history_versions_strict")
    def test_live_success_writes_one_rollback_to_version_row_with_post_restore_version(
        self,
        mock_hist,
        mock_get_ver,
        mock_arc_count,
        mock_spark,
        mock_run_context,
        audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        mock_hist.return_value = [6, 5, 4]
        mock_get_ver.return_value = 7
        mock_arc_count.return_value = 1001
        out = rollback_archived_slice(
            mock_spark,
            audit_tbl,
            mock_run_context,
            _table_config(),
            year=2020,
            target_audit_id="aid-1",
            dry_run=False,
        )
        assert out["action"] == "RECOVERY_ARCHIVE_ROLLED_BACK"
        assert out["target_version"] == 5
        assert out["post_restore_version"] == 7
        assert out["dry_run"] is False
        restore_calls = [
            c[0][0]
            for c in mock_spark.sql.call_args_list
            if "RESTORE TABLE" in c[0][0]
        ]
        assert len(restore_calls) == 1
        assert "TO VERSION AS OF 5" in restore_calls[0]
        audit_tbl.log_archive.assert_called_once()
        kw = audit_tbl.log_archive.call_args.kwargs
        assert kw["status"] == "RECOVERY_ARCHIVE_ROLLED_BACK"
        assert kw["archive_delta_version"] == 7
        assert kw["needs_review"] is True
        assert kw["record_count"] == 1001

    @pytest.mark.parametrize(
        "bad_status",
        ["ARCHIVED_AND_DELETED", "RECOVERY_ARCHIVE_DELETED", "FAILED", "DRY_RUN", "SKIPPED_CONCURRENT"],
    )
    def test_target_with_non_rollbackable_status_raises(
        self, bad_status, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = [
            _FakeRow(
                {
                    "audit_id": "aid-1",
                    "table_name": "healthcare.claims.member",
                    "year": 2020,
                    "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                    "status": bad_status,
                    "archive_delta_version": 5,
                },
            ),
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_status_not_rollbackable"
        assert bad_status in str(ei.value)
        audit_tbl.log_archive.assert_not_called()

    def test_target_with_null_status_raises(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = [
            _FakeRow(
                {
                    "audit_id": "aid-1",
                    "table_name": "healthcare.claims.member",
                    "year": 2020,
                    "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                    "status": None,
                    "archive_delta_version": 5,
                },
            ),
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=True,
            )
        assert ei.value.reason == "target_status_not_rollbackable"

    @patch("src.recovery.archive_row_count")
    @patch("src.recovery.get_archive_delta_version")
    @patch("src.recovery.get_delta_history_versions_strict")
    def test_live_restore_then_post_version_none_raises(
        self,
        mock_hist,
        mock_get_ver,
        mock_arc_count,
        mock_spark,
        mock_run_context,
        audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        mock_hist.return_value = [6, 5, 4]
        mock_get_ver.return_value = None
        mock_arc_count.return_value = 0
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=False,
            )
        assert ei.value.reason == "target_version_unavailable"
        audit_tbl.log_archive.assert_not_called()

    @patch("src.recovery.get_delta_history_versions_strict")
    def test_live_restore_raw_spark_failure_wraps_as_operation_failure(
        self,
        mock_hist,
        mock_spark,
        mock_run_context,
        audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [
                _FakeRow(
                    {
                        "audit_id": "aid-1",
                        "table_name": "healthcare.claims.member",
                        "year": 2020,
                        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "status": "ARCHIVED",
                        "archive_delta_version": 5,
                    },
                ),
            ],
            [],
        ]
        mock_hist.return_value = [6, 5, 4]

        def _sql_dispatch(query):
            if "RESTORE TABLE" in query:
                raise RuntimeError("Py4J: storage outage")
            result = mock_spark.sql.return_value
            return result

        mock_spark.sql.side_effect = _sql_dispatch
        with pytest.raises(ArchiveOperationError) as ei:
            rollback_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                target_audit_id="aid-1",
                dry_run=False,
            )
        assert ei.value.reason == "operation_failure"
        audit_tbl.log_archive.assert_not_called()


class TestResetSlice:
    def test_live_without_reason_raises_reason_required(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = []
        with pytest.raises(ArchiveOperationError) as ei:
            delete_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                dry_run=False,
                reason=None,
            )
        assert ei.value.reason == "reason_required"

    def test_g1_existing_archived_and_deleted_blocks(
        self, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [
            [_FakeRow({"audit_id": "bad-deletion"})],
        ]
        with pytest.raises(ArchiveOperationError) as ei:
            delete_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                dry_run=True,
            )
        assert ei.value.reason == "delete_archived_after_source_delete_refused"

    @patch("src.recovery.archive_row_count")
    def test_archive_missing_raises_delete_archived_target_missing_no_audit_write(
        self, mock_arc, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [[]]
        mock_arc.side_effect = ArchiveOperationError(
            "x",
            table="claims",
            year=2020,
            operation="archive_row_count",
            reason="archive_folder_missing",
        )
        with pytest.raises(ArchiveOperationError) as ei:
            delete_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                dry_run=True,
            )
        assert ei.value.reason == "delete_archived_target_missing"
        audit_tbl.log_archive.assert_not_called()

    @patch("src.recovery.archive_row_count")
    def test_archive_orphan_raises_delete_archived_target_missing_no_audit_write(
        self, mock_arc, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [[]]
        mock_arc.side_effect = ArchiveOperationError(
            "x",
            table="claims",
            year=2020,
            operation="archive_row_count",
            reason="archive_folder_orphan",
        )
        with pytest.raises(ArchiveOperationError) as ei:
            delete_archived_slice(
                mock_spark,
                audit_tbl,
                mock_run_context,
                _table_config(),
                year=2020,
                dry_run=True,
            )
        assert ei.value.reason == "delete_archived_target_missing"
        audit_tbl.log_archive.assert_not_called()

    @patch("src.recovery.archive_row_count")
    def test_dry_run_emits_preview_no_audit_write(
        self, mock_arc, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [[]]
        mock_arc.return_value = 42
        out = delete_archived_slice(
            mock_spark,
            audit_tbl,
            mock_run_context,
            _table_config(),
            year=2020,
            dry_run=True,
        )
        assert out == {
            "action": "WOULD_RECOVERY_ARCHIVE_DELETED",
            "rows_to_delete": 42,
            "table": "healthcare.claims.member",
            "year": 2020,
            "dry_run": True,
        }
        audit_tbl.log_archive.assert_not_called()

    @patch("src.recovery.archive_row_count")
    def test_live_success_writes_one_reset_row_with_needs_review_true(
        self, mock_arc, mock_spark, mock_run_context, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.side_effect = [[]]
        mock_arc.return_value = 42
        out = delete_archived_slice(
            mock_spark,
            audit_tbl,
            mock_run_context,
            _table_config(),
            year=2020,
            dry_run=False,
            reason="  wipe bad slice  ",
        )
        assert out["action"] == "RECOVERY_ARCHIVE_DELETED"
        assert out["rows_deleted"] == 42
        assert out["reason"] == "wipe bad slice"
        del_calls = [
            c[0][0] for c in mock_spark.sql.call_args_list if "DELETE FROM delta." in c[0][0]
        ]
        assert len(del_calls) == 1
        assert "WHERE true" in del_calls[0]
        audit_tbl.log_archive.assert_called_once()
        kw = audit_tbl.log_archive.call_args.kwargs
        assert kw["status"] == "RECOVERY_ARCHIVE_DELETED"
        assert kw["record_count"] == 42
        assert kw["needs_review"] is True
        assert kw["archive_delta_version"] is None
        assert kw["error_message"] == "wipe bad slice"


class TestPrivateHelpers:
    def test_fetch_audit_row_by_id_returns_dict(
        self, mock_spark, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = [
            _FakeRow(
                {
                    "audit_id": "a",
                    "table_name": "t",
                    "year": 2020,
                    "created_at": datetime(2024, 1, 1),
                    "status": "ARCHIVED",
                    "archive_delta_version": 3,
                },
            ),
        ]
        d = _fetch_audit_row_by_id(mock_spark, audit_tbl, "a")
        assert d is not None
        assert d.get("audit_id") == "a"
        assert d.get("year") == 2020

    def test_fetch_audit_row_by_id_returns_none_when_no_rows(
        self, mock_spark, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = []
        assert _fetch_audit_row_by_id(mock_spark, audit_tbl, "nope") is None

    def test_has_newer_archived_and_deleted_with_cutoff(
        self, mock_spark, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = []
        cut = datetime(2024, 6, 1, 12, 30, 45)
        _has_newer_archived_and_deleted(
            mock_spark, audit_tbl, "healthcare.claims.member", 2020, cut,
        )
        sql = mock_spark.sql.call_args[0][0]
        assert "created_at > TIMESTAMP '2024-06-01 12:30:45'" in sql
        assert "ARCHIVED_AND_DELETED" in sql

    def test_has_newer_archived_and_deleted_without_cutoff(
        self, mock_spark, audit_tbl,
    ):
        mock_spark.sql.return_value.collect.return_value = []
        _has_newer_archived_and_deleted(
            mock_spark, audit_tbl, "healthcare.claims.member", 2020, None,
        )
        sql = mock_spark.sql.call_args[0][0]
        assert "created_at >" not in sql
        assert "ARCHIVED_AND_DELETED" in sql
