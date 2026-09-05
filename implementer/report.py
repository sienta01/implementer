"""Run summary: console table plus a JSON audit trail."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing playwright just to print a summary
    from .emr import PatientResult

_SYMBOL = {
    "done": "OK",
    "dry-run": "DRY",
    "partial": "!!",
    "failed": "XX",
    "skipped": "--",
}


def print_summary(results: list["PatientResult"]) -> None:
    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    width = max((len(r.patient.label) for r in results), default=10)
    for result in results:
        saved = sum(1 for t in result.tindakan if t.outcome in ("saved", "dry-run"))
        skipped = sum(1 for t in result.tindakan if t.outcome == "skipped")
        failed = sum(1 for t in result.tindakan if t.outcome == "failed")
        counts = f"{saved} saved"
        if skipped:
            counts += f", {skipped} already there"
        if failed:
            counts += f", {failed} FAILED"
        print(f"  [{_SYMBOL.get(result.outcome, '??'):>3}] {result.patient.label:<{width}}  {counts}")
        if result.detail:
            print(f"        {result.detail}")
        for tindakan in result.tindakan:
            if tindakan.outcome == "failed":
                print(f"        - {tindakan.code}: {tindakan.detail}")
    print("-" * 78)
    tally: dict[str, int] = {}
    for result in results:
        tally[result.outcome] = tally.get(result.outcome, 0) + 1
    print("  " + ", ".join(f"{count} {name}" for name, count in sorted(tally.items())))
    print("=" * 78)


def write_report(
    results: list["PatientResult"], path: Path, run_id: str, dry_run: bool
) -> None:
    payload = {
        "run_id": run_id,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "patients": [
            {
                "nama": result.patient.nama,
                "no_rm": result.patient.no_rm,
                "ruangan": result.patient.ruangan,
                "worklist_line": result.patient.row_number,
                "outcome": result.outcome,
                "detail": result.detail,
                "subyektif_touched": result.subyektif_touched,
                "tindakan": [
                    {"code": t.code, "outcome": t.outcome, "detail": t.detail}
                    for t in result.tindakan
                ],
            }
            for result in results
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport written to {path}")


def exit_code(results: list["PatientResult"]) -> int:
    if any(result.outcome in ("failed", "partial") for result in results):
        return 1
    if any(result.outcome == "skipped" for result in results):
        return 2
    return 0
