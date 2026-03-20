import sys
import types
import importlib.util
import subprocess
import shutil
import requests
import os
import json
import base64
import time
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
APP_PATH = BASE_DIR / "3appDays.py"
ARCHIVE_DIR = BASE_DIR / "archives"

GITHUB_OWNER = "arabsnipertech-bet"
GITHUB_REPO = "arabsniper"
GITHUB_BRANCH = "main"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"


# =========================
# FAKE STREAMLIT
# =========================
class SessionState(dict):
    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value


class DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def progress(self, *args, **kwargs):
        return self

    def empty(self):
        return None

    def write(self, *args, **kwargs):
        return None

    def markdown(self, *args, **kwargs):
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def success(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def button(self, *args, **kwargs):
        return False

    def download_button(self, *args, **kwargs):
        return False

    def subheader(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def header(self, *args, **kwargs):
        return None

    def selectbox(self, label, options=None, index=0, **kwargs):
        if options is None:
            return None
        if len(options) == 0:
            return None
        return options[index] if len(options) > index else options[0]

    def multiselect(self, label, options=None, default=None, **kwargs):
        return default or []


class FakeSidebar(DummyContext):
    pass


class FakeSecrets(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeStreamlitModule(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = SessionState()
        self.sidebar = FakeSidebar()
        self.secrets = FakeSecrets()

    def set_page_config(self, *args, **kwargs):
        return None

    def spinner(self, *args, **kwargs):
        return DummyContext()

    def progress(self, *args, **kwargs):
        return DummyContext()

    def columns(self, spec, **kwargs):
        if isinstance(spec, int):
            n = spec
        else:
            n = len(spec)
        return [DummyContext() for _ in range(n)]

    def button(self, *args, **kwargs):
        return False

    def markdown(self, *args, **kwargs):
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def success(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def subheader(self, *args, **kwargs):
        return None

    def write(self, *args, **kwargs):
        return None

    def rerun(self):
        return None

    def download_button(self, *args, **kwargs):
        return False

    def dialog(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator


fake_st = FakeStreamlitModule()
sys.modules["streamlit"] = fake_st


# =========================
# IMPORT DINAMICO DI 3appDays.py
# =========================
spec = importlib.util.spec_from_file_location("app3days_module", APP_PATH)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


# =========================
# HELPERS
# =========================
LIVE_FILES = [
    "data.json",
    "data_day1.json",
    "data_day2.json",
    "data_day3.json",
    "data_day4.json",
    "data_day5.json",
    "details_day1.json",
    "details_day2.json",
    "details_day3.json",
    "details_day4.json",
    "details_day5.json",
    "quote_history.json",
]

SYNC_FILES = [
    "data.json",
    "data_day1.json",
    "data_day2.json",
    "data_day3.json",
    "data_day4.json",
    "data_day5.json",
    "details_day1.json",
    "details_day2.json",
    "details_day3.json",
    "details_day4.json",
    "details_day5.json",
]


def archive_live_files():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = ARCHIVE_DIR / ts
    target.mkdir(parents=True, exist_ok=True)

    copied = 0
    for name in LIVE_FILES:
        src = BASE_DIR / name
        if src.exists():
            shutil.copy2(src, target / name)
            copied += 1

    print(f"📦 Backup creato in: {target}", flush=True)
    print(f"📦 File copiati: {copied}", flush=True)


def github_headers():
    token = os.getenv("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arabsniper-runner"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_github_file(path: str) -> str:
    url = f"{GITHUB_API}/{path}"
    params = {"ref": GITHUB_BRANCH}
    r = requests.get(url, headers=github_headers(), params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()

    if "content" not in payload:
        raise RuntimeError(f"Contenuto mancante per {path}")

    content = payload["content"].replace("\n", "")
    return base64.b64decode(content).decode("utf-8")


def read_json_safe(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json_atomic(path: Path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")
    # validazione prima del replace
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(path)


def export_local_day_outputs(days=None, update_main_from_day1=True):
    if days is None:
        days = [1, 2, 3, 4, 5]

    for day_num in days:
        try:
            day_results = app.build_day_results(day_num)
            details_payload = app.build_day_details_payload(day_num)

            write_json_atomic(BASE_DIR / f"data_day{day_num}.json", day_results)
            write_json_atomic(BASE_DIR / f"details_day{day_num}.json", details_payload)
            print(
                f"💾 Export locale day{day_num}: data={len(day_results)} | details={len(details_payload.get('details', {}))}",
                flush=True,
            )

            if day_num == 1 and update_main_from_day1:
                write_json_atomic(BASE_DIR / "data.json", day_results)
                print("💾 Export locale data.json da day1", flush=True)
        except Exception as e:
            print(f"⚠️ Export locale fallito per day{day_num}: {e}", flush=True)


def expected_day1_date() -> str:
    return app.get_target_dates()[0]


def is_day1_synced_correctly() -> bool:
    p = BASE_DIR / "data_day1.json"
    data = read_json_safe(p)
    if not isinstance(data, list) or not data:
        return False

    expected = expected_day1_date()
    first = data[0]
    actual = str(first.get("Data", "")).strip()
    return actual == expected


def is_non_empty_text(text: str) -> bool:
    return bool(str(text or "").strip())


def validate_remote_json_text(name: str, text: str):
    if not is_non_empty_text(text):
        return False, "contenuto vuoto"

    try:
        obj = json.loads(text)
    except Exception as e:
        return False, f"json non valido: {e}"

    if name.startswith("data"):
        if not isinstance(obj, list):
            return False, "data file non è una lista"
        if name in ("data.json", "data_day1.json", "data_day2.json", "data_day3.json", "data_day4.json") and len(obj) == 0:
            return False, "data file vuoto"
        return True, "ok"

    if name.startswith("details"):
        if not isinstance(obj, dict):
            return False, "details file non è un oggetto"
        if "details" not in obj:
            return False, "chiave 'details' mancante"
        if not isinstance(obj.get("details"), dict):
            return False, "campo 'details' non è un dict"
        return True, "ok"

    return True, "ok"


def sync_remote_outputs_to_local(max_attempts=6, wait_seconds=10):
    print("🔄 Sincronizzo i file remoti GitHub nel workspace locale...", flush=True)

    expected = expected_day1_date()
    print(f"📅 Attendo day1 coerente con data: {expected}", flush=True)

    for attempt in range(1, max_attempts + 1):
        print(f"🔁 Tentativo sync {attempt}/{max_attempts}", flush=True)

        all_ok = True

        for name in SYNC_FILES:
            dest = BASE_DIR / name
            tmp_dest = BASE_DIR / f"{name}.tmp"

            try:
                text = fetch_github_file(name)
                is_valid, reason = validate_remote_json_text(name, text)
                if not is_valid:
                    print(f"⚠️ Sync scartata per {name}: {reason}", flush=True)
                    continue

                tmp_dest.write_text(text, encoding="utf-8")
                tmp_obj = read_json_safe(tmp_dest)
                if tmp_obj is None:
                    print(f"⚠️ Sync scartata per {name}: file tmp non leggibile", flush=True)
                    try:
                        tmp_dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue

                tmp_dest.replace(dest)
                print(f"✅ Sync locale: {name}", flush=True)

            except Exception as e:
                all_ok = False
                print(f"⚠️ Sync fallita per {name}: {e}", flush=True)
            finally:
                try:
                    if tmp_dest.exists():
                        tmp_dest.unlink()
                except Exception:
                    pass

        if is_day1_synced_correctly():
            print("✅ Sync completata: data_day1.json contiene la data corretta.", flush=True)
            return True

        actual = ""
        p = BASE_DIR / "data_day1.json"
        data = read_json_safe(p)
        if isinstance(data, list) and data:
            actual = str(data[0].get("Data", "")).strip()

        print(f"⚠️ Data day1 non ancora coerente. Attesa: {expected} | Trovata: {actual or 'N/D'}", flush=True)

        if attempt < max_attempts:
            time.sleep(wait_seconds)

    print("❌ Sync remota fallita o day1 ancora incoerente dopo tutti i tentativi.", flush=True)
    return False


def run_quote_history(days: str, label: str):
    cmd = [
        sys.executable,
        "-u",
        str(BASE_DIR / "quote_history_updater.py"),
        "--days",
        days,
        "--label",
        label,
    ]
    print(f"🧠 Aggiorno quote_history: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=BASE_DIR, check=False)


def run_night():
    print("🌙 RUNNER: backup file live prima del night scan...", flush=True)
    archive_live_files()

    print("🌙 RUNNER: avvio build multi-day notturna...", flush=True)
    app.run_nightly_multiday_build()
    print("✅ RUNNER: build multi-day completata.", flush=True)

    # NUOVO: esporta SEMPRE i file day in locale prima della sync remota
    export_local_day_outputs(days=[1, 2, 3, 4, 5], update_main_from_day1=True)

    sync_remote_outputs_to_local()
    run_quote_history("1,2,3,4,5", "night")


def run_mid_day1():
    print("☀️ RUNNER: refresh veloce day1...", flush=True)
    app.run_full_scan(horizon=1, snap=False, update_main_site=True, show_success=False)
    export_local_day_outputs(days=[1], update_main_from_day1=True)
    sync_remote_outputs_to_local(max_attempts=4, wait_seconds=5)
    run_quote_history("1", "mid-day1")


def run_evening_multi():
    print("🌆 RUNNER: refresh serale multi-day (day1-day4)...", flush=True)
    app.run_full_scan(horizon=1, snap=False, update_main_site=True, show_success=False)
    app.run_full_scan(horizon=2, snap=False, update_main_site=False, show_success=False)
    app.run_full_scan(horizon=3, snap=False, update_main_site=False, show_success=False)
    app.run_full_scan(horizon=4, snap=False, update_main_site=False, show_success=False)
    export_local_day_outputs(days=[1, 2, 3, 4], update_main_from_day1=True)
    sync_remote_outputs_to_local(max_attempts=4, wait_seconds=5)
    run_quote_history("1,2,3,4", "evening-multi")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "--night":
        run_night()
    elif arg == "--mid-day1":
        run_mid_day1()
    elif arg == "--evening-multi":
        run_evening_multi()
    else:
        print("❌ Argomento non valido. Usa: --night | --mid-day1 | --evening-multi", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
