"""Drives the SIMETRIS EMR through the 'Mengisi Implementasi' workflow.

Step numbers in the log messages follow 'Mengisi Implementasi.md'.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import selectors as S
from .config import Credentials, Settings
from .inputs import Patient
from .util import (
    contains_keyword,
    fold,
    is_today,
    names_match,
    patient_names_match,
    squash,
)


class StepError(Exception):
    """This patient cannot be processed; move on to the next one."""


class FatalError(Exception):
    """The session is unusable (login failed, site down); stop the run."""


@dataclass
class CpptRow:
    cppt_id: str
    instruksi_id: str
    tgl: str
    ppa: str
    asesmen: str

    @property
    def edit_selector(self) -> str:
        return f"button.cppt-btn-edit[data-cppt-id='{self.cppt_id}']:visible"

    @property
    def implementasi_selector(self) -> str:
        return f"button.cppt-btn-implementasi[data-i-id='{self.instruksi_id}']:visible"


@dataclass
class TindakanResult:
    code: str
    outcome: str  # saved | skipped | dry-run | failed
    detail: str = ""


@dataclass
class PatientResult:
    patient: Patient
    outcome: str = "pending"  # done | partial | skipped | failed
    detail: str = ""
    subyektif_touched: bool = False
    tindakan: list[TindakanResult] = field(default_factory=list)


def _best_option(options: list[str], match: str) -> int | None:
    """Pick the option best matching `match`: exact, then prefix, then substring."""
    target = fold(match)
    if not target:
        return None
    folded = [fold(option) for option in options]
    for scorer in (
        lambda o: o == target,
        lambda o: o.startswith(target),
        lambda o: target in o,
    ):
        for index, option in enumerate(folded):
            if scorer(option):
                return index
    return None


class EmrSession:
    """One logged-in browser session, reused across every patient in the run."""

    def __init__(
        self,
        page: Page,
        creds: Credentials,
        settings: Settings,
        log,
        dry_run: bool = False,
        debug_dir: Path | None = None,
    ) -> None:
        self.page = page
        self.creds = creds
        self.settings = settings
        self.log = log
        self.dry_run = dry_run
        # Where screenshots/notes go when a step fails in a way worth seeing.
        self.debug_dir = debug_dir
        self.active_user = ""
        self.current_ward = ""
        page.set_default_timeout(settings.timeout_ms)

    # ------------------------------------------------------------------ utils
    def _short_timeout(self) -> int:
        return min(5_000, self.settings.timeout_ms)

    def _visible(self, selector: str, timeout: int = 1_500) -> bool:
        try:
            self.page.wait_for_selector(selector, state="visible", timeout=timeout)
            return True
        except PlaywrightTimeout:
            return False

    def _click(self, selector: str, timeout: int | None = None) -> None:
        """Click, falling back to a JS click when an overlay is in the way."""
        locator = self.page.locator(selector).first
        try:
            locator.click(timeout=timeout or self.settings.timeout_ms)
        except (PlaywrightTimeout, PlaywrightError):
            locator.evaluate("el => el.click()")

    def _text(self, selector: str) -> str:
        locator = self.page.locator(selector).first
        if locator.count() == 0:
            return ""
        return squash(locator.inner_text())

    def _jquery(self, body: str, arg=None):
        """Run a snippet with the page's jQuery bound to `$`."""
        return self.page.evaluate("(arg) => { const $ = window.jQuery; " + body + " }", arg)

    def _settle(self, seconds: float = 0.4) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def close_select2(self) -> None:
        """Close any open select2 without pressing Escape (that closes modals)."""
        self._jquery(
            "const s = $('select.select2-hidden-accessible');"
            " if (s.length) { try { s.select2('close'); } catch (e) {} }"
        )

    def close_datepicker(self, container: str) -> None:
        self._jquery(
            "const dp = $(arg).data('DateTimePicker');"
            " if (dp && dp.hide) { try { dp.hide(); } catch (e) {} }",
            container,
        )

    def _wait_not_loading(self, selector: str, css_class: str, timeout: int = 30_000) -> None:
        """Wait until `selector` no longer carries `css_class` (panel spinners)."""
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            busy = self.page.evaluate(
                "([sel, cls]) => { const el = document.querySelector(sel);"
                " return !!el && el.classList.contains(cls); }",
                [selector, css_class],
            )
            if not busy:
                return
            self.page.wait_for_timeout(200)

    # ----------------------------------------------------------------- popups
    def dismiss_modal(self, selector: str, timeout: int = 2_000) -> bool:
        """Close a Bootstrap modal by its dismiss button, if it is showing."""
        if not self._visible(selector, timeout=timeout):
            return False
        self.log.debug("closing modal %s", selector)
        for candidate in (f"{selector} button.close", f"{selector} [data-dismiss='modal']"):
            if self.page.locator(candidate).count():
                self._click(candidate)
                break
        else:
            self._jquery("$(arg).modal('hide');", selector)
        try:
            self.page.wait_for_selector(selector, state="hidden", timeout=timeout)
        except PlaywrightTimeout:
            self._jquery("$(arg).modal('hide');", selector)
        self._settle(0.2)
        return True

    def drain_bootbox(self, timeout: int = 1_200) -> list[str]:
        """Acknowledge every bootbox alert on screen; return their messages."""
        messages: list[str] = []
        while self._visible(S.BOOTBOX, timeout=timeout):
            text = self._text(f"{S.BOOTBOX} .bootbox-body, {S.BOOTBOX} .modal-body")
            messages.append(text)
            self.log.info("    popup: %s", text or "(no text)")
            affirmative = (
                f"{S.BOOTBOX} .modal-footer button.btn-primary, "
                f"{S.BOOTBOX} .modal-footer button.btn-success"
            )
            if self.page.locator(S.BOOTBOX_HANDLER_OK).count():
                self._click(S.BOOTBOX_HANDLER_OK)
            elif self.page.locator(affirmative).count():
                self._click(affirmative)
            elif self.page.locator(S.BOOTBOX_OK).count():
                self._click(S.BOOTBOX_OK)
            else:
                self._jquery("$('.bootbox').modal('hide');")
            try:
                self.page.wait_for_selector(S.BOOTBOX, state="hidden", timeout=3_000)
            except PlaywrightTimeout:
                self._jquery("$('.bootbox').modal('hide');")
                break
            timeout = 600  # only glance for a follow-up popup
        return messages

    def clear_toasts(self) -> None:
        self.page.evaluate(
            "() => document.querySelectorAll('#toast-container .toast')"
            ".forEach(el => el.remove())"
        )

    def wait_for_toast(self, timeout: int | None = None) -> tuple[str, str]:
        """Wait for the next toastr message. Returns (kind, text).

        A bootbox alert counts as a reply too: the app uses them for validation
        errors that never produce a toast.
        """
        deadline = time.monotonic() + (timeout or self.settings.timeout_ms) / 1000
        while time.monotonic() < deadline:
            info = self.page.evaluate(
                """() => {
                    const el = document.querySelector('#toast-container .toast');
                    if (!el) return null;
                    const kind = el.className.includes('toast-success') ? 'success'
                               : el.className.includes('toast-error') ? 'error'
                               : el.className.includes('toast-warning') ? 'warning' : 'info';
                    const msg = el.querySelector('.toast-message');
                    return {kind: kind, text: (msg ? msg.innerText : el.innerText).trim()};
                }"""
            )
            if info:
                self.clear_toasts()
                return info["kind"], squash(info["text"])
            if self.page.locator(S.BOOTBOX).count():
                messages = self.drain_bootbox()
                if messages:
                    return "popup", " | ".join(messages)
            self.page.wait_for_timeout(200)
        return "", ""

    # ------------------------------------------------------------ select2 glue
    def select2_pick(
        self,
        rendered_selector: str,
        query: str,
        match: str,
        results_timeout: int = 15_000,
    ) -> str:
        """Open a select2, type `query`, click the option matching `match`.

        Returns the text of the option that was clicked.
        """
        if not self._visible(rendered_selector, timeout=self._short_timeout()):
            raise StepError(f"the dropdown {rendered_selector} is not on screen")
        self._click(rendered_selector)
        try:
            self.page.wait_for_selector(
                S.SELECT2_OPEN_SEARCH, timeout=self._short_timeout()
            )
        except PlaywrightTimeout as exc:
            raise StepError(
                f"the dropdown {rendered_selector} did not open"
            ) from exc
        field = self.page.locator(S.SELECT2_OPEN_SEARCH).first
        field.fill("")
        try:
            field.press_sequentially(query, delay=40)
        except AttributeError:  # playwright < 1.38
            field.type(query, delay=40)

        deadline = time.monotonic() + results_timeout / 1000
        options: list[str] = []
        while time.monotonic() < deadline:
            options = self.page.evaluate(
                """() => [...document.querySelectorAll(
                        '.select2-container--open li.select2-results__option')]
                    .filter(li => !li.classList.contains('loading-results'))
                    .map(li => li.innerText.trim())"""
            )
            if options and not any(
                option.lower().startswith(("searching", "mencari", "please enter"))
                for option in options
            ):
                break
            self.page.wait_for_timeout(200)

        index = _best_option(options, match)
        if index is None:
            self.close_select2()
            raise StepError(
                f"no option matching {match!r} for query {query!r} "
                f"(offered: {', '.join(options) or 'nothing'})"
            )

        chosen = options[index]
        self._pick_select2_option(index)
        self._settle(0.3)

        if self.page.locator(".select2-container--open").count():
            self.close_select2()
            raise StepError(f"the dropdown stayed open after picking {chosen!r}")
        return chosen

    def _pick_select2_option(self, index: int) -> None:
        """Click result `index`, working around anything overlapping the list.

        The page has a fixed navbar that can sit over the dropdown, so a plain
        click may be refused as intercepted. select2 v4 selects on `mouseup`,
        which is what the fallback dispatches.
        """
        option = self.page.locator(S.SELECT2_OPEN_RESULTS).nth(index)
        try:
            option.click(timeout=self._short_timeout())
            return
        except (PlaywrightTimeout, PlaywrightError):
            self.log.debug("select2 option click intercepted; dispatching mouseup")
        option.evaluate(
            "el => el.dispatchEvent(new MouseEvent('mouseup',"
            " {bubbles: true, cancelable: true, view: window}))"
        )

    # ------------------------------------------------------- steps 1, 2 (auth)
    def open_form(self) -> None:
        """STEP 1 - navigate to the EMR form page."""
        url = self.creds.form_url
        self.log.info("step 1: opening %s", url)
        self.page.goto(url, wait_until="domcontentloaded")

    def ensure_logged_in(self) -> None:
        """STEP 2 - log in when the login form is showing, then clear pop-ups."""
        self._settle(0.8)
        if self._login_form_showing():
            self._sign_in()
        else:
            self.log.info("step 2: already signed in")

        self.ensure_on_form()

        # "Set Default Jenis Pelayanan" appears right after login.
        self.dismiss_modal(S.MODAL_DEFAULT_PELAYANAN, timeout=4_000)
        self.drain_bootbox(timeout=800)

        try:
            self.page.wait_for_selector(S.PANEL_REGISTRASI, timeout=self.settings.timeout_ms)
        except PlaywrightTimeout as exc:
            raise FatalError(
                "the registration panel never appeared - is base_url pointing at the EMR?"
            ) from exc

    def _login_form_showing(self) -> bool:
        return self._visible(S.LOGIN_PASSWORD_VISIBLE, timeout=2_000)

    def _first_visible(self, candidates: tuple[str, ...], what: str):
        """Return the first candidate selector that matches something visible."""
        for selector in candidates:
            locator = self.page.locator(selector).first
            if locator.count() and locator.is_visible():
                return locator
        raise FatalError(
            f"could not find the {what} on {self.page.url} "
            f"(tried: {'; '.join(candidates)})"
        )

    def _sign_in(self) -> None:
        """Fill and submit the login form, then work out what the site did.

        doLogin() posts by AJAX and can answer in several ways, so the outcome
        has to be read off the page rather than assumed from a page load.
        """
        self.log.info("step 2: login form detected, signing in as %s", self.creds.username)
        login_path = urlparse(self.page.url).path

        self._first_visible(S.LOGIN_USERNAME_CANDIDATES, "username field").fill(
            self.creds.username
        )
        self._first_visible(S.LOGIN_PASSWORD_CANDIDATES, "password field").fill(
            self.creds.password
        )
        self._first_visible(S.LOGIN_SUBMIT_CANDIDATES, "Login button").click()

        # The takeover confirmation re-runs doLogin(), so allow a second pass.
        for _ in range(3):
            outcome, detail = self._await_login_outcome(login_path)

            if outcome == "signed-in":
                self.log.info("  signed in")
                return

            if outcome == "takeover":
                # Answering OK makes the page call doLogin() again.
                self.confirm_login_takeover(timeout=2_000)
                self._settle(1.0)
                continue

            if outcome == "rejected":
                raise FatalError(f"the EMR refused the login: {detail}")

            if outcome == "smf":
                self._dump_failure("login-smf")
                raise FatalError(
                    "login succeeded but the EMR is asking to 'Set Default DPJP' "
                    "before it will continue. Log in once by hand, choose the DPJP, "
                    "then re-run - or use --profile to keep that session."
                )

            if outcome in ("pakta", "kode-etik"):
                self._dump_failure(f"login-{outcome}")
                raise FatalError(
                    f"login is waiting on the {detail} agreement. Read and accept it "
                    "yourself by logging in once in a browser, then re-run."
                )

            if outcome == "stuck-loading":
                self._dump_failure("login-stuck")
                raise FatalError(
                    "the login request never came back (the spinner is still up) - "
                    "the EMR may be slow or unreachable; try again"
                )

            self._dump_failure("login-timeout")
            raise FatalError(
                f"the login did not resolve: still on {self.page.url} with no "
                f"message from the site. {detail}"
            )

        raise FatalError("the login kept restarting without completing")

    def _await_login_outcome(self, login_path: str) -> tuple[str, str]:
        """Poll until the login resolves. Returns (outcome, detail)."""
        deadline = time.monotonic() + self.settings.timeout_ms / 1000
        saw_loading = False

        while time.monotonic() < deadline:
            if urlparse(self.page.url).path != login_path:
                return "signed-in", self.page.url
            if not self.page.locator(S.LOGIN_PASSWORD_VISIBLE).count():
                return "signed-in", self.page.url

            if self.page.locator(f"{S.BOOTBOX}:visible").count():
                text = self._text(f"{S.BOOTBOX} .bootbox-body, {S.BOOTBOX} .modal-body")
                title = self._text(f"{S.BOOTBOX} .modal-title")
                if contains_keyword(f"{title} {text}", S.LOGIN_TAKEOVER_MARKER):
                    return "takeover", title or S.LOGIN_TAKEOVER_MARKER
                # status==2: the site's own explanation, e.g. a wrong password.
                self.drain_bootbox(timeout=500)
                return "rejected", text or title or "no message given"

            for selector, tag, label in (
                (S.MODAL_PILIH_DOKTER_SMF, "smf", "Set Default DPJP"),
                (S.MODAL_PAKTA_INTEGRITAS, "pakta", "Pakta Integritas"),
                (S.MODAL_KODE_ETIK, "kode-etik", "Kode Etik"),
            ):
                if self.page.locator(f"{selector}:visible").count():
                    return tag, label

            if self.page.locator(f"{S.LOGIN_LOADING}:visible").count():
                saw_loading = True

            self.page.wait_for_timeout(250)

        if saw_loading and self.page.locator(f"{S.LOGIN_LOADING}:visible").count():
            return "stuck-loading", "the loading spinner never cleared"
        return "timeout", "no pop-up, no redirect, no error message appeared"

    def _dump_failure(self, name: str) -> None:
        """Save a screenshot and the visible dialogs, to make this debuggable."""
        if self.debug_dir is None:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S")
            shot = self.debug_dir / f"{name}-{stamp}.png"
            self.page.screenshot(path=str(shot), full_page=True)

            visible = self.page.evaluate(
                """() => [...document.querySelectorAll('.modal, .bootbox')]
                    .filter(el => el.getClientRects().length > 0)
                    .map(el => ({id: el.id, cls: el.className,
                                 text: el.innerText.trim().slice(0, 300)}))"""
            )
            notes = self.debug_dir / f"{name}-{stamp}.txt"
            lines = [f"url: {self.page.url}", f"title: {self.page.title()}", ""]
            for modal in visible:
                lines.append(f"visible dialog id={modal['id']!r} class={modal['cls']!r}")
                lines.append(f"  {modal['text']}")
            notes.write_text("\n".join(lines), encoding="utf-8")
            self.log.error("  saved %s and %s", shot.name, notes.name)
        except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
            self.log.debug("could not write the failure dump", exc_info=True)

    def ensure_on_form(self, attempts: int = 2) -> None:
        """STEP 2 - the EMR lands on /main/info_rs after login; go to the form.

        Navigating straight there is also how a still-valid session skips the
        landing page entirely.
        """
        for attempt in range(attempts):
            if self._on_form():
                return
            self.log.info(
                "  landed on %s; going to %s", self.page.url, self.creds.form_url
            )
            try:
                self.page.goto(self.creds.form_url, wait_until="domcontentloaded")
            except PlaywrightError as exc:
                if attempt == attempts - 1:
                    raise FatalError(f"could not open {self.creds.form_url}: {exc}") from exc
                continue
            self._settle(1.0)

        if not self._on_form():
            raise FatalError(
                f"the EMR keeps redirecting away from {self.creds.form_url} "
                f"(now at {self.page.url}) - the login may not have taken effect"
            )

    def _on_form(self) -> bool:
        """Compare paths, not substrings: /nowhere/emr/form is not the form."""
        expected = urlparse(self.creds.form_url).path.rstrip("/")
        return urlparse(self.page.url).path.rstrip("/") == expected

    def confirm_login_takeover(self, timeout: int = 6_000) -> bool:
        """STEP 2 - answer "USER SUDAH LOGIN DI PERANGKAT LAIN" with "Ok, lanjut login".

        Confirming signs this user's other session out, which is why it is
        answered explicitly here rather than swept up by drain_bootbox().
        """
        if not self._visible(S.BOOTBOX, timeout=timeout):
            return False

        text = self._text(f"{S.BOOTBOX} .bootbox-body, {S.BOOTBOX} .modal-body")
        title = self._text(f"{S.BOOTBOX} .modal-title")
        if not contains_keyword(f"{title} {text}", S.LOGIN_TAKEOVER_MARKER):
            # Some other pop-up; let the generic handler deal with it.
            self.drain_bootbox(timeout=500)
            return False

        self.log.warning(
            "step 2: %s - continuing, which signs the other session out",
            title or "this user is already logged in on another device",
        )
        ok_button = (
            S.BOOTBOX_HANDLER_OK
            if self.page.locator(S.BOOTBOX_HANDLER_OK).count()
            else f"{S.BOOTBOX} .modal-footer button.btn-success"
        )
        self._click(ok_button)
        try:
            self.page.wait_for_selector(S.BOOTBOX, state="hidden", timeout=6_000)
        except PlaywrightTimeout:
            raise FatalError(
                "the 'sudah login di perangkat lain' confirmation would not close"
            ) from None
        self._settle(0.5)
        return True

    def verify_active_user(self) -> str:
        """STEP 2 - confirm the signed-in name matches the credentials."""
        self.active_user = self._text(S.ACTIVE_USER)
        if not self.active_user:
            raise FatalError("could not read the signed-in user name from the page")
        self.log.info("signed in as: %s", self.active_user)
        expected = self.creds.expected_user
        if expected and not names_match(expected, self.active_user):
            raise FatalError(
                f"signed-in user {self.active_user!r} does not match expected_user "
                f"{expected!r} in credentials.json"
            )
        if not expected:
            self.log.warning(
                "credentials.json has no 'expected_user'; skipping the identity check"
            )
        return self.active_user

    # ------------------------------------------------------------ step 3 (ward)
    def select_ward(self, ward: str) -> None:
        """STEP 3 - show all registrations for one ward."""
        if self.current_ward and fold(self.current_ward) == fold(ward):
            return
        self.log.info("step 3: filtering the registration list to %r", ward)

        radio = self.page.locator(S.RADIO_SEMUA).first
        if radio.count() and not radio.is_checked():
            self._click(S.RADIO_SEMUA)
            self._settle(0.3)

        try:
            chosen = self.select2_pick(S.SELECT_POLI_RENDERED, ward, ward)
        except StepError:
            chosen = self._select_ward_by_value(ward)
        self.log.info("  ward set to %r", chosen)

        self._wait_not_loading(f"{S.PANEL_REGISTRASI} .panel-body", "panel-loading")
        self.dismiss_modal(S.MODAL_RESUME_LOS, timeout=6_000)
        self.drain_bootbox(timeout=600)
        self.current_ward = ward

    def _select_ward_by_value(self, ward: str) -> str:
        """Fallback: drive the underlying <select> directly."""
        options = self.page.evaluate(
            "sel => [...document.querySelectorAll(sel + ' option')]"
            ".map(o => ({value: o.value, text: o.textContent.trim()}))",
            S.SELECT_POLI,
        )
        texts = [option["text"] for option in options]
        index = _best_option(texts, ward)
        if index is None:
            raise StepError(
                f"ward {ward!r} is not in the Poli/Ruang list "
                f"({len(texts)} options available)"
            )
        self._jquery(
            "$(arg.sel).val(arg.value).trigger('change');",
            {"sel": S.SELECT_POLI, "value": options[index]["value"]},
        )
        return texts[index]

    # --------------------------------------------------------- step 4 (patient)
    def open_patient(self, patient: Patient) -> None:
        """STEP 4 - search the list, open the chart, verify it is the right one."""
        term = patient.search_term
        self.log.info("step 4: searching the registration list for %r", term)

        search = self.page.locator(S.SEARCH_REGISTRASI).first
        search.click()
        search.fill(term)
        # DataTables filters on keyup in this build; fill() alone does not fire it.
        self.page.dispatch_event(S.SEARCH_REGISTRASI, "input")
        self.page.dispatch_event(S.SEARCH_REGISTRASI, "keyup")
        self._settle(0.8)

        rows = self.page.evaluate(
            """sel => [...document.querySelectorAll(sel + " tbody tr[role='row']")]
                .map((tr, i) => ({
                    index: i,
                    cells: [...tr.children].map(td => td.innerText.trim())
                }))""",
            S.TABLE_REGISTRASI,
        )
        candidates = [row for row in rows if len(row["cells"]) >= 7]
        index = _match_patient_row(candidates, patient)
        if index is None:
            listed = "; ".join(
                f"{row['cells'][5]} {row['cells'][6]}" for row in candidates[:5]
            )
            raise StepError(
                f"not found in the {patient.ruangan!r} registration list "
                f"(showing: {listed or 'no rows'}). Check the ward, the spelling, "
                "and the 'Hanya yang sudah dianamnesis' filter."
            )

        row = candidates[index]
        self.log.info("  opening chart: %s / %s", row["cells"][6], row["cells"][5])
        self.page.locator(f"{S.TABLE_REGISTRASI} tbody tr[role='row']").nth(
            row["index"]
        ).locator("td").first.dblclick()

        self._wait_for_chart(patient)

    def _wait_for_chart(self, patient: Patient) -> None:
        try:
            self.page.wait_for_selector(S.PASIEN_NO_RM, timeout=self.settings.timeout_ms)
        except PlaywrightTimeout as exc:
            raise StepError("the patient chart did not load") from exc

        deadline = time.monotonic() + self.settings.timeout_ms / 1000
        loaded_rm = ""
        while time.monotonic() < deadline:
            loaded_rm = self._text(S.PASIEN_NO_RM)
            if loaded_rm and (not patient.no_rm or loaded_rm == patient.no_rm):
                break
            self.page.wait_for_timeout(250)

        loaded_name = self._text(S.PASIEN_NAMA)
        loaded_ward = self._text(S.PASIEN_RUANG)

        if patient.no_rm and loaded_rm != patient.no_rm:
            raise StepError(
                f"opened chart has No. RM {loaded_rm!r} but the worklist says "
                f"{patient.no_rm!r} - refusing to write to the wrong record"
            )
        if patient.nama and not patient_names_match(patient.nama, loaded_name):
            raise StepError(
                f"opened chart is for {loaded_name!r} but the worklist says "
                f"{patient.nama!r} - refusing to write to the wrong record"
            )
        self.log.info(
            "  verified: %s / %s / %s", loaded_name, loaded_rm, loaded_ward or "?"
        )
        self.drain_bootbox(timeout=600)

    # ------------------------------------------------------------ step 5 (CPPT)
    def open_rppt_cppt(self) -> None:
        """STEP 5 - open the 'RPPT & CPPT' form for the open chart."""
        self.log.info("step 5: opening RPPT & CPPT")
        self.page.wait_for_selector(S.NAV_RPPT_CPPT, timeout=self.settings.timeout_ms)
        self._click(S.NAV_RPPT_CPPT)
        try:
            self.page.wait_for_selector(S.TABLE_CPPT, timeout=self.settings.timeout_ms)
        except PlaywrightTimeout as exc:
            raise StepError("the CPPT table never appeared") from exc
        self._settle(1.2)
        self._set_cppt_page_length()
        self._filter_cppt("")
        self.drain_bootbox(timeout=600)

    def _set_cppt_page_length(self) -> None:
        """Show as many CPPT rows per page as the table allows (default is 10)."""
        self._jquery(
            "const sel = $('select[name=\"tbl_cppt_length\"]');"
            " if (!sel.length) { return; }"
            " const values = sel.find('option').map((i, o) => parseInt(o.value, 10))"
            "   .get().filter(v => !isNaN(v));"
            " if (!values.length) { return; }"
            " const max = String(Math.max.apply(null, values));"
            " if (sel.val() !== max) { sel.val(max).trigger('change'); }"
        )
        self._settle(0.6)

    def _filter_cppt(self, text: str) -> None:
        """Type into the CPPT table's own Search box."""
        search = self.page.locator(S.SEARCH_CPPT).first
        if not search.count():
            return
        if search.input_value() == text:
            return
        search.fill(text)
        self.page.dispatch_event(S.SEARCH_CPPT, "input")
        self.page.dispatch_event(S.SEARCH_CPPT, "keyup")
        self._settle(0.6)

    def read_cppt_rows(self) -> list[dict]:
        return self.page.evaluate(
            """sel => [...document.querySelectorAll(sel + " tbody tr[role='row']")]
                .map(tr => {
                    const cells = [...tr.children].map(td => td.innerText.trim());
                    const edit = tr.querySelector('button.cppt-btn-edit');
                    const impl = tr.querySelector('button.cppt-btn-implementasi');
                    const seen = el => !!el && el.getClientRects().length > 0;
                    return {
                        cells: cells,
                        text: tr.innerText,
                        cpptId: edit ? (edit.dataset.cpptId || '') : '',
                        instruksiId: impl ? (impl.dataset.iId || '') : '',
                        editVisible: seen(edit),
                        implVisible: seen(impl)
                    };
                })""",
            S.TABLE_CPPT,
        )

    def find_cppt_row(self, keyword: str, attempts: int = 3) -> CpptRow:
        """STEP 5 - locate today's CPPT row whose text contains `keyword`.

        Retries, because the table is redrawn asynchronously after a save.
        """
        last: StepError | None = None
        for attempt in range(attempts):
            try:
                return self._find_cppt_row_once(keyword)
            except StepError as exc:
                last = exc
                if attempt >= attempts - 1:
                    break
                if "no CPPT row" in str(exc):
                    # The row may be on another page; let the table filter for us.
                    self._filter_cppt(keyword)
                self._settle(1.2)
        raise last  # type: ignore[misc]

    def _find_cppt_row_once(self, keyword: str) -> CpptRow:
        rows = self.read_cppt_rows()
        usable = [row for row in rows if row["editVisible"] or row["implVisible"]]
        matched = [row for row in usable if contains_keyword(row["text"], keyword)]
        if not matched:
            raise StepError(
                f"no CPPT row containing {keyword!r} "
                f"({len(usable)} row(s) on the current page)"
            )

        dated = [
            row
            for row in matched
            if not self.settings.require_today
            or is_today(row["cells"][S.COL_CPPT_TGL] if len(row["cells"]) > 1 else "")
        ]
        if not dated:
            seen = ", ".join(
                row["cells"][S.COL_CPPT_TGL] for row in matched if len(row["cells"]) > 1
            )
            raise StepError(
                f"found {len(matched)} row(s) for {keyword!r} but none dated today "
                f"(dates seen: {seen or 'unknown'})"
            )
        if len(dated) > 1:
            self.log.warning(
                "  %d CPPT rows match %r today; using the topmost", len(dated), keyword
            )

        row = dated[0]
        cells = row["cells"]
        chosen = CpptRow(
            cppt_id=row["cpptId"],
            instruksi_id=row["instruksiId"],
            tgl=cells[S.COL_CPPT_TGL] if len(cells) > 1 else "",
            ppa=cells[S.COL_CPPT_PPA] if len(cells) > 2 else "",
            asesmen=cells[S.COL_CPPT_ASESMEN] if len(cells) > 3 else "",
        )
        self.log.info("  matched CPPT row %s (%s)", chosen.tgl, chosen.cppt_id or "no id")
        return chosen

    def touch_subyektif(self, row: CpptRow) -> bool:
        """STEP 5 - append a blank line to Subyektif and re-save the CPPT.

        Existing text is never modified: we only append a trailing newline.
        """
        if not row.cppt_id:
            raise StepError("the matched CPPT row has no 'Ubah' button")

        self.log.info("step 5: re-saving the CPPT (append a blank line to Subyektif)")
        self._click(row.edit_selector)  # fires a native confirm(), auto-accepted
        self._settle(0.5)
        self.drain_bootbox(timeout=800)

        try:
            self.page.wait_for_selector(S.TXT_SUBYEKTIF, state="visible", timeout=self._short_timeout())
        except PlaywrightTimeout as exc:
            raise StepError("the CPPT edit form did not open") from exc

        # Wait for loadDetil() to populate the form before touching it.
        deadline = time.monotonic() + self.settings.timeout_ms / 1000
        before = ""
        while time.monotonic() < deadline:
            before = self.page.locator(S.TXT_SUBYEKTIF).first.input_value()
            enabled = self.page.locator(S.BTN_CPPT_SIMPAN).first.is_enabled()
            if before.strip() and enabled:
                break
            self.page.wait_for_timeout(250)

        if not before.strip():
            raise StepError("the Subyektif box is still empty - the CPPT did not load")

        after = before + "\n"
        try:
            self.page.locator(S.TXT_SUBYEKTIF).first.fill(after)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise StepError(f"could not type into the Subyektif box: {exc}") from exc
        self._jquery(
            "$(arg).trigger('change').trigger('paste.cppt');", S.TXT_SUBYEKTIF
        )
        self._settle(0.3)

        readback = self.page.locator(S.TXT_SUBYEKTIF).first.input_value()
        if not readback.startswith(before):
            raise StepError("Subyektif text changed unexpectedly - not saving")

        if self.dry_run:
            self.log.info("  [dry-run] would click 'Simpan CPPT'")
            return False

        self._confirm_save("Simpan CPPT")
        self.clear_toasts()
        self._click(S.BTN_CPPT_SIMPAN)
        kind, message = self.wait_for_toast()
        if kind != "success":
            raise StepError(f"saving the CPPT failed: {message or 'no response'}")
        self.log.info("  %s", message)
        self._settle(1.0)
        self.drain_bootbox(timeout=600)
        return True

    def verify_cppt_updater(self, row: CpptRow) -> bool:
        """STEP 5 - the row's 'User Update' should now be the signed-in user."""
        updater = ""
        for line in row.ppa.splitlines():
            line = squash(line)
            if line and not line.lower().startswith(("user insert", "user update")):
                updater = line
        if names_match(self.active_user, row.ppa):
            self.log.info("  CPPT now shows %s as the last updater", self.active_user)
            return True
        self.log.warning(
            "  the CPPT 'User Update' (%r) does not look like %r",
            updater or row.ppa,
            self.active_user,
        )
        return False

    # ---------------------------------------------------- steps 6, 7 (implement)
    def open_implementasi(self, row: CpptRow) -> None:
        """STEP 6 - open the 'Status Implementasi' modal for the CPPT row."""
        if not row.instruksi_id:
            raise StepError("the matched CPPT row has no 'Implementasi' button")
        self.log.info("step 6: opening the Implementasi modal")
        self._click(row.implementasi_selector)
        try:
            self.page.wait_for_selector(
                f"{S.MODAL_IMPLEMENTASI}.in", timeout=self.settings.timeout_ms
            )
        except PlaywrightTimeout as exc:
            raise StepError("the Implementasi modal did not open") from exc
        self._wait_not_loading(f"{S.MODAL_IMPLEMENTASI} .modal-body", "show-loading")
        self._settle(0.6)
        self.drain_bootbox(timeout=600)

    def existing_implementasi(self) -> str:
        """Text already recorded in the modal's 'Catatan Implementasi' table."""
        return self.page.evaluate(
            "sel => { const el = document.querySelector(sel);"
            " return el ? el.innerText : ''; }",
            S.TABLE_IMPL,
        )

    def _impl_date_value(self) -> str:
        return self.page.locator(S.INPUT_IMPL_TGL).first.input_value().strip()

    def set_implementasi_date(self) -> str:
        """STEP 7 - stamp the Tanggal field with 'now'.

        The primary path is the double-click on the calendar addon from the
        instructions. The fallbacks exist because the field is readonly, so only
        the datetimepicker widget normally writes to it.
        """
        self.page.locator(S.SPAN_IMPL_TGL).first.dblclick()
        self._settle(0.4)
        value = self._impl_date_value()

        if not value:
            self._jquery(
                "const dp = $(arg).data('DateTimePicker');"
                " const now = window.moment ? window.moment() : new Date();"
                " if (dp && dp.setValue) { dp.setValue(now); }"
                " else if (dp && dp.date) { dp.date(now); }"
                " $(arg).trigger('dp.change');",
                S.DIV_IMPL_TGL,
            )
            self._settle(0.4)
            value = self._impl_date_value()

        if not value:
            stamp = datetime.now().strftime("%d-%m-%Y %H:%M")
            self._jquery(
                "$(arg.input).val(arg.stamp).trigger('change');"
                " $(arg.container).trigger('dp.change');",
                {"input": S.INPUT_IMPL_TGL, "container": S.DIV_IMPL_TGL, "stamp": stamp},
            )
            self._settle(0.4)
            value = self._impl_date_value()

        # An open picker would swallow the next click.
        self.close_datepicker(S.DIV_IMPL_TGL)
        self._settle(0.2)

        if not value:
            raise StepError("could not set the Tanggal field")
        return value

    def add_tindakan(self, code: str) -> TindakanResult:
        """STEP 7 - record one tindakan and save it."""
        self.log.info("step 7: tindakan %s", code)

        if self.settings.skip_existing and code in self.existing_implementasi():
            self.log.info("  already recorded for this instruksi - skipping")
            return TindakanResult(code, "skipped", "already in Catatan Implementasi")

        # The tindakan lookup sends the Tanggal field as `tanggal_tindakan`,
        # so the date has to be stamped before the search runs.
        tanggal = self.set_implementasi_date()
        self.log.info("  tanggal: %s", tanggal)

        try:
            chosen = self.select2_pick(S.SELECT_TINDAKAN_RENDERED, code, code)
        except StepError as exc:
            return TindakanResult(code, "failed", str(exc))
        self.log.info("  selected: %s", chosen)

        # getTarifTindakan() runs on change and may reject the tindakan.
        self._settle(1.0)
        popups = self.drain_bootbox(timeout=800)
        if popups:
            return TindakanResult(code, "failed", " | ".join(popups))
        if not self.page.locator(S.SELECT_TINDAKAN).first.input_value():
            return TindakanResult(code, "failed", "the tindakan was cleared by the form")

        self.page.select_option(S.SELECT_IMPL_STATUS, self.settings.status)
        self.log.info("  status: %s", S.STATUS_LABELS[self.settings.status])

        keterangan = self.page.locator(S.TXT_IMPL_KET).first.input_value()
        if not keterangan.strip():
            self.page.locator(S.TXT_IMPL_KET).first.fill(chosen)
            keterangan = chosen
        self.log.info("  keterangan: %s", keterangan)

        if self.dry_run:
            self.log.info("  [dry-run] would click 'Simpan'")
            return TindakanResult(code, "dry-run", chosen)

        self._confirm_save(f"Simpan implementasi {code}")
        self.clear_toasts()
        self._click(S.BTN_IMPL_SAVE)
        kind, message = self.wait_for_toast()
        self.drain_bootbox(timeout=1_500)
        if kind != "success":
            return TindakanResult(code, "failed", message or "no response after Simpan")
        self.log.info("  %s", message)
        self._wait_not_loading(f"{S.MODAL_IMPLEMENTASI} .modal-body", "show-loading")
        self._settle(0.6)
        return TindakanResult(code, "saved", chosen)

    def close_implementasi(self) -> None:
        """STEP 7 - close the modal with 'Tutup'."""
        self.close_select2()
        if self._visible(S.MODAL_IMPLEMENTASI, timeout=1_000) and self.page.locator(
            S.BTN_IMPL_CLOSE
        ).count():
            self._click(S.BTN_IMPL_CLOSE)
            try:
                self.page.wait_for_selector(
                    S.MODAL_IMPLEMENTASI, state="hidden", timeout=3_000
                )
            except PlaywrightTimeout:
                pass
        self.dismiss_modal(S.MODAL_IMPLEMENTASI, timeout=1_500)
        self.drain_bootbox(timeout=600)

    # ------------------------------------------------------------- supervision
    def _confirm_save(self, what: str) -> None:
        if not self.settings.confirm_each_save:
            return
        self.log.info("  paused - about to: %s", what)
        input(f"    press Enter to {what}, or Ctrl+C to stop: ")


def _match_patient_row(rows: list[dict], patient: Patient) -> int | None:
    """Find the registration row for `patient`. MR number wins over the name."""
    if patient.no_rm:
        for index, row in enumerate(rows):
            if squash(row["cells"][5]) == patient.no_rm:
                return index
    if patient.nama:
        target = fold(patient.nama)
        for index, row in enumerate(rows):
            if fold(row["cells"][6]) == target:
                return index
        for index, row in enumerate(rows):
            if patient_names_match(patient.nama, row["cells"][6]):
                return index
    return None
