"""Recovery tooling — operator-initiated rollback_archived_slice and delete_archived_slice per archived slice.

Follows the project exception hierarchy defined in src/exceptions.py: input
validation failures raise ArchiveConfigError, recovery gate and Spark
operation failures raise ArchiveOperationError. Spark SQL calls are wrapped
so that AnalysisException / Py4JJavaError from RESTORE or DELETE do not
escape raw; all failures carry a stable ``reason`` for audit and runbook
cross-reference. Uses ``audit._archive_table()`` as the sole coupling point
to AuditLogger internals — expose a public accessor there if this contract
needs to widen.

Audit ``created_at`` timestamps are assumed UTC; notebook operators should
run recovery in a UTC-configured session. TZ-aware datetimes are normalized
to UTC-naive before being emitted as ``TIMESTAMP '...'`` literals.
"""

import datetime as datetime_module
import logging
from typing import Any, Mapping, Optional

from src.exceptions import ArchiveConfigError, ArchiveError, ArchiveOperationError
from src.utils import (
    archive_path_from_config,
    archive_row_count,
    get_archive_delta_version,
    get_delta_history_versions_strict,
    row_to_dict,
    source_fq_from_config,
    sql_int,
    sql_quote,
)

logger = logging.getLogger("caresource_archive.recovery")

_ROLLBACK_TARGET_ALLOWED_STATUSES = frozenset({"ARCHIVED", "RECOVERY_ARCHIVE_ROLLED_BACK"})


def _fetch_audit_row_by_id(spark, audit, target_audit_id: str) -> dict | None:
    """Load one archive_audit_log row by id.

    Uses ``audit._archive_table()`` for the FQ table name (tight coupling to
    ``AuditLogger`` internal layout; keep in sync with ``src/audit.py``).
    """
    tbl = audit._archive_table()
    sql = (
        f"SELECT audit_id, table_name, year, created_at, status, archive_delta_version "
        f"FROM {tbl} "
        f"WHERE audit_id = {sql_quote(target_audit_id)} LIMIT 1"
    )
    rows = spark.sql(sql).collect()
    if not rows:
        return None
    return row_to_dict(rows[0])


def _has_newer_archived_and_deleted(
    spark,
    audit,
    table: str,
    year: int,
    after_created_at: Optional[datetime_module.datetime],
) -> Optional[str]:
    tbl = audit._archive_table()
    parts = [
        f"table_name = {sql_quote(table)}",
        f"year = {int(year)}",
        "status = 'ARCHIVED_AND_DELETED'",
    ]
    if after_created_at is not None:
        if not isinstance(after_created_at, datetime_module.datetime):
            raise ArchiveConfigError(
                msg="after_created_at must be a datetime or None for blocker query",
            )
        ts = after_created_at
        if ts.tzinfo is not None:
            ts = ts.astimezone(datetime_module.timezone.utc).replace(tzinfo=None)
        lit = ts.isoformat(sep=" ", timespec="seconds")
        parts.append(f"created_at > TIMESTAMP '{lit}'")
    pred = " AND ".join(parts)
    sql = f"SELECT audit_id FROM {tbl} WHERE {pred} ORDER BY created_at DESC LIMIT 1"
    rows = spark.sql(sql).collect()
    if not rows:
        return None
    rowd = row_to_dict(rows[0])
    aid = rowd.get("audit_id")
    if aid is None:
        raise ArchiveConfigError(
            msg="ARCHIVED_AND_DELETED blocker query returned a row without audit_id",
        )
    return str(aid)


