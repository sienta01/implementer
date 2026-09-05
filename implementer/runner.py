"""Orchestrates the whole run: log in once, then walk the worklist."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Credentials, Settings
from .emr import EmrSession, FatalError, PatientResult, StepError
from .inputs import Patient, group_by_ward

log = logging.getLogger("implementer.run")


def run(
    page,
    patients: list[Patient],
    creds: Credentials,
    settings: Settings,
    dry_run: bool = False,
    debug_dir: Path | None = None,
) -> list[PatientResult]:
    session = EmrSession(page, creds, settings, log, dry_run=dry_run, debug_dir=debug_dir)
    session.open_form()
    session.ensure_logged_in()
    session.verify_active_user()

    results: list[PatientResult] = []
    for ward, ward_patients in group_by_ward(patients):
        log.info("")
        log.info("=== ward: %s (%d patient(s)) ===", ward, len(ward_patients))
        try:
            session.select_ward(ward)
        except (StepError, FatalError) as exc:
            log.error("cannot open ward %r: %s", ward, exc)
            for patient in ward_patients:
                results.append(
                    PatientResult(patient, outcome="skipped", detail=f"ward: {exc}")
                )
            continue

        for patient in ward_patients:
            log.info("")
            log.info("--- %s (worklist line %d) ---", patient.label, patient.row_number)
            results.append(_process_patient(session, patient, settings, dry_run))

    return results


def _process_patient(
    session: EmrSession, patient: Patient, settings: Settings, dry_run: bool
) -> PatientResult:
    result = PatientResult(patient)
    codes = patient.tindakan or settings.tindakan
    keyword = patient.keyword or settings.cppt_keyword

    if not codes:
        result.outcome = "skipped"
        result.detail = "no tindakan codes configured for this patient"
        log.warning("  %s", result.detail)
        return result

    try:
        session.open_patient(patient)
        session.open_rppt_cppt()
        row = session.find_cppt_row(keyword)

        if settings.touch_subyektif:
            result.subyektif_touched = session.touch_subyektif(row)
            if result.subyektif_touched:
                # The table is redrawn after the save; re-read before reusing it.
                row = session.find_cppt_row(keyword)
                session.verify_cppt_updater(row)

        session.open_implementasi(row)
        try:
            for code in codes:
                result.tindakan.append(session.add_tindakan(code))
        finally:
            session.close_implementasi()

    except StepError as exc:
        result.outcome = "failed"
        result.detail = str(exc)
        log.error("  %s", exc)
        _recover(session)
        return result
    except FatalError:
        raise
    except Exception as exc:  # noqa: BLE001 - one bad patient must not kill the run
        result.outcome = "failed"
        result.detail = f"unexpected error: {exc}"
        log.exception("  unexpected error while processing %s", patient.label)
        _recover(session)
        return result

    outcomes = {t.outcome for t in result.tindakan}
    if outcomes == {"dry-run"} or (dry_run and "failed" not in outcomes):
        result.outcome = "dry-run"
    elif "failed" in outcomes:
        result.outcome = "partial"
        result.detail = "; ".join(
            f"{t.code}: {t.detail}" for t in result.tindakan if t.outcome == "failed"
        )
    else:
        result.outcome = "done"
    return result


def _recover(session: EmrSession) -> None:
    """Get back to a clean page state so the next patient can start."""
    try:
        session.close_implementasi()
        session.drain_bootbox(timeout=500)
        session.clear_toasts()
    except Exception:  # noqa: BLE001
        log.debug("recovery failed", exc_info=True)
