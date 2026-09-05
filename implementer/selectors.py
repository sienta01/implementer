"""Every DOM selector the automation depends on, in one place.

The EMR is a server-rendered Bootstrap 3 + jQuery app. When the markup changes,
this is the only module that should need editing.
"""

# --- Step 1/2: login + landing ------------------------------------------------
FORM_PATH = "/emr/form"

# Tried in order, first visible match wins. A comma-separated CSS list would
# not work here: Playwright's .first is first in DOM order, not first listed,
# and the login page carries several hidden modals with their own inputs and
# btn-success buttons.
LOGIN_USERNAME_CANDIDATES = (
    "#username",
    "input[name='username']",
    "#loginForm input[type='text']",
    "input[name='user'], input[name='login'], input[name='nip']",
)
LOGIN_PASSWORD_CANDIDATES = (
    "#password",
    "input[name='password']",
    "#loginForm input[type='password']",
)
LOGIN_SUBMIT_CANDIDATES = (
    "#submitLogin",
    "button.btn-login",
    "#loginForm button[type='submit']",
    "button[type='submit'], input[type='submit']",
)

# Used to decide whether we are still sitting on the login page.
LOGIN_PASSWORD_VISIBLE = "#password:visible, #loginForm input[type='password']:visible"

# doLogin() is an AJAX post; this spinner is up while it is in flight.
LOGIN_LOADING = "#loading"

# Shown on success when the account belongs to an SMF: login stops here.
MODAL_PILIH_DOKTER_SMF = "#modalPilihDokterSMF"
# Agreements the person has to accept themselves.
MODAL_PAKTA_INTEGRITAS = "#myModalPaktaIntegritas"
MODAL_KODE_ETIK = "#myModalKodeEtik"

# "Set Default Jenis Pelayanan" pop-up shown right after login.
MODAL_DEFAULT_PELAYANAN = "#modalDefaultPelayanan"

# Name of the logged-in user, shown in the doctor panel.
ACTIVE_USER = "#panel-data-dokter .panel-title span"

# --- Step 3: registration list -----------------------------------------------
PANEL_REGISTRASI = "#panel-data-registrasi"
RADIO_SEMUA = "input.trigger-filter-list-registrasi[name='status_dilayani'][value='-1']"
CHECK_ANAMNESIS = "input.trigger-filter-list-registrasi[name='is_anamnesis'][type='checkbox']"

SELECT_POLI = "#filter-reg-mins-kode"
SELECT_POLI_RENDERED = "#select2-filter-reg-mins-kode-container"

MODAL_RESUME_LOS = "#modalResumeLos"

# --- Step 4: pick the patient -------------------------------------------------
TABLE_REGISTRASI = "#tbl-data-registrasi"
SEARCH_REGISTRASI = "#tbl-data-registrasi_filter input[type='search']"
ROWS_REGISTRASI = "#tbl-data-registrasi tbody tr[role='row']"

# Patient header, used to confirm we opened the right chart.
PASIEN_NO_RM = "#frm-data-pasien .pasien-no-rm"
PASIEN_NAMA = "#frm-data-pasien .pasien-nama"
PASIEN_RUANG = "#frm-data-pasien .pasien-poliruang"
FORM_PASIEN_NO_RM = "#form-pasien-no-rm"

# --- Step 5: RPPT & CPPT ------------------------------------------------------
NAV_RPPT_CPPT = "#tree-registrasi li[data-url='/emr/form/rppt_cppt'] .branch"
TABLE_CPPT = "#tbl_cppt"
ROWS_CPPT = "#tbl_cppt tbody tr[role='row']"
SEARCH_CPPT = "#tbl_cppt_filter input[type='search']"
BTN_CPPT_EDIT = "button.cppt-btn-edit"
BTN_CPPT_IMPLEMENTASI = "button.cppt-btn-implementasi"

TXT_SUBYEKTIF = "#cppt-form-det-subyektif"
BTN_CPPT_SIMPAN = "#cppt-btn-simpan"

# Column indexes of #tbl_cppt (0-based).
COL_CPPT_NO = 0
COL_CPPT_TGL = 1
COL_CPPT_PPA = 2
COL_CPPT_ASESMEN = 3
COL_CPPT_IMPLEMENTASI = 8
COL_CPPT_MANAGE = 9

# --- Step 6/7: implementasi modal --------------------------------------------
MODAL_IMPLEMENTASI = "#cppt-modal-implementasi"
SELECT_TINDAKAN = "#cppt-kartu-kendali-tindakan"
SELECT_TINDAKAN_RENDERED = "#select2-cppt-kartu-kendali-tindakan-container"
INPUT_IMPL_TGL = "#cppt-implementasi-tgl"
SPAN_IMPL_TGL = "#span-cppt-implementasi-tgl"
DIV_IMPL_TGL = "#div-cppt-implementasi-tgl"
SELECT_IMPL_STATUS = "#cppt-implementasi-status"
TXT_IMPL_KET = "#cppt-implementasi-ket"
BTN_IMPL_SAVE = "#cppt-implementasi-btn-save"
BTN_IMPL_CLOSE = "#cppt-modal-implementasi .modal-footer button[data-dismiss='modal']"
TABLE_IMPL = "#tbl-cppt-implementasi"

# --- Generic widgets ----------------------------------------------------------
SELECT2_OPEN_SEARCH = ".select2-container--open input.select2-search__field"
SELECT2_OPEN_RESULTS = ".select2-container--open li.select2-results__option"
SELECT2_LOADING = ".select2-container--open li.loading-results"

TOAST_MESSAGE = ".toast-message"
TOAST_SUCCESS = ".toast-success .toast-message"
TOAST_ERROR = ".toast-error .toast-message"

BOOTBOX = ".bootbox.modal.in"
BOOTBOX_OK = ".bootbox.modal.in .modal-footer button"
# bootbox tags its buttons, which is more reliable than going by CSS class.
BOOTBOX_HANDLER_OK = ".bootbox.modal.in .modal-footer button[data-bb-handler='ok']"

# Confirm shown at login when the same user has a session on another device.
# Its OK button is "Ok, lanjut login", which signs that other session out.
LOGIN_TAKEOVER_MARKER = "login di perangkat yang lain"

# Status values of #cppt-implementasi-status
STATUS_BELUM = "0"
STATUS_SEBAGIAN = "1"
STATUS_TIDAK = "2"
STATUS_SELESAI = "3"

STATUS_LABELS = {
    "0": "Belum dilakukan",
    "1": "Selesai Sebagian",
    "2": "Tidak Dilakukan",
    "3": "Selesai Dilakukan",
}