def rollback_archived_slice(
    spark,
    audit,
    ctx,
    table_config: Mapping[str, Any],
    *,
    year: int,
    target_audit_id: str,
    dry_run: bool = True,
) -> dict:
    """Restore an archive slice Delta table to the version recorded on a target audit row.

    Raises:
        ArchiveConfigError: When the target audit row is missing or required
            columns are absent (input validity — not a recovery gate).
        ArchiveOperationError: When a recovery gate blocks the operation.
    """
    _ = ctx
    tid = table_config.get("table_id", source_fq_from_config(table_config))
    base_path = table_config.get("archive_base_path")
    if base_path is None:
        raise ArchiveConfigError(msg="table_config missing archive_base_path")
    source_table = table_config.get("source_table")
    if source_table is None:
        raise ArchiveConfigError(msg="table_config missing source_table")

    target = _fetch_audit_row_by_id(spark, audit, target_audit_id)
    if target is None:
        raise ArchiveConfigError(
            msg=f"No archive_audit_log row found for target_audit_id={target_audit_id!r}",
        )
    if target.get("audit_id") is None:
        raise ArchiveConfigError(msg="target audit row missing audit_id")
    if target.get("audit_id") != target_audit_id:
        raise ArchiveConfigError(
            msg=f"target row audit_id mismatch: expected {target_audit_id!r}",
        )
    row_table = target.get("table_name")
    if row_table is None:
        raise ArchiveConfigError(msg="target audit row missing table_name")
    if row_table != tid:
        raise ArchiveConfigError(
            msg=f"target audit row is for table {row_table!r}, expected {tid!r}",
        )

    target_status = target.get("status")
    if target_status not in _ROLLBACK_TARGET_ALLOWED_STATUSES:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "target_status_not_rollbackable",
            table=tid,
            year=year,
            target_audit_id=target_audit_id,
            target_status=target_status if target_status is not None else "NULL",
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="target_status_not_rollbackable",
        )

    if target.get("created_at") is None:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "target_missing_created_at",
            table=tid,
            year=year,
            target_audit_id=target_audit_id,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="target_missing_created_at",
        )

    ty = target.get("year")
    if ty is None:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "target_missing_year",
            table=tid,
            year=year,
            target_audit_id=target_audit_id,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="target_missing_year",
        )
    try:
        target_year_int = int(ty)
    except (TypeError, ValueError) as exc:
        raise ArchiveConfigError(
            msg=f"target audit row has non-integer year: {ty!r}",
        ) from exc
    if target_year_int != int(year):
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "target_wrong_year",
            table=tid,
            year=year,
            target_year=ty,
            target_audit_id=target_audit_id,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="target_wrong_year",
        )

    created_at = target.get("created_at")
    if created_at is None:
        raise ArchiveConfigError(
            msg="target audit row lost created_at between NULL gate and blocker query",
        )
    blocker = _has_newer_archived_and_deleted(
        spark, audit, tid, int(year), created_at,
    )
    if blocker:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "rollback_after_source_delete_refused",
            table=tid,
            year=year,
            blocker_audit_id=blocker,
            target_audit_id=target_audit_id,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="rollback_after_source_delete_refused",
        )

    raw_ver = target.get("archive_delta_version")
    if raw_ver is None:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "target_missing_version",
            table=tid,
            year=year,
            target_audit_id=target_audit_id,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="target_missing_version",
        )
    try:
        target_version = int(raw_ver)
    except (TypeError, ValueError) as exc:
        raise ArchiveConfigError(
            msg=f"target archive_delta_version is not an integer: {raw_ver!r}",
        ) from exc

    path = archive_path_from_config(table_config, year)
    try:
        versions = get_delta_history_versions_strict(spark, base_path, source_table, year)
    except ArchiveOperationError as exc:
        if exc.reason in ("archive_folder_missing", "archive_folder_orphan"):
            msg = ArchiveError.diagnostic_message(
                "FAILED",
                "target_version_unavailable",
                table=tid,
                year=year,
                target_audit_id=target_audit_id,
                target_version=target_version,
                path=path,
            )
            raise ArchiveOperationError(
                msg,
                table=tid,
                year=year,
                operation="rollback_archived_slice",
                reason="target_version_unavailable",
            ) from exc
        raise

    if target_version not in versions:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "target_version_vacuumed",
            table=tid,
            year=year,
            target_audit_id=target_audit_id,
            target_version=target_version,
            path=path,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="target_version_vacuumed",
        )

    if dry_run:
        return {
            "action": "WOULD_RECOVERY_ARCHIVE_ROLLED_BACK",
            "target_version": target_version,
            "table": tid,
            "year": int(year),
            "dry_run": True,
        }

    try:
        spark.sql(
            f"RESTORE TABLE delta.`{path}` TO VERSION AS OF {sql_int(target_version)}",
        )
    except ArchiveError:
        raise
    except Exception as exc:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "operation_failure",
            table=tid,
            year=year,
            operation="RESTORE TABLE",
            error=str(exc),
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="operation_failure",
        ) from exc

    post_restore_version = get_archive_delta_version(
        spark, base_path, source_table, year,
    )
    if post_restore_version is None:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "target_version_unavailable",
            table=tid,
            year=year,
            target_audit_id=target_audit_id,
            target_version=target_version,
            path=path,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="rollback_archived_slice",
            reason="target_version_unavailable",
        )
    restored_count = archive_row_count(spark, base_path, source_table, year)
    audit.log_archive(
        table=tid,
        year=int(year),
        status="RECOVERY_ARCHIVE_ROLLED_BACK",
        record_count=int(restored_count),
        archive_delta_version=int(post_restore_version),
        needs_review=True,
        error_message=None,
    )
    return {
        "action": "RECOVERY_ARCHIVE_ROLLED_BACK",
        "target_version": target_version,
        "post_restore_version": int(post_restore_version),
        "table": tid,
        "year": int(year),
        "dry_run": False,
    }


