import os
import json
import argparse
import traceback
import importlib.util
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

# =========================================================
# CONFIG
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

ENGINE_FILE = os.path.join(BASE_DIR, "3appDays.py")

DAY_FILES = {
    "day1": {
        "snapshot": os.path.join(DATA_DIR, "snapshot_day1.json"),
        "output": os.path.join(OUTPUT_DIR, "data_day1.json"),
    },
    "day2": {
        "snapshot": os.path.join(DATA_DIR, "snapshot_day2.json"),
        "output": os.path.join(OUTPUT_DIR, "data_day2.json"),
    },
    "day3": {
        "snapshot": os.path.join(DATA_DIR, "snapshot_day3.json"),
        "output": os.path.join(OUTPUT_DIR, "data_day3.json"),
    },
    "day4": {
        "snapshot": os.path.join(DATA_DIR, "snapshot_day4.json"),
        "output": os.path.join(OUTPUT_DIR, "data_day4.json"),
    },
}

LAST_FAST_UPDATE_FILE = os.path.join(OUTPUT_DIR, "last_fast_update.json")
RUN_STATE_FILE = os.path.join(DATA_DIR, "run_state.json")

# Se vuoi forzare il timezone logico, tienilo come nota.
# Lato date operative uso datetime.now() locale del runtime.
TIMEZONE_NAME = "Europe/Rome"


# =========================================================
# LOG
# =========================================================
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


# =========================================================
# FILE / JSON UTILS
# =========================================================
def ensure_directories() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_read_json(path: str, default: Optional[Any] = None) -> Any:
    if default is None:
        default = {}

    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"WARNING lettura JSON fallita: {path} -> {e}")
        return default


def safe_write_json(path: str, payload: Any) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def get_snapshot_path(day_label: str) -> str:
    return DAY_FILES[day_label]["snapshot"]


def get_output_path(day_label: str) -> str:
    return DAY_FILES[day_label]["output"]


def load_snapshot(day_label: str) -> Optional[Dict[str, Any]]:
    path = get_snapshot_path(day_label)
    data = safe_read_json(path, default=None)
    if not data:
        return None
    return data


def save_snapshot(day_label: str, snapshot_payload: Dict[str, Any]) -> None:
    path = get_snapshot_path(day_label)
    safe_write_json(path, snapshot_payload)


def save_output(day_label: str, output_payload: Dict[str, Any]) -> None:
    path = get_output_path(day_label)
    safe_write_json(path, output_payload)


def update_last_fast_update(mode: str, day_label: str = "day1") -> None:
    payload = {
        "last_update": now_iso(),
        "mode": mode,
        "day": day_label,
        "source": "3appDays_runner.py",
    }
    safe_write_json(LAST_FAST_UPDATE_FILE, payload)


def save_run_state(payload: Dict[str, Any]) -> None:
    safe_write_json(RUN_STATE_FILE, payload)


# =========================================================
# DATE UTILS
# =========================================================
def get_base_date() -> datetime.date:
    """
    Data base operativa.
    Per ora: oggi locale del runtime.
    Se in futuro vuoi forzare logiche notturne particolari, si modifica qui.
    """
    return datetime.now().date()


def build_day_map(base_date) -> Dict[str, str]:
    return {
        "day1": base_date.strftime("%Y-%m-%d"),
        "day2": (base_date + timedelta(days=1)).strftime("%Y-%m-%d"),
        "day3": (base_date + timedelta(days=2)).strftime("%Y-%m-%d"),
        "day4": (base_date + timedelta(days=3)).strftime("%Y-%m-%d"),
    }


