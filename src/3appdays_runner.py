import os
import sys
import json
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

try:
    from github import Github
except Exception:
    Github = None


# =========================================================
# PATHS
# =========================================================
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"

ENGINE_FILE = SRC_DIR / "3appdays.py"

RUN_STATE_FILE = DATA_DIR / "run_state.json"
LAST_FAST_UPDATE_FILE = DATA_DIR / "last_fast_update.json"

REMOTE_LAST_FAST_UPDATE_FILE = "data/last_fast_update.json"

REPO_NAME = "dweezil78/arabsniper2"


# =========================================================
# LOG
# =========================================================
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# =========================================================
# FILE UTILS
# =========================================================
def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_write_json(path: Path, payload) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def save_run_state(payload: dict) -> None:
    safe_write_json(RUN_STATE_FILE, payload)


# =========================================================
# GITHUB HELPERS
# =========================================================
def get_github_token():
    token = os.getenv("GITHUB_TOKEN")
    if token:
        return token

    # fallback semplice facoltativo: GitHub Actions o env locale
    return None


def github_write_json(filename: str, payload, commit_message: str) -> str:
    if Github is None:
        return "PYGITHUB_NOT_AVAILABLE"

    token = get_github_token()
    if not token:
        return "MISSING_TOKEN"

    try:
        g = Github(token)
        repo = g.get_repo(REPO_NAME)
        content_str = json.dumps(payload, indent=2, ensure_ascii=False)

        try:
            contents = repo.get_contents(filename)
            repo.update_file(contents.path, commit_message, content_str, contents.sha)
            return "SUCCESS"
        except Exception:
            try:
                repo.create_file(filename, commit_message, content_str)
                return "SUCCESS"
            except Exception as e_create:
                return f"CREATE_FAILED: {e_create}"

    except Exception as e:
        return f"GITHUB_ERROR: {e}"


def update_last_fast_update(mode: str, command: str, returncode: int) -> dict:
    payload = {
        "last_update": now_iso(),
        "mode": mode,
        "command": command,
        "returncode": returncode,
        "source": "src/3appdays_runner.py",
    }

    # salva locale
    safe_write_json(LAST_FAST_UPDATE_FILE, payload)

    # prova upload GitHub
    gh_status = github_write_json(
        REMOTE_LAST_FAST_UPDATE_FILE,
        payload,
        f"Update last_fast_update ({mode})"
    )
    payload["github_status"] = gh_status

    # risalva con github_status incluso
    safe_write_json(LAST_FAST_UPDATE_FILE, payload)

    return payload


# =========================================================
# ENGINE EXECUTION
# =========================================================
def build_engine_command(mode: str):
    python_exe = sys.executable or "python"

    if mode == "night":
        return [python_exe, str(ENGINE_FILE), "--auto"]

    if mode == "fast":
        return [python_exe, str(ENGINE_FILE), "--fast"]

    if mode == "day2-refresh":
        return [python_exe, str(ENGINE_FILE), "--day2-refresh"]

    raise ValueError(f"Modalità non valida: {mode}")


def run_engine(mode: str) -> dict:
    if not ENGINE_FILE.exists():
        raise FileNotFoundError(f"Motore non trovato: {ENGINE_FILE}")

    cmd = build_engine_command(mode)
    cmd_str = " ".join(cmd)

    log(f"ENGINE START -> {cmd_str}")

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True
    )

    log(f"ENGINE END -> rc={result.returncode}")

    if result.stdout:
        print("\n========== STDOUT ==========")
        print(result.stdout)

    if result.stderr:
        print("\n========== STDERR ==========")
        print(result.stderr)

    return {
        "mode": mode,
        "command": cmd_str,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "status": "ok" if result.returncode == 0 else "error",
        "generated_at": now_iso(),
    }


# =========================================================
# WORKFLOWS
# =========================================================
def run_night_workflow() -> None:
    ensure_directories()

    log("=====================================")
    log("NIGHT WORKFLOW START")
    log("=====================================")

    report = run_engine("night")
    last_update_payload = update_last_fast_update(
        mode="night",
        command=report["command"],
        returncode=report["returncode"]
    )

    save_run_state({
        "last_run_type": "night",
        "generated_at": now_iso(),
        "engine_report": report,
        "last_fast_update": last_update_payload,
    })

    if report["returncode"] != 0:
        log("NIGHT WORKFLOW END -> ERRORE")
        raise SystemExit(1)

    log("NIGHT WORKFLOW END -> OK")
    log("=====================================")


def run_fast_workflow() -> None:
    ensure_directories()

    log("=====================================")
    log("FAST WORKFLOW START")
    log("=====================================")

    report = run_engine("fast")
    last_update_payload = update_last_fast_update(
        mode="fast",
        command=report["command"],
        returncode=report["returncode"]
    )

    save_run_state({
        "last_run_type": "fast",
        "generated_at": now_iso(),
        "engine_report": report,
        "last_fast_update": last_update_payload,
    })

    if report["returncode"] != 0:
        log("FAST WORKFLOW END -> ERRORE")
        raise SystemExit(1)

    log("FAST WORKFLOW END -> OK")
    log("=====================================")


def run_day2_refresh_workflow() -> None:
    ensure_directories()

    log("=====================================")
    log("DAY2 REFRESH WORKFLOW START")
    log("=====================================")

    report = run_engine("day2-refresh")
    last_update_payload = update_last_fast_update(
        mode="day2-refresh",
        command=report["command"],
        returncode=report["returncode"]
    )

    save_run_state({
        "last_run_type": "day2-refresh",
        "generated_at": now_iso(),
        "engine_report": report,
        "last_fast_update": last_update_payload,
    })

    if report["returncode"] != 0:
        log("DAY2 REFRESH WORKFLOW END -> ERRORE")
        raise SystemExit(1)

    log("DAY2 REFRESH WORKFLOW END -> OK")
    log("=====================================")


# =========================================================
# CLI
# =========================================================
def parse_args():
    parser = argparse.ArgumentParser(description="ArabSniper runner compatibile col motore attuale")
    parser.add_argument("--night", action="store_true", help="Lancia 3appdays.py --auto")
    parser.add_argument("--fast", action="store_true", help="Lancia 3appdays.py --fast")
    parser.add_argument("--day2-refresh", action="store_true", help="Lancia 3appdays.py --day2-refresh")
    return parser.parse_args()


def main():
    args = parse_args()

    chosen = sum([
        1 if args.night else 0,
        1 if args.fast else 0,
        1 if args.day2_refresh else 0
    ])

    if chosen != 1:
        log("Usa una sola modalità: --night oppure --fast oppure --day2-refresh")
        raise SystemExit(1)

    if args.night:
        run_night_workflow()
        return

    if args.fast:
        run_fast_workflow()
        return

    if args.day2_refresh:
        run_day2_refresh_workflow()
        return


if __name__ == "__main__":
    main()