def delete_archived_slice(
    spark,
    audit,
    ctx,
    table_config: Mapping[str, Any],
    *,
    year: int,
    dry_run: bool = True,
    reason: str | None = None,
) -> dict:
    """Delete all rows in an archive slice Delta table and log RECOVERY_ARCHIVE_DELETED (live) or preview (dry-run).

    Raises:
        ArchiveOperationError: When a gate blocks the delete or the archive
            path is missing/orphan (translated to
            ``delete_archived_target_missing``).
        ArchiveConfigError: When ``table_config`` lacks required keys.
    """
    _ = ctx
    tid = table_config.get("table_id", source_fq_from_config(table_config))
    base_path = table_config.get("archive_base_path")
    if base_path is None:
        raise ArchiveConfigError(msg="table_config missing archive_base_path")
    source_table = table_config.get("source_table")
    if source_table is None:
        raise ArchiveConfigError(msg="table_config missing source_table")

    if not dry_run:
        if reason is None or not str(reason).strip():
            msg = ArchiveError.diagnostic_message(
                "FAILED",
                "reason_required",
                table=tid,
                year=year,
            )
            raise ArchiveOperationError(
                msg,
                table=tid,
                year=year,
                operation="delete_archived_slice",
                reason="reason_required",
            )

    path = archive_path_from_config(table_config, year)
    blocker = _has_newer_archived_and_deleted(spark, audit, tid, int(year), None)
    if blocker:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "delete_archived_after_source_delete_refused",
            table=tid,
            year=year,
            blocker_audit_id=blocker,
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="delete_archived_slice",
            reason="delete_archived_after_source_delete_refused",
        )

    try:
        row_n = archive_row_count(spark, base_path, source_table, year)
    except ArchiveOperationError as exc:
        if exc.reason in ("archive_folder_missing", "archive_folder_orphan"):
            msg = ArchiveError.diagnostic_message(
                "FAILED",
                "delete_archived_target_missing",
                table=tid,
                year=year,
                path=path,
            )
            raise ArchiveOperationError(
                msg,
                table=tid,
                year=year,
                operation="delete_archived_slice",
                reason="delete_archived_target_missing",
            ) from exc
        raise

    if dry_run:
        return {
            "action": "WOULD_RECOVERY_ARCHIVE_DELETED",
            "rows_to_delete": int(row_n),
            "table": tid,
            "year": int(year),
            "dry_run": True,
        }

    try:
        spark.sql(f"DELETE FROM delta.`{path}` WHERE true")
    except ArchiveError:
        raise
    except Exception as exc:
        msg = ArchiveError.diagnostic_message(
            "FAILED",
            "operation_failure",
            table=tid,
            year=year,
            operation="DELETE FROM delta",
            error=str(exc),
        )
        raise ArchiveOperationError(
            msg,
            table=tid,
            year=year,
            operation="delete_archived_slice",
            reason="operation_failure",
        ) from exc

    reason_text = str(reason).strip()
    audit.log_archive(
        table=tid,
        year=int(year),
        status="RECOVERY_ARCHIVE_DELETED",
        record_count=int(row_n),
        error_message=reason_text,
        needs_review=True,
        archive_delta_version=None,
    )
    return {
        "action": "RECOVERY_ARCHIVE_DELETED",
        "rows_deleted": int(row_n),
        "table": tid,
        "year": int(year),
        "dry_run": False,
        "reason": reason_text,
    }
