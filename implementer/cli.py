"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, selectors
from .config import ConfigError, load_credentials, load_settings, validate_tindakan
from .inputs import Patient, load_patients
from .report import exit_code, print_summary, write_report
from .util import make_run_id

DEFAULT_CREDENTIALS = "credentials.json"
DEFAULT_CONFIG = "config.json"
DEFAULT_PATIENTS = "patients.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="implementer",
        description=(
            "Fill in 'Implementasi' entries in the SIMETRIS EMR for a list of "
            "patients. Runs on macOS and Windows."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"implementer {__version__}")

    files = parser.add_argument_group("input files")
    files.add_argument("-c", "--credentials", type=Path, default=Path(DEFAULT_CREDENTIALS),
                       help="JSON file with base_url, username, password, expected_user")
    files.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG),
                       help="JSON file with run settings (optional)")
    files.add_argument("-p", "--patients", type=Path, default=Path(DEFAULT_PATIENTS),
                       help="CSV worklist: nama, no_rm, ruangan[, tindakan][, keyword]")
    files.add_argument("--report", type=Path, default=None,
                       help="where to write the JSON run report "
                            "(default: runs/<timestamp>.json)")

    single = parser.add_argument_group("single patient (instead of --patients)")
    single.add_argument("--nama", help="patient name")
    single.add_argument("--no-rm", dest="no_rm", help="medical record number")
    single.add_argument("--ruangan", help="ward / Poli-Ruang name, e.g. 'Rawat Inap Anak (Gardenia 1)'")

    work = parser.add_argument_group("what to record")
    work.add_argument("-t", "--tindakan", action="append", default=None, metavar="XX.XX.XXX",
                      help="tindakan code; repeat for several (overrides config.json)")
    work.add_argument("-k", "--keyword", default=None,
                      help="text identifying the CPPT row, e.g. 'CPPT Alergi Imunologi'")
    work.add_argument("--status", default=None, choices=sorted(selectors.STATUS_LABELS),
                      help="implementasi status value (3 = Selesai Dilakukan)")
    work.add_argument("--allow-any-date", action="store_true",
                      help="also accept CPPT rows that are not dated today")
    work.add_argument("--no-touch-subyektif", action="store_true",
                      help="skip step 5 (appending a blank line and re-saving the CPPT)")

    how = parser.add_argument_group("how to run")
    how.add_argument("-n", "--dry-run", action="store_true",
                     help="walk every step but never click a save button")
    how.add_argument("--plan", action="store_true",
                     help="validate the inputs, print the plan, and exit without a browser")
    how.add_argument("--headless", action="store_true", help="run without a visible browser window")
    how.add_argument("--confirm", action="store_true",
                     help="pause for Enter before every save")
    how.add_argument("--slow-mo", type=int, default=None, metavar="MS",
                     help="delay between browser actions, to watch what happens")
    how.add_argument("--timeout", type=int, default=None, metavar="MS",
                     help="per-action timeout")
    how.add_argument("--profile", type=Path, default=None, metavar="DIR",
                     help="reuse a browser profile directory so the login survives runs")
    how.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    how.add_argument("--log-file", type=Path, default=None,
                     help="also write the log here (default: logs/<timestamp>.log)")

    return parser


def setup_logging(verbose: bool, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger("implementer")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)


def collect_patients(args: argparse.Namespace) -> list[Patient]:
    if args.nama or args.no_rm:
        if not args.ruangan:
            raise ConfigError("--ruangan is required when using --nama/--no-rm")
        return [
            Patient(
                nama=(args.nama or "").strip(),
                no_rm=(args.no_rm or "").strip(),
                ruangan=args.ruangan.strip(),
                row_number=0,
            )
        ]
    return load_patients(args.patients)


def print_plan(patients: list[Patient], settings, creds, dry_run: bool) -> None:
    print(f"EMR       : {creds.form_url}")
    print(f"user      : {creds.username} ({creds.expected_user or 'identity check disabled'})")
    print(f"keyword   : {settings.cppt_keyword}")
    print(f"status    : {settings.status} = {selectors.STATUS_LABELS[settings.status]}")
    print(f"tindakan  : {', '.join(settings.tindakan) or '(per patient only)'}")
    print(f"today only: {settings.require_today}")
    print(f"mode      : {'DRY RUN - nothing will be saved' if dry_run else 'LIVE - records will be written'}")
    print()
    print(f"{len(patients)} patient(s):")
    for patient in patients:
        codes = patient.tindakan or settings.tindakan
        keyword = patient.keyword or settings.cppt_keyword
        print(f"  - {patient.nama or '(by MR)'} / {patient.no_rm or '-'} @ {patient.ruangan}")
        print(f"      keyword : {keyword}")
        print(f"      tindakan: {', '.join(codes) or 'NONE - will be skipped'}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = make_run_id()
    log_file = args.log_file or Path("logs") / f"{run_id}.log"
    setup_logging(args.verbose, log_file)

    try:
        creds = load_credentials(args.credentials)
        settings = load_settings(args.config if args.config.exists() else None)

        if args.tindakan is not None:
            settings.tindakan = [validate_tindakan(code) for code in args.tindakan]
        if args.keyword:
            settings.cppt_keyword = args.keyword
        if args.status:
            settings.status = args.status
        if args.allow_any_date:
            settings.require_today = False
        if args.no_touch_subyektif:
            settings.touch_subyektif = False
        if args.headless:
            settings.headless = True
        if args.confirm:
            settings.confirm_each_save = True
        if args.slow_mo is not None:
            settings.slow_mo_ms = args.slow_mo
        if args.timeout is not None:
            settings.timeout_ms = args.timeout
        settings.validate()

        patients = collect_patients(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    without_codes = [p for p in patients if not (p.tindakan or settings.tindakan)]
    if without_codes:
        print(
            "error: no tindakan codes for "
            f"{', '.join(p.label for p in without_codes)}. "
            "Pass --tindakan, set 'tindakan' in config.json, or add a tindakan "
            "column to the CSV.",
            file=sys.stderr,
        )
        return 3

    print_plan(patients, settings, creds, args.dry_run)
    if args.plan:
        return 0

    # Imported here so --plan works before playwright is installed.
    from .browser import open_page
    from .runner import run

    print()
    try:
        with open_page(settings, args.profile) as page:
            results = run(
                page,
                patients,
                creds,
                settings,
                dry_run=args.dry_run,
                debug_dir=log_file.parent,
            )
    except KeyboardInterrupt:
        print("\ninterrupted by user", file=sys.stderr)
        return 130
    except ImportError as exc:
        print(
            f"error: {exc}\nRun: pip install -r requirements.txt && python -m playwright install chromium",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:  # noqa: BLE001 - surface the reason, not a traceback
        logging.getLogger("implementer").exception("run aborted")
        print(f"\nrun aborted: {exc}", file=sys.stderr)
        return 4

    print_summary(results)
    write_report(results, args.report or Path("runs") / f"{run_id}.json", run_id, args.dry_run)
    return exit_code(results)