# =========================================================
# ENGINE LOADER
# =========================================================
def load_engine_module():
    """
    Carica 3appDays.py anche se il nome file inizia con numero.
    """
    if not os.path.exists(ENGINE_FILE):
        raise FileNotFoundError(f"File motore non trovato: {ENGINE_FILE}")

    spec = importlib.util.spec_from_file_location("engine_3appDays", ENGINE_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossibile creare spec per: {ENGINE_FILE}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =========================================================
# ENGINE BRIDGE
# =========================================================
def engine_build_snapshot(engine_module, target_date: str, day_label: str) -> Dict[str, Any]:
    """
    Qui agganciamo il tuo 3appDays.py.

    PRIORITÀ:
    1) Se nel motore esiste build_snapshot_for_day(...) usa quella
    2) altrimenti se esiste run_scan_for_day(..., create_snapshot=True) usa quella
    3) se non esiste nulla, alza errore chiaro
    """

    if hasattr(engine_module, "build_snapshot_for_day"):
        return engine_module.build_snapshot_for_day(
            target_date=target_date,
            day_label=day_label
        )

    if hasattr(engine_module, "run_scan_for_day"):
        result = engine_module.run_scan_for_day(
            target_date=target_date,
            day_label=day_label,
            mode="night",
            snapshot_data=None,
            create_snapshot=True,
        )

        snapshot = result.get("snapshot")
        if not snapshot:
            raise ValueError(
                "Il motore ha eseguito run_scan_for_day(create_snapshot=True) "
                "ma non ha restituito la chiave 'snapshot'."
            )
        return snapshot

    raise AttributeError(
        "Nel file 3appDays.py non trovo né build_snapshot_for_day(...) "
        "né run_scan_for_day(...)."
    )


def engine_scan_with_snapshot(
    engine_module,
    target_date: str,
    day_label: str,
    mode: str,
    snapshot_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Qui agganciamo lo scan vero del motore.

    PRIORITÀ:
    1) Se esiste scan_day_with_snapshot(...) usa quella
    2) altrimenti se esiste run_scan_for_day(...) usa quella
    """

    if hasattr(engine_module, "scan_day_with_snapshot"):
        return engine_module.scan_day_with_snapshot(
            target_date=target_date,
            day_label=day_label,
            mode=mode,
            snapshot_data=snapshot_data,
        )

    if hasattr(engine_module, "run_scan_for_day"):
        return engine_module.run_scan_for_day(
            target_date=target_date,
            day_label=day_label,
            mode=mode,
            snapshot_data=snapshot_data,
            create_snapshot=False,
        )

    raise AttributeError(
        "Nel file 3appDays.py non trovo né scan_day_with_snapshot(...) "
        "né run_scan_for_day(...)."
    )


# =========================================================
# OUTPUT NORMALIZER
# =========================================================
def normalize_output_payload(
    raw_output: Dict[str, Any],
    day_label: str,
    target_date: str,
    mode: str
) -> Dict[str, Any]:
    """
    Garantisce sempre un JSON coerente per il sito.
    """
    if raw_output is None:
        raw_output = {}

    results = raw_output.get("results", [])
    meta = raw_output.get("meta", {})

    payload = {
        "day_label": day_label,
        "target_date": target_date,
        "mode": mode,
        "generated_at": now_iso(),
        "results": results,
        "meta": {
            "matches_found": len(results),
            **meta
        }
    }

    # Manteniamo anche eventuali altre chiavi utili dal motore
    for k, v in raw_output.items():
        if k not in payload:
            payload[k] = v

    return payload


# =========================================================
# SINGLE DAY EXECUTION
# =========================================================
def execute_day(engine_module, day_label: str, target_date: str, mode: str) -> Dict[str, Any]:
    """
    Night:
    - crea snapshot
    - salva snapshot
    - esegue scan con snapshot
    - salva output

    Fast:
    - legge snapshot esistente
    - esegue scan con snapshot
    - salva output
    - NON tocca snapshot
    """
    log(f"START {mode.upper()} {day_label} ({target_date})")

    if mode not in ("night", "fast"):
        raise ValueError(f"Mode non valido: {mode}")

    snapshot_data = None
    snapshot_created = False
    snapshot_path = get_snapshot_path(day_label)
    output_path = get_output_path(day_label)

    if mode == "night":
        snapshot_data = engine_build_snapshot(engine_module, target_date, day_label)
        if not isinstance(snapshot_data, dict):
            raise TypeError(f"Snapshot non valido per {day_label}: atteso dict")

        # arricchimento meta minimo
        if "generated_at" not in snapshot_data:
            snapshot_data["generated_at"] = now_iso()
        if "day_label" not in snapshot_data:
            snapshot_data["day_label"] = day_label
        if "target_date" not in snapshot_data:
            snapshot_data["target_date"] = target_date

        save_snapshot(day_label, snapshot_data)
        snapshot_created = True
        log(f"Snapshot salvato: {snapshot_path}")

    else:
        snapshot_data = load_snapshot(day_label)
        if not snapshot_data:
            raise FileNotFoundError(
                f"Snapshot mancante per {day_label}: {snapshot_path}. "
                f"Non puoi fare fast scan senza snapshot."
            )
        log(f"Snapshot caricato: {snapshot_path}")

    raw_output = engine_scan_with_snapshot(
        engine_module=engine_module,
        target_date=target_date,
        day_label=day_label,
        mode=mode,
        snapshot_data=snapshot_data,
    )

    output_payload = normalize_output_payload(
        raw_output=raw_output,
        day_label=day_label,
        target_date=target_date,
        mode=mode
    )

    save_output(day_label, output_payload)
    log(f"Output salvato: {output_path}")

    results_count = len(output_payload.get("results", []))

    report = {
        "day": day_label,
        "date": target_date,
        "mode": mode,
        "status": "ok",
        "snapshot_created": snapshot_created,
        "snapshot_path": snapshot_path,
        "output_path": output_path,
        "results_count": results_count,
        "generated_at": now_iso(),
    }

    log(f"END {mode.upper()} {day_label} -> {results_count} risultati")
    return report


# =========================================================
# NIGHT WORKFLOW
# =========================================================
def run_night_workflow() -> None:
    ensure_directories()
    engine_module = load_engine_module()

    log("=====================================")
    log("NIGHT WORKFLOW START")
    log("=====================================")

    base_date = get_base_date()
    day_map = build_day_map(base_date)

    reports = []

    for day_label in ["day1", "day2", "day3", "day4"]:
        target_date = day_map[day_label]

        try:
            report = execute_day(
                engine_module=engine_module,
                day_label=day_label,
                target_date=target_date,
                mode="night"
            )
            reports.append(report)

        except Exception as e:
            err = {
                "day": day_label,
                "date": target_date,
                "mode": "night",
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc(),
                "generated_at": now_iso(),
            }
            reports.append(err)
            log(f"ERRORE {day_label}: {e}")

            # Nota: qui NON fermo tutto il workflow.
            # Così un errore su day3 non ti blocca day4.

    update_last_fast_update(mode="night", day_label="day1")

    save_run_state({
        "last_run_type": "night",
        "generated_at": now_iso(),
        "base_date": base_date.strftime("%Y-%m-%d"),
        "reports": reports,
    })

    ok_count = sum(1 for r in reports if r.get("status") == "ok")
    log(f"NIGHT WORKFLOW END - successi: {ok_count}/{len(reports)}")
    log("=====================================")


# =========================================================
# FAST WORKFLOW
# =========================================================
def run_fast_workflow() -> None:
    ensure_directories()
    engine_module = load_engine_module()

    log("=====================================")
    log("FAST WORKFLOW START")
    log("=====================================")

    base_date = get_base_date()
    day_map = build_day_map(base_date)
    target_date = day_map["day1"]

    try:
        report = execute_day(
            engine_module=engine_module,
            day_label="day1",
            target_date=target_date,
            mode="fast"
        )
    except Exception as e:
        report = {
            "day": "day1",
            "date": target_date,
            "mode": "fast",
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "generated_at": now_iso(),
        }
        log(f"ERRORE FAST day1: {e}")

    update_last_fast_update(mode="fast", day_label="day1")

    save_run_state({
        "last_run_type": "fast",
        "generated_at": now_iso(),
        "base_date": base_date.strftime("%Y-%m-%d"),
        "reports": [report],
    })

    log("FAST WORKFLOW END")
    log("=====================================")


# =========================================================
# CLI
# =========================================================
def parse_args():
    parser = argparse.ArgumentParser(description="Runner workflow ArabSniper")
    parser.add_argument("--night", action="store_true", help="Esegue il night workflow")
    parser.add_argument("--fast", action="store_true", help="Esegue il fast workflow")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.night and args.fast:
        log("Errore: usa solo una modalità per volta (--night oppure --fast).")
        return

    if args.night:
        run_night_workflow()
        return

    if args.fast:
        run_fast_workflow()
        return

    log("Nessuna modalità specificata. Usa --night oppure --fast.")


if __name__ == "__main__":
    main()
