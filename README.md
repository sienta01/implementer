# implementer

Automates the "Mengisi Implementasi" workflow in the SIMETRIS EMR: for each
patient on a worklist it opens the chart, finds today's CPPT row, re-saves it,
and records the given *tindakan* codes as **Selesai Dilakukan**.

Runs the same way on **macOS** and **Windows** (Playwright + Chromium).

---

## 1. Install

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Python 3.10 or newer.

> **Windows:** if `playwright install` fails with a "No such file or directory"
> error about a long path, enable long path support once (admin PowerShell):
> `Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1`
> and reboot — or move the project closer to the drive root.

## 2. Configure

**`credentials.json`** — copy `credentials.example.json` and fill it in:

```json
{
  "base_url": "https://simetriss.rsupprofngoerah.my.id",
  "username": "YOUR_USERNAME",
  "password": "YOUR_PASSWORD",
  "expected_user": "dr Ida Bagus Ramajaya Sutawan M.Biomed., Sp.A (K)"
}
```

- `expected_user` is checked against the name the EMR shows after login
  (step 2). If they don't match, the run stops before touching any record.
  Leave it out to skip the check.
- To keep the password out of the file, set it to `"env:EMR_PASSWORD"` and put
  the real value in that environment variable.
- `credentials.json`, `config.json` and `patients.csv` are already in
  `.gitignore`. On macOS/Linux also run `chmod 600 credentials.json`.

**`config.json`** *(optional)* — copy `config.example.json`:

| key | default | meaning |
|---|---|---|
| `cppt_keyword` | `CPPT Alergi Imunologi` | text that identifies the CPPT row (step 5). Not hard-coded. |
| `tindakan` | `[]` | default tindakan codes, `xx.xx.xxx`, digits only |
| `status` | `"3"` | `0` Belum dilakukan · `1` Selesai Sebagian · `2` Tidak Dilakukan · `3` Selesai Dilakukan |
| `require_today` | `true` | only touch CPPT rows dated today |
| `touch_subyektif` | `true` | step 5: append a blank line to Subyektif and re-save |
| `skip_existing` | `true` | don't re-record a tindakan already in Catatan Implementasi |
| `headless` | `false` | run without a visible window |
| `slow_mo_ms` | `0` | slow every action down so you can watch |
| `timeout_ms` | `30000` | per-action timeout |
| `confirm_each_save` | `false` | pause for Enter before every save |

**`patients.csv`** — the worklist. Copy `patients.example.csv`:

```csv
nama,no_rm,ruangan,tindakan,keyword
CHIARA AURELIA ELMINA KLAU,26030012,Rawat Inap Anak (Gardenia 1),,
CONTOH PASIEN DUA,26030099,Rawat Inap Anak (Gardenia 1),01.01.010;01.49.101,
```

- `nama`, `no_rm`, `ruangan` are the inputs. `ruangan` must match a name in the
  Poli/Ruang dropdown (e.g. `Rawat Inap Anak (Gardenia 1)`, `Angsoka 1`).
- `tindakan` and `keyword` are optional per-patient overrides; leave blank to
  use the values from `config.json` / the command line.
- Headers are case-insensitive and accept aliases (`name`, `mr`, `mr#`,
  `no_mr`, `ward`, `poli`, …). Separate multiple tindakan with `;` or `|`.
- Consecutive patients in the same ward are batched, so the ward filter is set
  once per ward instead of once per patient.

## 3. Run

Always start with a dry run — it walks every step but never clicks a save button:

```bash
python main.py --dry-run
```

Then, for real:

```bash
python main.py
```

Useful variations:

```bash
# check the inputs only; no browser at all
python main.py --plan

# one patient, tindakan on the command line
python main.py --nama "CHIARA AURELIA ELMINA KLAU" --no-rm 26030012 \
  --ruangan "Rawat Inap Anak (Gardenia 1)" -t 01.01.010 -t 01.49.101

# watch it work, and confirm every save by hand
python main.py --slow-mo 300 --confirm

# a different CPPT row
python main.py -k "CPPT Alergi Imunologi"

# reuse the login between runs
python main.py --profile .browser-profile
```

`python main.py --help` lists everything.

Exit codes: `0` all good · `1` something failed or was partial · `2` a patient
was skipped · `3` bad input · `4` the run aborted · `130` Ctrl+C.

