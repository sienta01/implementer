"""Patient worklist input.

The worklist is a CSV so it can be produced from Excel/Sheets by anyone.
Columns are matched case-insensitively and a few common aliases are accepted.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError, validate_tindakan

# Accepted header spellings -> canonical field name.
_ALIASES = {
    "nama": "nama",
    "name": "nama",
    "nama_pasien": "nama",
    "patient": "nama",
    "patient_name": "nama",
    "no_rm": "no_rm",
    "norm": "no_rm",
    "rm": "no_rm",
    "mr": "no_rm",
    "mr#": "no_rm",
    "no_mr": "no_rm",
    "mrn": "no_rm",
    "medical_record": "no_rm",
    "ruangan": "ruangan",
    "ruang": "ruangan",
    "ward": "ruangan",
    "poli": "ruangan",
    "poliruang": "ruangan",
    "kamar": "kamar",
    "bed": "kamar",
    "tindakan": "tindakan",
    "tindakan_list": "tindakan",
    "keyword": "keyword",
    "cppt_keyword": "keyword",
}

_SPLIT_RE = re.compile(r"[;|,]")


@dataclass
class Patient:
    nama: str
    no_rm: str
    ruangan: str
    tindakan: list[str] = field(default_factory=list)
    keyword: str = ""
    row_number: int = 0

    @property
    def search_term(self) -> str:
        """What to type into the registrasi search box.

        The MR number is unambiguous, so prefer it when we have one.
        """
        return self.no_rm or self.nama

    @property
    def label(self) -> str:
        parts = [p for p in (self.nama, self.no_rm) if p]
        return " / ".join(parts) if parts else "<unnamed>"


def _normalise_header(name: str) -> str:
    return re.sub(r"[^a-z0-9#]+", "_", name.strip().lower()).strip("_")


def load_patients(path: Path) -> list[Patient]:
    if not path.exists():
        raise ConfigError(f"patient list not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ConfigError(f"{path} has no header row")

        mapping: dict[str, str] = {}
        for raw in reader.fieldnames:
            if raw is None:
                continue
            canonical = _ALIASES.get(_normalise_header(raw))
            if canonical:
                mapping[raw] = canonical

        if "nama" not in mapping.values() and "no_rm" not in mapping.values():
            raise ConfigError(
                f"{path} must have at least a 'nama' or 'no_rm' column "
                f"(found: {', '.join(reader.fieldnames)})"
            )
        if "ruangan" not in mapping.values():
            raise ConfigError(
                f"{path} must have a 'ruangan' column "
                f"(found: {', '.join(reader.fieldnames)})"
            )

        patients: list[Patient] = []
        for line_no, row in enumerate(reader, start=2):
            values = {"nama": "", "no_rm": "", "ruangan": "", "kamar": "",
                      "tindakan": "", "keyword": ""}
            for raw, canonical in mapping.items():
                values[canonical] = (row.get(raw) or "").strip()

            if not any(values[k] for k in ("nama", "no_rm")):
                continue  # blank filler row

            if not values["ruangan"]:
                raise ConfigError(
                    f"{path} line {line_no}: 'ruangan' is empty for {values['nama'] or values['no_rm']}"
                )

            codes = [c.strip() for c in _SPLIT_RE.split(values["tindakan"]) if c.strip()]
            try:
                codes = [validate_tindakan(c) for c in codes]
            except ConfigError as exc:
                raise ConfigError(f"{path} line {line_no}: {exc}") from exc

            patients.append(
                Patient(
                    nama=values["nama"],
                    no_rm=values["no_rm"],
                    ruangan=values["ruangan"],
                    tindakan=codes,
                    keyword=values["keyword"],
                    row_number=line_no,
                )
            )

    if not patients:
        raise ConfigError(f"{path} contains no patient rows")
    return patients


def group_by_ward(patients: list[Patient]) -> list[tuple[str, list[Patient]]]:
    """Keep worklist order but batch consecutive patients per ward.

    Switching the Poli/Ruang filter reloads the registration list, so doing it
    once per ward instead of once per patient saves a round trip.
    """
    groups: list[tuple[str, list[Patient]]] = []
    for patient in patients:
        if groups and groups[-1][0].casefold() == patient.ruangan.casefold():
            groups[-1][1].append(patient)
        else:
            groups.append((patient.ruangan, [patient]))
    return groups
