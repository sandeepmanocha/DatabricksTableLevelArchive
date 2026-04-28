"""
Standalone delete-job entry point for source rows whose year has already
been archived by a prior run. Sibling of `ArchiveEngine`: shares SQL
primitives via `ArchiveBase` but uses a different concurrency model and
report shape.

Subclasses-as-extension: `DeleteJob` calls into `ArchiveBase`'s
`_`-prefixed helpers (`_prepare_run`, `_calculate_eligible_years`,
`_source_year_count`, `_run_watermark_window`, `_delete_archived`).
Treat those names as a protected API; renaming or signature changes
need to update both subclasses (`ArchiveEngine` and `DeleteJob`).
"""

from typing import Any, Mapping, Optional

from src.archiver import ArchiveBase
from src.exceptions import ArchiveError
from src.utils import archive_row_count


class DeleteJob(ArchiveBase):
    """
    Description: Standalone delete job for source rows whose year has already been archived. Use when archive and delete run as separate jobs.
    Parameters: ctx: run context; audit: audit interface; spark: Spark session
    Return: None
    """

    def run(
        self,
        table_config: Mapping[str, Any],
        years: Optional[list[int]] = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """
        Description: Removes source rows for years a prior run already archived. `years=None` walks every year past retention; `dry_run=True` plans only and writes DRY_RUN audit rows.
        Parameters: table_config: table config; years: whitelist of years (None => all eligible); dry_run: emit DRY_RUN rows only when True
        Return: report dict {"tables": {tid: {"years": {year: {...}}}}, "had_concurrent_failures": bool}
        """
        # Preamble: merge config, validate, parse conditions, set tz, ensure audit table.
        merged, _conditions, exclusion_clause, tid = self._prepare_run(table_config)

        # Year scope: caller-supplied list, otherwise everything past the retention cutoff.
        if years is None:
            scope_years = self._calculate_eligible_years(
                merged,
                int(merged["retention_years"]),
            )
        else:
            scope_years = sorted({int(y) for y in years})

        # Empty report scaffold; per-year entries get filled in below.
        report: dict[str, Any] = {
            "tables": {tid: {"years": {}}},
            "had_concurrent_failures": False,
        }
        years_report = report["tables"][tid]["years"]

        # Table-wide concurrency guard: another run touching ANY year on this table blocks all years.
        stale_h = float(self._ctx.settings.get("stale_started_threshold_hours", 4))
        is_busy, foreign_run_id, foreign_year, age_hours = (
            self._audit.check_concurrent_any_year(
                tid,
                self._ctx.archive_run_id,
                stale_threshold_hours=stale_h,
            )
        )
        if is_busy:
            # Mark every requested year as SKIP_CONCURRENT and bail out early.
            report["had_concurrent_failures"] = True
            for y in scope_years:
                msg = ArchiveError.diagnostic_message(
                    "FAILED",
                    "concurrent_run_on_table",
                    table=tid,
                    year=y,
                    foreign_run_id=foreign_run_id,
                    foreign_year=foreign_year,
                    age_hours=age_hours if age_hours is not None else 0.0,
                    stale_threshold_hours=stale_h,
                )
                self._record_delete_skip(
                    years_report, tid, y,
                    dry_run=dry_run,
                    dry_action="SKIP_CONCURRENT",
                    reason="concurrent_run_on_table",
                    error_message=msg,
                )
            return report

        # Per-year processing: eligibility -> drift check -> dry-run plan or live delete.
        for y in scope_years:
            # Eligibility gate: must be ARCHIVED by a prior run, no in-flight starts, etc.
            eligible, reason_code = self._audit.is_eligible_for_delete(
                tid, y, archive_run_id=self._ctx.archive_run_id,
                stale_threshold_hours=stale_h,
            )
            if not eligible:
                last_state = self._audit.get_last_run_state(tid, y)
                last_status = last_state[0] if last_state else None
                msg = ArchiveError.diagnostic_message(
                    "FAILED",
                    "not_eligible_for_delete",
                    table=tid,
                    year=y,
                    reason_code=reason_code,
                    last_status=last_status,
                )
                self._record_delete_skip(
                    years_report, tid, y,
                    dry_run=dry_run,
                    dry_action="SKIP_NOT_ELIGIBLE",
                    reason=reason_code,
                    error_message=msg,
                )
                continue

            # Drift check: archive count must match source count before we delete anything.
            archive_count = archive_row_count(
                self._spark,
                merged["archive_base_path"],
                merged["source_table"],
                y,
            )
            # Source rows the delete job would actually remove.
            source_count = self._source_year_count(
                merged, y, exclusion_clause=exclusion_clause,
            )
            if archive_count != source_count:
                # Mismatch: dry-run records SKIP_DRIFT, live run logs VERIFY_FAILED. No delete either way.
                if dry_run:
                    self._audit.log_dry_run(
                        table=tid,
                        year=y,
                        total_eligible=source_count,
                        would_archive=0,
                        per_condition_counts={},
                        action="SKIP_DRIFT",
                    )
                    years_report[y] = {
                        "action": "SKIP_DRIFT",
                        "status": "DRY_RUN",
                        "record_count": 0,
                        "reason": "source_drift",
                        "archive_count": archive_count,
                        "source_count": source_count,
                    }
                else:
                    self._audit.log_verify_failed(
                        table=tid,
                        year=y,
                        archive_count=archive_count,
                        source_count=source_count,
                    )
                    years_report[y] = {
                        "action": "VERIFY_FAILED",
                        "status": "VERIFY_FAILED",
                        "record_count": archive_count,
                        "reason": "source_drift",
                        "archive_count": archive_count,
                        "source_count": source_count,
                    }
                continue

            # Dry-run: record WOULD_DELETE and skip the actual delete.
            if dry_run:
                self._audit.log_dry_run(
                    table=tid,
                    year=y,
                    total_eligible=source_count,
                    would_archive=source_count,
                    per_condition_counts={},
                    action="WOULD_DELETE",
                )
                years_report[y] = {
                    "action": "WOULD_DELETE",
                    "status": "DRY_RUN",
                    "record_count": source_count,
                    "archive_count": archive_count,
                    "source_count": source_count,
                }
                continue

            # Live delete: scope rows by this year's full watermark window (no after_watermark bound).
            run_wm_low, run_wm_high = self._run_watermark_window(
                merged, y, after_watermark=None,
            )
            self._delete_archived(
                merged, y, exclusion_clause, run_wm_low, run_wm_high,
            )

            # Build a human-readable note linking this delete back to the original archive run.
            prior = self._audit.get_prior_archived_run(tid, y)
            if prior is not None:
                prior_run_id, prior_ts = prior
                message = (
                    f"Deleted {source_count} rows from source. "
                    f"Originally archived by run {prior_run_id} at {prior_ts}."
                )
            else:
                message = (
                    f"Deleted {source_count} rows from source. "
                    f"Prior archive run not found in audit."
                )

            # Audit row + report entry: ARCHIVED_AND_DELETED with the prior watermark.
            last_state = self._audit.get_last_run_state(tid, y)
            last_wm = last_state[1] if last_state else None
            self._audit.log_archive(
                table=tid,
                year=y,
                status="ARCHIVED_AND_DELETED",
                record_count=source_count,
                error_message=message,
                watermark_value=last_wm,
                source_year_count=None,
                archive_mode="DELETE",
                needs_review=False,
                archive_delta_version=None,
            )
            years_report[y] = {
                "action": "DELETE",
                "status": "ARCHIVED_AND_DELETED",
                "record_count": source_count,
                "archive_count": archive_count,
                "source_count": source_count,
                "message": message,
            }
        return report

    def _record_delete_skip(
        self,
        years_report: dict,
        table: str,
        year: int,
        *,
        dry_run: bool,
        dry_action: str,
        reason: str,
        error_message: str,
    ) -> None:
        """
        Description: Routes a skipped year to the right audit row (DRY_RUN with `dry_action` in dry mode, FAILED in live) and mirrors the entry into the report dict. Bi-modal so reports stay consistent across modes.
        Parameters: years_report: per-year report dict (mutated); table: table id; year: year; dry_run: True for dry mode; dry_action: dry-run action label (e.g. SKIP_CONCURRENT); reason: short machine code; error_message: human-readable diagnostic
        Return: None (mutates years_report)
        """
        if dry_run:
            self._audit.log_dry_run(
                table=table,
                year=year,
                total_eligible=0,
                would_archive=0,
                per_condition_counts={},
                action=dry_action,
            )
            years_report[year] = {
                "action": dry_action,
                "status": "DRY_RUN",
                "record_count": 0,
                "reason": reason,
                "error_message": error_message,
            }
        else:
            self._log_delete_failed(table, year, error_message)
            years_report[year] = {
                "action": "FAILED",
                "status": "FAILED",
                "record_count": 0,
                "reason": reason,
                "error_message": error_message,
            }

    def _log_delete_failed(
        self,
        tid: str,
        year: int,
        message: str,
    ) -> None:
        """
        Description: Writes a FAILED audit row for one delete-job table-year with `needs_review=True` so operators get flagged. Wrapper exists to pin FAILED-row defaults across delete-job skip paths.
        Parameters: tid: table id; year: partition year; message: diagnostic message
        Return: None
        """
        self._audit.log_archive(
            table=tid,
            year=year,
            status="FAILED",
            record_count=0,
            error_message=message,
            archive_mode=None,
            needs_review=True,
            archive_delta_version=None,
        )