## 4. What it does

Following `Mengisi Implementasi.md`:

1. Opens `<base_url>/emr/form`.
2. Logs in if needed. If the EMR answers with **USER SUDAH LOGIN DI PERANGKAT
   LAIN**, it presses **Ok, lanjut login** — which signs your other session out,
   and is logged as a warning. Login drops you on `/main/info_rs`, so it then
   navigates straight back to `/emr/form`. Finally it closes the **Set Default
   Jenis Pelayanan** pop-up and checks the signed-in name against
   `expected_user`.
3. Selects the **Semua** filter, sets **Poli/Ruang** to the ward, closes the
   **Resume LOS** pop-up.
4. Searches the registration list (by MR number when there is one, otherwise by
   name) and double-clicks the row. **Refuses to continue if the chart that
   opens has a different name or MR number than the worklist row.**
5. Opens **RPPT & CPPT**, finds the row whose text contains the keyword *and*
   is dated today, clicks **Ubah** (accepting the confirmation), appends a
   single blank line to **Subyektif** — existing text is read back and verified
   unchanged — saves, and checks the row's *User Update* is now the signed-in
   user.
6. Clicks **Implementasi** on that same row.
7. For each tindakan code: stamps the date, picks the tindakan, sets the status,
   and saves. Pop-ups are acknowledged and their text is reported.
8. Closes the modal and moves to the next patient.

Each tindakan is saved individually, because the EMR clears the form after every
save.

**One deliberate deviation from the .md:** the date is stamped *before* the
tindakan is chosen, not after. The tindakan lookup sends the date field to the
server as `tanggal_tindakan`, so searching with an empty date can return the
wrong list or nothing at all. The end result is identical.

## 5. Output

- `logs/<timestamp>.log` — full log of the run, including every pop-up message.
- `logs/login-*.png` and `logs/login-*.txt` — a screenshot plus a list of the
  dialogs on screen, written whenever the login stalls, so the cause is visible
  without guessing.
- `runs/<timestamp>.json` — machine-readable report: per patient and per
  tindakan, what was saved, skipped or failed and why.
- A summary table on the console.

## 6. When something goes wrong

| message | what to check |
|---|---|
| `the EMR refused the login: <message>` | the site's own words, e.g. *Username atau Password Anda salah* |
| `USER SUDAH LOGIN DI PERANGKAT LAIN - continuing` | not an error: your session elsewhere was signed out so this run could log in |
| `the EMR is asking to 'Set Default DPJP'` | your account is an SMF account; log in once by hand, pick the DPJP, then re-run (or use `--profile`) |
| `login is waiting on the Pakta Integritas / Kode Etik agreement` | accept it yourself in a browser once — the tool will not agree on your behalf |
| `the login request never came back` | the EMR did not answer the login post; try again |
| `the login did not resolve` | see the screenshot and notes saved next to the log |
| `the EMR keeps redirecting away from .../emr/form` | the login did not really take effect; log in by hand once and re-run |
| `signed-in user ... does not match expected_user` | wrong account, or fix `expected_user` |
| `ward ... is not in the Poli/Ruang list` | spell `ruangan` exactly as the dropdown shows it |
| `not found in the ... registration list` | wrong ward, or the patient is filtered out by **Hanya yang sudah dianamnesis** — untick it in the browser and re-run |
| `opened chart has No. RM ... refusing to write` | the worklist row and the chart disagree; nothing was written |
| `no CPPT row containing ...` | the CPPT for that keyword doesn't exist yet, or is on another page of the table |
| `none dated today` | the CPPT is from an earlier day; use `--allow-any-date` only if that is really what you want |
| `the tindakan was cleared by the form` | the EMR rejected it (no tarif set, wrong unit) — the pop-up text is in the log |

Run with `-v` for debug logging, and without `--headless` to watch.

## 7. Layout

```
main.py                  entry point
implementer/
  cli.py                 arguments, logging, plan printing
  config.py              credentials.json + config.json
  inputs.py              patients.csv
  browser.py             Playwright launch, native-dialog handling
  runner.py              the per-patient loop
  emr.py                 all the EMR steps
  selectors.py           every CSS selector, in one place
  util.py                name/date matching helpers
  report.py              summary + JSON report
```

If the EMR's markup changes, `selectors.py` is normally the only file to edit.
