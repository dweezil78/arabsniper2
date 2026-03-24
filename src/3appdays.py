import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
import json
import os
import time
import sys
from pathlib import Path
from github import Github

# ==========================================
# CONFIGURAZIONE ARAB SNIPER V24.1 MULTI-DAY WEB
# Base derivata dalla V24 test
# Stretta selettiva su:
# - BOOST
# - GOLD
# + rolling snapshot 5 giorni
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

DATA_DIR = PROJECT_ROOT / "data"

DB_FILE = str(DATA_DIR / "arab_sniper_database.json")
SNAP_FILE = str(DATA_DIR / "arab_snapshot_database.json")
CONFIG_FILE = str(DATA_DIR / "nazioni_config.json")
DETAILS_FILE = str(DATA_DIR / "match_details.json")

DEFAULT_EXCLUDED = ["Thailand", "Indonesia", "India", "Kenya", "Morocco", "Rwanda", "Nigeria", "Oman", "Algeria", "UAE"]
LEAGUE_BLACKLIST = ["u19", "u20", "youth", "women", "friendly", "carioca", "paulista", "mineiro"]
ROLLING_SNAPSHOT_HORIZONS = [1, 2, 3, 4, 5]

REMOTE_MAIN_FILE = "data/data.json"
REMOTE_SNAPSHOT_FILE = "data/arab_snapshot_database.json"

REMOTE_DAY_FILES = {
    1: "data/data_day1.json",
    2: "data/data_day2.json",
    3: "data/data_day3.json",
    4: "data/data_day4.json",
    5: "data/data_day5.json",
}

REMOTE_DETAILS_FILES = {
    1: "data/details_day1.json",
    2: "data/details_day2.json",
    3: "data/details_day3.json",
    4: "data/details_day4.json",
    5: "data/details_day5.json",
}

REMOTE_SNAPSHOT_DAY_FILES = {
    1: "data/snapshot_day1.json",
    2: "data/snapshot_day2.json",
    3: "data/snapshot_day3.json",
    4: "data/snapshot_day4.json",
    5: "data/snapshot_day5.json",
}

try:
    from zoneinfo import ZoneInfo
    ROME_TZ = ZoneInfo("Europe/Rome")
except Exception:
    ROME_TZ = None


def now_rome():
    return datetime.now(ROME_TZ) if ROME_TZ else datetime.now()


def fixture_dt_rome(fixture_obj):
    """
    Converte la data fixture in Europe/Rome in modo robusto.
    Usa timestamp se disponibile, altrimenti prova con il campo date ISO.
    """
    try:
        ts = fixture_obj.get("timestamp")
        if ts:
            dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            return dt_utc.astimezone(ROME_TZ) if ROME_TZ else dt_utc
    except Exception:
        pass

    try:
        raw = str(fixture_obj.get("date", "")).strip()
        if raw:
            raw = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(ROME_TZ) if ROME_TZ else dt
    except Exception:
        pass

    return None


st.set_page_config(page_title="ARAB SNIPER V24.1 MULTI-DAY WEB", layout="wide")

# ==========================================
# GITHUB UPDATE CORE
# ==========================================
def github_write_json(filename, payload, commit_message):
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            try:
                token = st.secrets["GITHUB_TOKEN"]
            except Exception:
                token = None

        if not token:
            print("❌ GITHUB_TOKEN mancante", flush=True)
            return "MISSING_TOKEN"

        g = Github(token)
        repo_name = "dweezil78/arabsniper2"   # <-- verifica che sia ESATTO
        print(f"📦 Repo target: {repo_name}", flush=True)
        print(f"📄 File target: {filename}", flush=True)

        repo = g.get_repo(repo_name)
        content_str = json.dumps(payload, indent=4, ensure_ascii=False)

        # 1) provo update se il file esiste
        try:
            contents = repo.get_contents(filename)
            repo.update_file(contents.path, commit_message, content_str, contents.sha)
            print(f"✅ GitHub update OK: {filename}", flush=True)
            return "SUCCESS"
        except Exception as e_update:
            print(f"⚠️ Update fallito su {filename}: {e_update}", flush=True)

        # 2) se update fallisce, provo create
        try:
            repo.create_file(filename, commit_message, content_str)
            print(f"✅ GitHub create OK: {filename}", flush=True)
            return "SUCCESS"
        except Exception as e_create:
            print(f"❌ Create fallito su {filename}: {e_create}", flush=True)
            return f"CREATE_FAILED: {e_create}"

    except Exception as e:
        print(f"❌ GitHub write error su {filename}: {e}", flush=True)
        return f"GITHUB_ERROR: {e}"

# ==========================================
# WRAPPER FUNZIONI (FONDAMENTALI)
# ==========================================

def upload_to_github_main(results):
    return github_write_json(
        REMOTE_MAIN_FILE,
        results,
        "Update Arab Sniper Main Data"
    )


def upload_day_to_github(day_num, results):
    return github_write_json(
        REMOTE_DAY_FILES[day_num],
        results,
        f"Update Arab Sniper Day {day_num} Data"
    )


def upload_details_to_github(day_num, payload):
    return github_write_json(
        REMOTE_DETAILS_FILES[day_num],
        payload,
        f"Update Arab Sniper Day {day_num} Details"
    )

# ==========================================
# SESSION STATE
# ==========================================
if "config" not in st.session_state:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                st.session_state.config = json.load(f)
        except Exception:
            st.session_state.config = {"excluded": DEFAULT_EXCLUDED}
    else:
        st.session_state.config = {"excluded": DEFAULT_EXCLUDED}

if "team_stats_cache" not in st.session_state:
    st.session_state.team_stats_cache = {}

if "team_last_matches_cache" not in st.session_state:
    st.session_state.team_last_matches_cache = {}

if "available_countries" not in st.session_state:
    st.session_state.available_countries = []

if "scan_results" not in st.session_state:
    st.session_state.scan_results = []

if "odds_memory" not in st.session_state:
    st.session_state.odds_memory = {}

if "match_details" not in st.session_state:
    st.session_state.match_details = {}

if "selected_fixture_for_modal" not in st.session_state:
    st.session_state.selected_fixture_for_modal = None


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(st.session_state.config, f, indent=4, ensure_ascii=False)

def load_snapshot_from_github():
    """
    Fallback: carica lo snapshot da GitHub se il file locale
    non esiste o non contiene odds valide.
    """
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            try:
                token = st.secrets["GITHUB_TOKEN"]
            except Exception:
                token = None

        if not token:
            print("⚠️ GITHUB_TOKEN mancante: impossibile caricare snapshot da GitHub", flush=True)
            return None

        g = Github(token)
        repo = g.get_repo("dweezil78/arabsniper2")
        contents = repo.get_contents(REMOTE_SNAPSHOT_FILE)
        raw = contents.decoded_content.decode("utf-8")
        payload = json.loads(raw)

        if not isinstance(payload, dict):
            return None

        odds = payload.get("odds", {}) or {}
        if not isinstance(odds, dict):
            return None

        print(f"✅ Snapshot caricato da GitHub: {len(odds)} fixture", flush=True)
        return payload

    except Exception as e:
        print(f"⚠️ Errore load_snapshot_from_github: {e}", flush=True)
        return None

def load_db():
    ts = "N/D"
    today = now_rome().strftime("%Y-%m-%d")

    # Carica risultati principali
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f).get("results", [])
                st.session_state.scan_results = [r for r in data if r.get("Data", "") >= today]
        except Exception:
            pass

    # Carica snapshot: prima locale, poi fallback GitHub
    snap_data = None

    if os.path.exists(SNAP_FILE):
        try:
            with open(SNAP_FILE, "r", encoding="utf-8") as f:
                snap_data = json.load(f)
        except Exception:
            snap_data = None

    local_odds = {}
    if isinstance(snap_data, dict):
        local_odds = snap_data.get("odds", {}) or {}

    if not local_odds:
        snap_data = load_snapshot_from_github()

        if isinstance(snap_data, dict) and snap_data.get("odds"):
            try:
                save_snapshot_file(snap_data)
            except Exception as e:
                print(f"⚠️ Impossibile salvare snapshot locale dal fallback GitHub: {e}", flush=True)

    if isinstance(snap_data, dict):
        try:
            st.session_state.odds_memory = snap_data.get("odds", {}) or {}
            ts = snap_data.get("timestamp", "N/D")
        except Exception:
            pass

    # Carica dettagli match
    if os.path.exists(DETAILS_FILE):
        try:
            with open(DETAILS_FILE, "r", encoding="utf-8") as f:
                details_data = json.load(f)
                st.session_state.match_details = details_data.get("details", {})
        except Exception:
            pass

    return ts


last_snap_ts = load_db()

# ==========================================
# API CORE & ROBUSTNESS
# ==========================================
API_KEY = os.getenv("API_SPORTS_KEY")

if not API_KEY:
    try:
        API_KEY = st.secrets.get("API_SPORTS_KEY", None)
    except Exception:
        pass

HEADERS = {"x-apisports-key": API_KEY} if API_KEY else {}


def api_get(session, path, params):
    if not API_KEY:
        print("❌ API_KEY assente dentro api_get")
        return None

    safe_key = f"{API_KEY[:5]}***" if len(API_KEY) >= 5 else "***"
    print(f"🔑 API key rilevata: {safe_key}")
    print(f"🌐 API GET path={path} params={params}")

    for attempt in range(2):
        try:
            r = session.get(
                f"https://v3.football.api-sports.io/{path}",
                headers=HEADERS,
                params=params,
                timeout=20
            )

            print(f"📡 Tentativo {attempt+1} -> status_code={r.status_code}")

            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception as json_err:
                    print(f"❌ JSON decode error: {json_err}")
                    print(f"🧾 Response text preview: {r.text[:300]}")
                    time.sleep(1)
                    continue

                if not isinstance(data, dict):
                    print(f"❌ Risposta non dict: {type(data)}")
                    print(f"🧾 Response preview: {str(data)[:300]}")
                    time.sleep(1)
                    continue

                if data.get("errors"):
                    print(f"❌ API errors: {data.get('errors')}")

                if "response" not in data:
                    print(f"❌ Chiave 'response' assente nel payload")
                    print(f"🧾 Payload preview: {str(data)[:500]}")
                else:
                    try:
                        print(f"✅ Response entries: {len(data.get('response', []))}")
                    except Exception:
                        print("✅ Response presente")

                return data

            else:
                print(f"❌ HTTP status non 200: {r.status_code}")
                print(f"🧾 Response text preview: {r.text[:300]}")
                time.sleep(1)

        except Exception as e:
            print(f"❌ Exception api_get attempt {attempt+1}: {e}")
            if attempt == 1:
                return None
            time.sleep(1)

    return None


def _contains_ht(text):
    t = str(text or "").lower()
    return any(k in t for k in ["1st half", "first half", "1h", "ht", "half time", "halftime", "1° tempo"])


def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip().replace(",", ".")
        if s in ("", "-", "None", "null"):
            return default
        return float(s)
    except Exception:
        return default


def is_blacklisted_league(league_name):
    name = str(league_name or "").lower()
    return any(k in name for k in LEAGUE_BLACKLIST)


def extract_elite_markets(session, fid):
    res = api_get(session, "odds", {"fixture": fid})
    if not res or not res.get("response"):
        return None

    mk = {"q1": 0.0, "qx": 0.0, "q2": 0.0, "o25": 0.0, "o05ht": 0.0, "o15ht": 0.0}

    for bm in res["response"][0].get("bookmakers", []):
        for b in bm.get("bets", []):
            name = (b.get("name") or "").lower()
            bid = b.get("id")

            if bid == 1 and mk["q1"] == 0:
                for v in b.get("values", []):
                    vl = str(v.get("value", "")).lower()
                    odd = safe_float(v.get("odd"), 0.0)
                    if "home" in vl:
                        mk["q1"] = odd
                    elif "draw" in vl:
                        mk["qx"] = odd
                    elif "away" in vl:
                        mk["q2"] = odd

            if bid == 5 and mk["o25"] == 0:
                if any(j in name for j in ["corner", "card", "booking"]):
                    continue
                for v in b.get("values", []):
                    if "over 2.5" in str(v.get("value", "")).lower():
                        mk["o25"] = safe_float(v.get("odd"), 0.0)

            if _contains_ht(name) and any(k in name for k in ["total", "over/under", "ou", "goals"]):
                if "team" in name:
                    continue
                for v in b.get("values", []):
                    val_txt = str(v.get("value", "")).lower().replace(",", ".")
                    if "over 0.5" in val_txt and mk["o05ht"] == 0:
                        mk["o05ht"] = safe_float(v.get("odd"), 0.0)
                    if "over 1.5" in val_txt and mk["o15ht"] == 0:
                        mk["o15ht"] = safe_float(v.get("odd"), 0.0)

        if mk["q1"] > 0 and mk["o25"] > 0 and mk["o05ht"] > 0:
            break

    if (1.01 <= mk["q1"] <= 1.10) or (1.01 <= mk["q2"] <= 1.10) or (1.01 <= mk["o25"] <= 1.30):
        return "SKIP"

    return mk


def save_snapshot_file(payload):
    with open(SNAP_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)


def _normalize_snapshot_record(fid, rec):
    """
    Normalizza un record snapshot vecchio o nuovo.
    Mantiene compatibilità col vecchio formato che salvava solo q1/q2.
    """
    if not isinstance(rec, dict):
        return None

    norm = dict(rec)
    norm["fixture_id"] = str(fid)

    # Legacy compatibility: vecchio snapshot aveva solo q1 / q2
    legacy_q1 = safe_float(norm.get("q1"), 0.0)
    legacy_q2 = safe_float(norm.get("q2"), 0.0)

    # Nuovi campi open
    norm["q1_open"] = safe_float(norm.get("q1_open", legacy_q1), 0.0)
    norm["qx_open"] = safe_float(norm.get("qx_open", 0.0), 0.0)
    norm["q2_open"] = safe_float(norm.get("q2_open", legacy_q2), 0.0)
    norm["o25_open"] = safe_float(norm.get("o25_open", 0.0), 0.0)
    norm["o05ht_open"] = safe_float(norm.get("o05ht_open", 0.0), 0.0)
    norm["o15ht_open"] = safe_float(norm.get("o15ht_open", 0.0), 0.0)

    # Campi legacy tenuti per compatibilità temporanea
    # così compute_drop_diff continua a funzionare anche prima dello step 2
    norm["q1"] = norm["q1_open"]
    norm["q2"] = norm["q2_open"]

    # Metadati minimi
    norm.setdefault("first_seen_date", None)
    norm.setdefault("first_seen_horizon", None)
    norm.setdefault("first_seen_ts", None)
    norm.setdefault("last_seen_date", norm.get("first_seen_date"))
    norm.setdefault("last_seen_horizon", norm.get("first_seen_horizon"))
    norm.setdefault("last_seen_ts", norm.get("first_seen_ts"))

    return norm


def save_snapshot_file(payload):
    with open(SNAP_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)


def load_existing_snapshot_payload():
    """
    Carica snapshot esistente e lo migra al nuovo formato se necessario.
    Non perde i vecchi dati q1/q2.
    """
    if os.path.exists(SNAP_FILE):
        try:
            with open(SNAP_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)

            if isinstance(payload, dict):
                raw_odds = payload.get("odds", {}) or {}
                normalized_odds = {}

                for fid, rec in raw_odds.items():
                    norm = _normalize_snapshot_record(fid, rec)
                    if norm:
                        normalized_odds[str(fid)] = norm

                payload["odds"] = normalized_odds
                payload.setdefault("timestamp", None)
                payload.setdefault("updated_at", None)
                payload.setdefault("coverage", "rolling_day1_day5")
                return payload

        except Exception:
            pass

    return {
        "odds": {},
        "timestamp": None,
        "updated_at": None,
        "coverage": "rolling_day1_day5"
    }

def build_rolling_multiday_snapshot(session):
    """
    Snapshot rolling Day1-Day5 basato su fixture_id.

    Regola fondamentale:
    - se il fixture NON esiste -> salva la baseline open completa
    - se il fixture ESISTE -> NON sovrascrive mai le open
    - aggiorna solo i campi last_seen_*

    In questo modo:
    - se ieri era day3 e oggi è day2/day1, le quote open restano quelle iniziali
    - il drop resta ancorato alla prima quota vista
    """
    target_dates = get_target_dates()
    existing_payload = load_existing_snapshot_payload()
    existing_odds = existing_payload.get("odds", {}) or {}

    new_odds = {}
    active_fixture_ids = set()
    current_ts = now_rome().strftime("%Y-%m-%d %H:%M:%S")
    current_hhmm = now_rome().strftime("%H:%M")

    # Riparti già da dati normalizzati
    for fid, rec in existing_odds.items():
        norm = _normalize_snapshot_record(fid, rec)
        if norm:
            new_odds[str(fid)] = norm

    for horizon in ROLLING_SNAPSHOT_HORIZONS:
        target_date = target_dates[horizon - 1]

        res = api_get(session, "fixtures", {"date": target_date, "timezone": "Europe/Rome"})
        if not res:
            continue

        fx_list = [
            f for f in res.get("response", [])
            if f["fixture"]["status"]["short"] == "NS"
            and not is_blacklisted_league(f.get("league", {}).get("name", ""))
        ]

        for f in fx_list:
            fid = str(f["fixture"]["id"])
            active_fixture_ids.add(fid)

            mk = extract_elite_markets(session, f["fixture"]["id"])
            if not mk or mk == "SKIP":
                continue

            existing_rec = new_odds.get(fid)

            # Fixture mai visto prima -> scrivi OPEN una sola volta
            if not existing_rec:
                new_odds[fid] = {
                    "fixture_id": fid,

                    # OPEN fisse
                    "q1_open": safe_float(mk.get("q1"), 0.0),
                    "qx_open": safe_float(mk.get("qx"), 0.0),
                    "q2_open": safe_float(mk.get("q2"), 0.0),
                    "o25_open": safe_float(mk.get("o25"), 0.0),
                    "o05ht_open": safe_float(mk.get("o05ht"), 0.0),
                    "o15ht_open": safe_float(mk.get("o15ht"), 0.0),

                    # Legacy compatibility temporanea
                    "q1": safe_float(mk.get("q1"), 0.0),
                    "q2": safe_float(mk.get("q2"), 0.0),

                    # First seen
                    "first_seen_date": target_date,
                    "first_seen_horizon": horizon,
                    "first_seen_ts": current_ts,

                    # Last seen
                    "last_seen_date": target_date,
                    "last_seen_horizon": horizon,
                    "last_seen_ts": current_ts
                }

            else:
                # NON toccare mai le open già salvate
                existing_rec["fixture_id"] = fid

                # Garantisce presenza campi nuovi anche su record legacy
                existing_rec["q1_open"] = safe_float(existing_rec.get("q1_open", existing_rec.get("q1", 0.0)), 0.0)
                existing_rec["qx_open"] = safe_float(existing_rec.get("qx_open", 0.0), 0.0)
                existing_rec["q2_open"] = safe_float(existing_rec.get("q2_open", existing_rec.get("q2", 0.0)), 0.0)
                existing_rec["o25_open"] = safe_float(existing_rec.get("o25_open", 0.0), 0.0)
                existing_rec["o05ht_open"] = safe_float(existing_rec.get("o05ht_open", 0.0), 0.0)
                existing_rec["o15ht_open"] = safe_float(existing_rec.get("o15ht_open", 0.0), 0.0)

                # Legacy fields ancora presenti per compatibilità
                existing_rec["q1"] = existing_rec["q1_open"]
                existing_rec["q2"] = existing_rec["q2_open"]

                # Aggiorna solo last_seen
                existing_rec["last_seen_date"] = target_date
                existing_rec["last_seen_horizon"] = horizon
                existing_rec["last_seen_ts"] = current_ts

                new_odds[fid] = existing_rec

        time.sleep(0.15)

    # Mantieni solo i fixture ancora vivi nel rolling snapshot
    cleaned_odds = {}
    for fid, data in new_odds.items():
        if fid in active_fixture_ids:
            cleaned_odds[fid] = data

    payload = {
        "odds": cleaned_odds,
        "timestamp": current_hhmm,
        "updated_at": current_ts,
        "coverage": "rolling_day1_day5"
    }

    st.session_state.odds_memory = cleaned_odds

    # Salva locale
    save_snapshot_file(payload)

    # 🔥 Salva su GitHub
    upload_snapshot_to_github(payload)

    return payload

def upload_snapshot_day_to_github(day_num, payload):
    try:
        github_write_json(
            REMOTE_SNAPSHOT_DAY_FILES[day_num],
            payload,
            f"Update snapshot_day{day_num}.json"
        )
    except Exception as e:
        print(f"Snapshot day{day_num} upload error: {e}")


def build_daily_snapshots_from_rolling():
    """
    Crea snapshot_day1...snapshot_day5 filtrando il rolling snapshot centrale.
    NON riscrive le OPEN: usa i record già consolidati nel rolling snapshot.
    """
    payload = load_existing_snapshot_payload()
    odds_map = payload.get("odds", {}) or {}
    target_dates = get_target_dates()
    current_ts = now_rome().strftime("%Y-%m-%d %H:%M:%S")

    for day_num in range(1, 6):
        day_date = target_dates[day_num - 1]
        day_odds = {}

        for fid, rec in odds_map.items():
            if not isinstance(rec, dict):
                continue
            if str(rec.get("last_seen_date", "")).strip() == day_date:
                day_odds[str(fid)] = rec

        day_payload = {
            "day": day_num,
            "date": day_date,
            "updated_at": current_ts,
            "odds": day_odds,
        }

        out_file = DATA_DIR / f"snapshot_day{day_num}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(day_payload, f, indent=4, ensure_ascii=False)

        upload_snapshot_day_to_github(day_num, day_payload)
        print(f"📦 snapshot_day{day_num}.json aggiornato: {len(day_odds)} match")
        
def get_team_last_matches(session, tid):
    cache_key = str(tid)
    if cache_key in st.session_state.team_last_matches_cache:
        return st.session_state.team_last_matches_cache[cache_key]

    res = api_get(session, "fixtures", {"team": tid, "last": 8, "status": "FT"})
    fx = res.get("response", []) if res else []

    last_matches = []
    for f in fx:
        home = f.get("teams", {}).get("home", {})
        away = f.get("teams", {}).get("away", {})

        home_id = home.get("id")
        away_id = away.get("id")

        home_name = home.get("name", "N/D")
        away_name = away.get("name", "N/D")

        gh = safe_float(f.get("goals", {}).get("home", 0), 0.0)
        ga = safe_float(f.get("goals", {}).get("away", 0), 0.0)

        hth = safe_float(f.get("score", {}).get("halftime", {}).get("home", 0), 0.0)
        hta = safe_float(f.get("score", {}).get("halftime", {}).get("away", 0), 0.0)

        # Identifica se la squadra analizzata giocava in casa o fuori
        is_home_team = str(home_id) == str(tid)
        is_away_team = str(away_id) == str(tid)

        team_ht_scored = 0.0
        team_ht_conceded = 0.0
        team_ft_scored = 0.0
        team_ft_conceded = 0.0

        if is_home_team:
            team_ht_scored = hth
            team_ht_conceded = hta
            team_ft_scored = gh
            team_ft_conceded = ga
        elif is_away_team:
            team_ht_scored = hta
            team_ht_conceded = hth
            team_ft_scored = ga
            team_ft_conceded = gh

        total_ht_goals = hth + hta
        total_ft_goals = gh + ga

        second_half_scored = max(team_ft_scored - team_ht_scored, 0.0)
        second_half_conceded = max(team_ft_conceded - team_ht_conceded, 0.0)

        last_matches.append({
            "date": str(f.get("fixture", {}).get("date", ""))[:10],
            "league": f.get("league", {}).get("name", "N/D"),
            "match": f"{home_name} - {away_name}",
            "ht": f"{int(hth)}-{int(hta)}",
            "ft": f"{int(gh)}-{int(ga)}",
            "total_ht_goals": total_ht_goals,
            "total_ft_goals": total_ft_goals,

            # nuove metriche squadra-specifiche
            "team_ht_scored": team_ht_scored,
            "team_ht_conceded": team_ht_conceded,
            "team_ft_scored": team_ft_scored,
            "team_ft_conceded": team_ft_conceded,
            "team_2h_scored": second_half_scored,
            "team_2h_conceded": second_half_conceded
        })

    st.session_state.team_last_matches_cache[cache_key] = last_matches
    return last_matches

def get_team_performance(session, tid):
    cache_key = str(tid)
    if cache_key in st.session_state.team_stats_cache:
        return st.session_state.team_stats_cache[cache_key]

    last_matches = get_team_last_matches(session, tid)
    if not last_matches:
        return None

    ft_list = [safe_float(m.get("total_ft_goals"), 0.0) for m in last_matches]
    ht_list = [safe_float(m.get("total_ht_goals"), 0.0) for m in last_matches]

    ht_scored_list = [safe_float(m.get("team_ht_scored"), 0.0) for m in last_matches]
    ht_conceded_list = [safe_float(m.get("team_ht_conceded"), 0.0) for m in last_matches]
    ft_scored_list = [safe_float(m.get("team_ft_scored"), 0.0) for m in last_matches]
    ft_conceded_list = [safe_float(m.get("team_ft_conceded"), 0.0) for m in last_matches]

    if not ft_list or not ht_list:
        return None

    act = len(ft_list)

    def trimmed_mean(values):
        vals = sorted([safe_float(v, 0.0) for v in values])
        if not vals:
            return 0.0
        if len(vals) >= 5:
            core = vals[1:-1]
        else:
            core = vals
        if not core:
            return 0.0
        return sum(core) / len(core)

    avg_total = sum(ft_list) / act
    avg_ht = sum(ht_list) / act

    avg_total_clean = trimmed_mean(ft_list)
    avg_ht_clean = trimmed_mean(ht_list)

    avg_ht_scored = sum(ht_scored_list) / act
    avg_ht_conceded = sum(ht_conceded_list) / act
    avg_ft_scored = sum(ft_scored_list) / act
    avg_ft_conceded = sum(ft_conceded_list) / act

    avg_ht_scored_clean = trimmed_mean(ht_scored_list)
    avg_ht_conceded_clean = trimmed_mean(ht_conceded_list)
    avg_ft_scored_clean = trimmed_mean(ft_scored_list)
    avg_ft_conceded_clean = trimmed_mean(ft_conceded_list)

    ft_2plus_rate = sum(1 for x in ft_list if x >= 2) / act
    ft_3plus_rate = sum(1 for x in ft_list if x >= 3) / act
    ft_low_rate = sum(1 for x in ft_list if x <= 1) / act

    ht_1plus_rate = sum(1 for x in ht_list if x >= 1) / act
    ht_zero_rate = sum(1 for x in ht_list if x == 0) / act

    # nuove frequenze utili per PT
    ht_scored_1plus_rate = sum(1 for x in ht_scored_list if x >= 1) / act
    ht_scored_2plus_rate = sum(1 for x in ht_scored_list if x >= 2) / act
    ht_conceded_1plus_rate = sum(1 for x in ht_conceded_list if x >= 1) / act

    ft_peak_count = sum(1 for x in ft_list if x >= 5)

    last_match = last_matches[0] if last_matches else {}
    last_2h_scored = safe_float(last_match.get("team_2h_scored"), 0.0)
    last_2h_conceded = safe_float(last_match.get("team_2h_conceded"), 0.0)

    last_2h_zero = (last_2h_scored == 0)
    last_2h_conceded_zero = (last_2h_conceded == 0)

    stats = {
        "avg_ht": round3(avg_ht),
        "avg_total": round3(avg_total),
        "avg_ht_clean": round3(avg_ht_clean),
        "avg_total_clean": round3(avg_total_clean),

        "avg_ht_scored": round3(avg_ht_scored),
        "avg_ht_conceded": round3(avg_ht_conceded),
        "avg_ft_scored": round3(avg_ft_scored),
        "avg_ft_conceded": round3(avg_ft_conceded),

        "avg_ht_scored_clean": round3(avg_ht_scored_clean),
        "avg_ht_conceded_clean": round3(avg_ht_conceded_clean),
        "avg_ft_scored_clean": round3(avg_ft_scored_clean),
        "avg_ft_conceded_clean": round3(avg_ft_conceded_clean),

        "ht_1plus_rate": round3(ht_1plus_rate),
        "ht_zero_rate": round3(ht_zero_rate),
        "ft_2plus_rate": round3(ft_2plus_rate),
        "ft_3plus_rate": round3(ft_3plus_rate),
        "ft_low_rate": round3(ft_low_rate),

        "ht_scored_1plus_rate": round3(ht_scored_1plus_rate),
        "ht_scored_2plus_rate": round3(ht_scored_2plus_rate),
        "ht_conceded_1plus_rate": round3(ht_conceded_1plus_rate),

        "ft_peak_count": int(ft_peak_count),
        "last_2h_zero": last_2h_zero,
        "last_2h_conceded_zero": last_2h_conceded_zero
    }

    st.session_state.team_stats_cache[cache_key] = stats
    return stats

# ==========================================
# SCORING HELPERS V24.1
# ==========================================
def round3(x):
    return round(float(x), 3)


def symmetry_bonus(a, b, tight=0.22, medium=0.45):
    diff = abs(float(a) - float(b))
    if diff <= tight:
        return 0.8
    if diff <= medium:
        return 0.4
    return 0.0


def band_score(value, core_low, core_high, soft_low=None, soft_high=None, core_pts=1.0, soft_pts=0.45):
    v = safe_float(value, 0.0)
    if core_low <= v <= core_high:
        return core_pts
    if soft_low is not None and soft_high is not None and soft_low <= v <= soft_high:
        return soft_pts
    return 0.0

def get_open_quote_pack(fid):
    """
    Legge dal snapshot le quote open salvate per il fixture.
    """
    odds_memory = st.session_state.get("odds_memory", {}) or {}
    rec = odds_memory.get(str(fid), {}) or {}

    return {
        "q1": safe_float(rec.get("q1_open", rec.get("q1", 0.0)), 0.0),
        "qx": safe_float(rec.get("qx_open", 0.0), 0.0),
        "q2": safe_float(rec.get("q2_open", rec.get("q2", 0.0)), 0.0),
        "o25": safe_float(rec.get("o25_open", 0.0), 0.0),
        "o05ht": safe_float(rec.get("o05ht_open", 0.0), 0.0),
        "o15ht": safe_float(rec.get("o15ht_open", 0.0), 0.0),
    }


def get_current_quote_pack(mk):
    """
    Legge le quote correnti dal pacchetto mercati estratto ora.
    """
    mk = mk or {}
    return {
        "q1": safe_float(mk.get("q1"), 0.0),
        "qx": safe_float(mk.get("qx"), 0.0),
        "q2": safe_float(mk.get("q2"), 0.0),
        "o25": safe_float(mk.get("o25"), 0.0),
        "o05ht": safe_float(mk.get("o05ht"), 0.0),
        "o15ht": safe_float(mk.get("o15ht"), 0.0),
    }


def classify_single_quote_move(open_q, current_q):
    """
    Classifica il movimento di una singola quota.

    Regole:
    - 0.00 -> none
    - 0.01 - 0.05 -> green
    - 0.06 - 0.14 -> yellow
    - >= 0.15 -> red

    Direzione:
    - current < open => down
    - current > open => up
    """
    open_q = safe_float(open_q, 0.0)
    current_q = safe_float(current_q, 0.0)

    if open_q <= 0 or current_q <= 0:
        return {
            "open": open_q,
            "current": current_q,
            "diff": 0.0,
            "abs_diff": 0.0,
            "dir": "flat",
            "color": "none",
            "arrow": "",
            "label": ""
        }

    diff = round(current_q - open_q, 3)
    abs_diff = round(abs(diff), 3)

    if abs_diff == 0:
        color = "none"
    elif abs_diff <= 0.05:
        color = "green"
    elif abs_diff <= 0.14:
        color = "yellow"
    else:
        color = "red"

    if diff < 0:
        direction = "down"
        arrow = "↓"
    elif diff > 0:
        direction = "up"
        arrow = "↑"
    else:
        direction = "flat"
        arrow = ""

    label = ""
    if arrow:
        label = f"{arrow}{abs_diff:.2f}"

    return {
        "open": open_q,
        "current": current_q,
        "diff": diff,
        "abs_diff": abs_diff,
        "dir": direction,
        "color": color,
        "arrow": arrow,
        "label": label
    }


def get_favorite_side_from_1x2(pack, min_gap=0.03):
    """
    Determina la favorita tra 1 e 2.
    Restituisce:
    - "1"
    - "2"
    - ""  se gap troppo piccolo o dati non validi
    """
    q1 = safe_float(pack.get("q1"), 0.0)
    q2 = safe_float(pack.get("q2"), 0.0)

    if q1 <= 0 or q2 <= 0:
        return ""

    if abs(q1 - q2) < min_gap:
        return ""

    return "1" if q1 < q2 else "2"


def detect_1x2_inversion(open_pack, current_pack, min_gap=0.03):
    """
    C'è inversione se la favorita open tra 1 e 2
    diventa il lato opposto nelle quote correnti.
    """
    fav_open = get_favorite_side_from_1x2(open_pack, min_gap=min_gap)
    fav_current = get_favorite_side_from_1x2(current_pack, min_gap=min_gap)

    inversion = bool(fav_open and fav_current and fav_open != fav_current)

    return {
        "INVERSION": inversion,
        "INV_FROM": fav_open if inversion else "",
        "INV_TO": fav_current if inversion else "",
        "FAV_OPEN": fav_open,
        "FAV_CURRENT": fav_current
    }


def build_quote_movement_package(fid, mk):
    """
    Costruisce il pacchetto completo quote:
    - open
    - current
    - movimenti
    - inversione 1X2
    """
    open_pack = get_open_quote_pack(fid)
    current_pack = get_current_quote_pack(mk)

    q1_move = classify_single_quote_move(open_pack["q1"], current_pack["q1"])
    qx_move = classify_single_quote_move(open_pack["qx"], current_pack["qx"])
    q2_move = classify_single_quote_move(open_pack["q2"], current_pack["q2"])
    o25_move = classify_single_quote_move(open_pack["o25"], current_pack["o25"])
    o05ht_move = classify_single_quote_move(open_pack["o05ht"], current_pack["o05ht"])
    o15ht_move = classify_single_quote_move(open_pack["o15ht"], current_pack["o15ht"])

    inversion_pack = detect_1x2_inversion(open_pack, current_pack, min_gap=0.03)

    return {
        "Q1_OPEN": open_pack["q1"],
        "QX_OPEN": open_pack["qx"],
        "Q2_OPEN": open_pack["q2"],
        "O25_OPEN": open_pack["o25"],
        "O05HT_OPEN": open_pack["o05ht"],
        "O15HT_OPEN": open_pack["o15ht"],

        "Q1_CURR": current_pack["q1"],
        "QX_CURR": current_pack["qx"],
        "Q2_CURR": current_pack["q2"],
        "O25_CURR": current_pack["o25"],
        "O05HT_CURR": current_pack["o05ht"],
        "O15HT_CURR": current_pack["o15ht"],

        "Q1_MOVE_DATA": q1_move,
        "QX_MOVE_DATA": qx_move,
        "Q2_MOVE_DATA": q2_move,
        "O25_MOVE_DATA": o25_move,
        "O05HT_MOVE_DATA": o05ht_move,
        "O15HT_MOVE_DATA": o15ht_move,

        "Q1_MOVE": q1_move["label"],
        "QX_MOVE": qx_move["label"],
        "Q2_MOVE": q2_move["label"],
        "O25_MOVE": o25_move["label"],
        "O05HT_MOVE": o05ht_move["label"],
        "O15HT_MOVE": o15ht_move["label"],

        "INVERSION": inversion_pack["INVERSION"],
        "INV_FROM": inversion_pack["INV_FROM"],
        "INV_TO": inversion_pack["INV_TO"],
        "FAV_OPEN": inversion_pack["FAV_OPEN"],
        "FAV_CURRENT": inversion_pack["FAV_CURRENT"],
    }
    
def build_movement_summary(row):
    """
    Restituisce un riassunto testuale pulito dei movimenti quota già calcolati.

    Esempi:
    - "⚠️ 1→2 • ↓1 • O25 ↓0.12"
    - "↓2 • O05HT ↓0.07"
    - ""
    """

    parts = []

    # -------------------------
    # 1) Inversione 1X2
    # -------------------------
    inv = bool(row.get("INVERSION", False))
    inv_from = str(row.get("INV_FROM", "")).strip()
    inv_to = str(row.get("INV_TO", "")).strip()

    if inv and inv_from and inv_to:
        parts.append(f"⚠️ {inv_from}→{inv_to}")

    # -------------------------
    # 2) Drop lato 1 o 2
    # Consideriamo utile da 0.06 in su
    # -------------------------
    q1 = row.get("Q1_MOVE_DATA", {}) or {}
    q2 = row.get("Q2_MOVE_DATA", {}) or {}

    q1_dir = str(q1.get("dir", "")).strip()
    q2_dir = str(q2.get("dir", "")).strip()

    q1_abs = safe_float(q1.get("abs_diff", 0.0), 0.0)
    q2_abs = safe_float(q2.get("abs_diff", 0.0), 0.0)

    if q1_dir == "down" and q1_abs >= 0.06:
        parts.append("↓1")

    if q2_dir == "down" and q2_abs >= 0.06:
        parts.append("↓2")

    # -------------------------
    # 3) Mercati secondari
    # Mostriamo solo se movimento utile
    # -------------------------
    o25 = row.get("O25_MOVE_DATA", {}) or {}
    o05 = row.get("O05HT_MOVE_DATA", {}) or {}

    o25_label = str(o25.get("label", "")).strip()
    o05_label = str(o05.get("label", "")).strip()

    o25_abs = safe_float(o25.get("abs_diff", 0.0), 0.0)
    o05_abs = safe_float(o05.get("abs_diff", 0.0), 0.0)

    if o25_label and o25_abs >= 0.06:
        parts.append(f"O25 {o25_label}")

    if o05_label and o05_abs >= 0.06:
        parts.append(f"O05HT {o05_label}")

    # -------------------------
    # 4) Output finale
    # -------------------------
    return " • ".join(parts)

def compute_drop_diff(fid, mk):
    if fid not in st.session_state.odds_memory:
        return 0.0

    old_data = st.session_state.odds_memory.get(fid, {})
    if not isinstance(old_data, dict):
        return 0.0

    fav_is_home = mk["q1"] <= mk["q2"]
    old_q = safe_float(old_data.get("q1") if fav_is_home else old_data.get("q2"), 0.0)
    fav_now = min(mk["q1"], mk["q2"])

    if old_q > 0 and fav_now > 0 and old_q > fav_now:
        return round(old_q - fav_now, 3)
    return 0.0


def score_drop(drop_diff):
    if drop_diff >= 0.15:
        return 1.2
    if drop_diff >= 0.10:
        return 0.9
    if drop_diff >= 0.05:
        return 0.5
    return 0.0

def score_ptgg_signal(mk, s_h, s_a, fav, drop_diff):
    """
    PTGG = candidata da almeno 1 goal nel primo tempo.
    Logica:
    - entrambe devono avere una vera capacità di segnare PT
    - conta anche la tendenza avversaria a concedere PT
    - piccolo bonus da recupero 2T e da drop/inversion
    """
    score = 0.0

    home_ht_scored = s_h["avg_ht_scored_clean"]
    away_ht_scored = s_a["avg_ht_scored_clean"]

    home_ft_scored = s_h["avg_ft_scored_clean"]
    away_ft_scored = s_a["avg_ft_scored_clean"]

    home_concede_ht = s_h["ht_conceded_1plus_rate"]
    away_concede_ht = s_a["ht_conceded_1plus_rate"]

    # base scoring capacità di segnare PT
    score += band_score(home_ht_scored, 0.80, 1.35, 0.65, 1.50, core_pts=1.50, soft_pts=0.60)
    score += band_score(away_ht_scored, 0.80, 1.35, 0.65, 1.50, core_pts=1.50, soft_pts=0.60)

    # se entrambe hanno buona media PT segnati
    if home_ht_scored >= 0.80 and away_ht_scored >= 0.80:
        score += 1.20
    elif (home_ht_scored >= 1.00 and away_ht_scored >= 0.65) or (away_ht_scored >= 1.00 and home_ht_scored >= 0.65):
        score += 0.70

    # supporto FT goal fatti
    if home_ft_scored >= 1.50:
        score += 0.55
    elif home_ft_scored >= 1.30:
        score += 0.25

    if away_ft_scored >= 1.50:
        score += 0.55
    elif away_ft_scored >= 1.30:
        score += 0.25

    # avversari che concedono nel PT
    if home_concede_ht >= 0.50:
        score += 0.45
    elif home_concede_ht >= 0.38:
        score += 0.20

    if away_concede_ht >= 0.50:
        score += 0.45
    elif away_concede_ht >= 0.38:
        score += 0.20

    # continuità generale HT
    if s_h["ht_scored_1plus_rate"] >= 0.50:
        score += 0.40
    if s_a["ht_scored_1plus_rate"] >= 0.50:
        score += 0.40

    # mercato HT
    score += band_score(mk["o05ht"], 1.20, 1.40, 1.15, 1.48, core_pts=1.15, soft_pts=0.45)

    # bonus recupero media dal 2T
    if s_h["last_2h_zero"]:
        score += 0.20
    if s_a["last_2h_zero"]:
        score += 0.20

    # se nell'ultima non ha concesso nel 2T può "scaricare" prima
    if s_h["last_2h_conceded_zero"]:
        score += 0.10
    if s_a["last_2h_conceded_zero"]:
        score += 0.10

    # drop
    if drop_diff >= 0.20:
        score += 0.40
    elif drop_diff >= 0.10:
        score += 0.20

    # malus favorite troppo bassa
    if fav < 1.30:
        score -= 0.35

    # penalità rumore
    if home_ht_scored < 0.60:
        score -= 0.75
    if away_ht_scored < 0.60:
        score -= 0.75

    if s_h["ht_scored_1plus_rate"] < 0.38:
        score -= 0.55
    if s_a["ht_scored_1plus_rate"] < 0.38:
        score -= 0.55

    if s_h["ht_zero_rate"] >= 0.50:
        score -= 0.40
    if s_a["ht_zero_rate"] >= 0.50:
        score -= 0.40

    return round3(max(score, 0.0))


def score_pto15_signal(mk, s_h, s_a, fav, drop_diff):
    """
    PTO1.5 = candidata da 2+ goal nel primo tempo.
    Logica:
    - qui basta anche una squadra molto forte + una di supporto
    - oppure entrambe spinte
    """
    score = 0.0

    home_ht_scored = s_h["avg_ht_scored_clean"]
    away_ht_scored = s_a["avg_ht_scored_clean"]

    home_ft_scored = s_h["avg_ft_scored_clean"]
    away_ft_scored = s_a["avg_ft_scored_clean"]

    combined_ht_scored = (home_ht_scored + away_ht_scored) / 2

    # base intensità PT
    score += band_score(combined_ht_scored, 0.90, 1.45, 0.78, 1.60, core_pts=1.60, soft_pts=0.70)

    # doppia via:
    # 1) entrambe forti
    if home_ht_scored >= 0.90 and away_ht_scored >= 0.90:
        score += 1.35
    # 2) una dominante + una discreta
    elif (home_ht_scored >= 1.10 and away_ht_scored >= 0.60) or (away_ht_scored >= 1.10 and home_ht_scored >= 0.60):
        score += 1.15

    # frequenze gol fatti PT
    if s_h["ht_scored_1plus_rate"] >= 0.62:
        score += 0.45
    elif s_h["ht_scored_1plus_rate"] >= 0.50:
        score += 0.20

    if s_a["ht_scored_1plus_rate"] >= 0.62:
        score += 0.45
    elif s_a["ht_scored_1plus_rate"] >= 0.50:
        score += 0.20

    if s_h["ht_scored_2plus_rate"] >= 0.25:
        score += 0.25
    if s_a["ht_scored_2plus_rate"] >= 0.25:
        score += 0.25

    # supporto FT segnati
    if home_ft_scored >= 1.50 and away_ft_scored >= 1.50:
        score += 0.70
    elif (home_ft_scored >= 1.80 and away_ft_scored >= 1.20) or (away_ft_scored >= 1.80 and home_ft_scored >= 1.20):
        score += 0.45

    # avversari concedenti nel PT
    if s_h["ht_conceded_1plus_rate"] >= 0.50:
        score += 0.30
    if s_a["ht_conceded_1plus_rate"] >= 0.50:
        score += 0.30

    # mercato O1.5 HT
    score += band_score(mk["o15ht"], 2.00, 3.35, 1.85, 3.80, core_pts=1.10, soft_pts=0.40)

    # bonus recupero
    if s_h["last_2h_zero"]:
        score += 0.15
    if s_a["last_2h_zero"]:
        score += 0.15

    # drop forte
    if drop_diff >= 0.20:
        score += 0.45
    elif drop_diff >= 0.10:
        score += 0.20

    # malus favorita estrema
    if fav < 1.30:
        score -= 0.25

    # penalità
    if home_ht_scored < 0.55:
        score -= 0.85
    if away_ht_scored < 0.55:
        score -= 0.85

    if s_h["ht_scored_1plus_rate"] < 0.38:
        score -= 0.50
    if s_a["ht_scored_1plus_rate"] < 0.38:
        score -= 0.50

    if mk["o15ht"] > 4.10 and mk["o15ht"] != 0:
        score -= 0.30

    return round3(max(score, 0.0))


def score_pt_signal(mk, s_h, s_a):
    score = 0.0

    # =========================
    # BASE: COMBINED HT CLEAN
    # =========================
    combined_ht_clean = (s_h["avg_ht_clean"] + s_a["avg_ht_clean"]) / 2

    score += band_score(
        combined_ht_clean,
        1.02, 1.65,
        0.95, 1.80,
        core_pts=1.7,
        soft_pts=0.8
    )

    # =========================
    # FORZA HT PULITA DELLE DUE SQUADRE
    # =========================
    if s_h["avg_ht_clean"] >= 1.00 and s_a["avg_ht_clean"] >= 1.00:
        score += 1.7
    elif (s_h["avg_ht_clean"] >= 1.15 and s_a["avg_ht_clean"] >= 0.90) or \
         (s_a["avg_ht_clean"] >= 1.15 and s_h["avg_ht_clean"] >= 0.90):
        score += 1.0

    score += symmetry_bonus(
        s_h["avg_ht_clean"],
        s_a["avg_ht_clean"],
        tight=0.18,
        medium=0.35
    )

    # =========================
    # CONTINUITÀ HT
    # =========================
    if s_h["ht_1plus_rate"] >= 0.75 and s_a["ht_1plus_rate"] >= 0.75:
        score += 1.2
    elif s_h["ht_1plus_rate"] >= 0.62 and s_a["ht_1plus_rate"] >= 0.62:
        score += 0.8
    elif (s_h["ht_1plus_rate"] >= 0.75 and s_a["ht_1plus_rate"] >= 0.50) or \
         (s_a["ht_1plus_rate"] >= 0.75 and s_h["ht_1plus_rate"] >= 0.50):
        score += 0.45

    # =========================
    # MERCATO HT
    # =========================
    score += band_score(mk["o05ht"], 1.20, 1.38, 1.15, 1.46, core_pts=1.5, soft_pts=0.6)
    score += band_score(mk["o15ht"], 2.00, 3.40, 1.85, 4.00, core_pts=0.7, soft_pts=0.25)

    # =========================
    # SUPPORTO FT LEGGERO
    # =========================
    if s_h["avg_total_clean"] >= 1.45 and s_a["avg_total_clean"] >= 1.45:
        score += 0.45

    if s_h["ft_low_rate"] <= 0.25 and s_a["ft_low_rate"] <= 0.25:
        score += 0.25

    # =========================
    # BONUS ULTIMO MATCH SENZA GOAL 2T
    # =========================
    if s_h["last_2h_zero"] or s_a["last_2h_zero"]:
        score += 0.30

    # =========================
    # PENALITÀ RUMORE HT
    # =========================
    if s_h["ht_zero_rate"] >= 0.38:
        score -= 0.75
    if s_a["ht_zero_rate"] >= 0.38:
        score -= 0.75

    if s_h["avg_ht_clean"] < 0.85:
        score -= 0.75
    if s_a["avg_ht_clean"] < 0.85:
        score -= 0.75

    if s_h["ht_1plus_rate"] < 0.62:
        score -= 0.45
    if s_a["ht_1plus_rate"] < 0.62:
        score -= 0.45

    if combined_ht_clean < 0.95:
        score -= 0.60

    return round3(max(score, 0.0))


def score_over_signal(mk, s_h, s_a, fav, drop_diff):
    """
    OVER = segnale FT puro.
    Logica:
    - incrocio attacco/difesa tra goal fatti e goal subiti
    - doppio controllo su media sporca + media pulita
    - NON dipende dal PT, al massimo riceve un supporto leggero
    """

    score = 0.0

    # =========================
    # MEDIE FATTI / SUBITI
    # =========================
    home_scored = s_h["avg_ft_scored"]
    away_scored = s_a["avg_ft_scored"]
    home_conceded = s_h["avg_ft_conceded"]
    away_conceded = s_a["avg_ft_conceded"]

    home_scored_clean = s_h["avg_ft_scored_clean"]
    away_scored_clean = s_a["avg_ft_scored_clean"]
    home_conceded_clean = s_h["avg_ft_conceded_clean"]
    away_conceded_clean = s_a["avg_ft_conceded_clean"]

    # incroci principali
    cross_home_dirty = home_scored + away_conceded
    cross_away_dirty = away_scored + home_conceded

    cross_home_clean = home_scored_clean + away_conceded_clean
    cross_away_clean = away_scored_clean + home_conceded_clean

    combined_cross_dirty = (cross_home_dirty + cross_away_dirty) / 2
    combined_cross_clean = (cross_home_clean + cross_away_clean) / 2

    # =========================
    # GATE BASE PULITO
    # =========================
    if cross_home_clean >= 2.20:
        score += 1.10
    elif cross_home_clean >= 2.05:
        score += 0.45

    if cross_away_clean >= 2.20:
        score += 1.10
    elif cross_away_clean >= 2.05:
        score += 0.45

    if cross_home_clean >= 2.20 and cross_away_clean >= 2.20:
        score += 1.30
    elif (cross_home_clean >= 2.20 and cross_away_clean >= 2.00) or \
         (cross_away_clean >= 2.20 and cross_home_clean >= 2.00):
        score += 0.65

    # =========================
    # GATE BASE SPORCO
    # =========================
    if cross_home_dirty >= 2.35:
        score += 0.90
    elif cross_home_dirty >= 2.20:
        score += 0.35

    if cross_away_dirty >= 2.35:
        score += 0.90
    elif cross_away_dirty >= 2.20:
        score += 0.35

    if cross_home_dirty >= 2.35 and cross_away_dirty >= 2.35:
        score += 1.00
    elif (cross_home_dirty >= 2.35 and cross_away_dirty >= 2.15) or \
         (cross_away_dirty >= 2.35 and cross_home_dirty >= 2.15):
        score += 0.45

    # =========================
    # ATTACCHI VERI
    # =========================
    if home_scored_clean >= 1.25:
        score += 0.50
    elif home_scored_clean >= 1.05:
        score += 0.20

    if away_scored_clean >= 1.25:
        score += 0.50
    elif away_scored_clean >= 1.05:
        score += 0.20

    if home_scored_clean >= 1.15 and away_scored_clean >= 1.15:
        score += 0.55

    # =========================
    # DIFESE CHE CONCEDONO
    # =========================
    if away_conceded_clean >= 1.10:
        score += 0.40
    elif away_conceded_clean >= 0.95:
        score += 0.15

    if home_conceded_clean >= 1.10:
        score += 0.40
    elif home_conceded_clean >= 0.95:
        score += 0.15

    if away_conceded_clean >= 1.05 and home_conceded_clean >= 1.05:
        score += 0.40

    # =========================
    # CONTINUITÀ FT
    # =========================
    if s_h["ft_2plus_rate"] >= 0.75:
        score += 0.45
    elif s_h["ft_2plus_rate"] >= 0.62:
        score += 0.20

    if s_a["ft_2plus_rate"] >= 0.75:
        score += 0.45
    elif s_a["ft_2plus_rate"] >= 0.62:
        score += 0.20

    if s_h["ft_2plus_rate"] >= 0.62 and s_a["ft_2plus_rate"] >= 0.62:
        score += 0.45

    if s_h["ft_3plus_rate"] >= 0.50:
        score += 0.22
    if s_a["ft_3plus_rate"] >= 0.50:
        score += 0.22

    # =========================
    # MERCATO O2.5
    # =========================
    score += band_score(
        mk["o25"],
        1.52, 2.18,
        1.42, 2.40,
        core_pts=1.35,
        soft_pts=0.55
    )

    # piccola rifinitura quota favorita
    if 1.35 <= fav <= 2.20:
        score += 0.20

    # =========================
    # SUPPORTO HT LEGGERO
    # =========================
    combined_ht_scored_clean = (s_h["avg_ht_scored_clean"] + s_a["avg_ht_scored_clean"]) / 2
    if combined_ht_scored_clean >= 0.78:
        score += 0.15
    if combined_ht_scored_clean >= 0.92:
        score += 0.10

    # =========================
    # DROP
    # =========================
    if drop_diff >= 0.20:
        score += 0.40
    elif drop_diff >= 0.10:
        score += 0.18
    elif drop_diff >= 0.05:
        score += 0.08

    # =========================
    # MALUS FAVORITA ESTREMA
    # =========================
    if fav < 1.30:
        score -= 0.30

    # =========================
    # PENALITÀ STRUTTURALI
    # =========================
    if home_scored_clean < 1.00:
        score -= 0.65
    if away_scored_clean < 1.00:
        score -= 0.65

    if away_conceded_clean < 0.90:
        score -= 0.45
    if home_conceded_clean < 0.90:
        score -= 0.45

    if s_h["ft_low_rate"] >= 0.38:
        score -= 0.65
    if s_a["ft_low_rate"] >= 0.38:
        score -= 0.65

    if cross_home_clean < 2.00:
        score -= 0.60
    if cross_away_clean < 2.00:
        score -= 0.60

    if cross_home_dirty < 2.10:
        score -= 0.30
    if cross_away_dirty < 2.10:
        score -= 0.30

    return round3(max(score, 0.0))


def score_boost_signal(mk, s_h, s_a, pt_score, over_score, drop_diff):
    score = 0.0

    combined_ht_clean = (s_h["avg_ht_clean"] + s_a["avg_ht_clean"]) / 2
    combined_ft_clean = (s_h["avg_total_clean"] + s_a["avg_total_clean"]) / 2
    

    # =========================
    # BASE: eredita da PT + OVER
    # BOOST = over forte con supporto PT
    # =========================
    score += pt_score * 0.30
    score += over_score * 0.46

    # =========================
    # CONVERGENZA HT PULITA
    # =========================
    if s_h["avg_ht_clean"] >= 1.00 and s_a["avg_ht_clean"] >= 1.00:
        score += 0.95
    elif (s_h["avg_ht_clean"] >= 1.15 and s_a["avg_ht_clean"] >= 0.90) or \
         (s_a["avg_ht_clean"] >= 1.15 and s_h["avg_ht_clean"] >= 0.90):
        score += 0.55

    if s_h["ht_1plus_rate"] >= 0.75 and s_a["ht_1plus_rate"] >= 0.75:
        score += 0.70
    elif s_h["ht_1plus_rate"] >= 0.62 and s_a["ht_1plus_rate"] >= 0.62:
        score += 0.40

    # =========================
    # CONVERGENZA FT PULITA
    # =========================
    if s_h["avg_total_clean"] >= 1.60 and s_a["avg_total_clean"] >= 1.60:
        score += 0.95
    elif (s_h["avg_total_clean"] >= 1.85 and s_a["avg_total_clean"] >= 1.35) or \
         (s_a["avg_total_clean"] >= 1.85 and s_h["avg_total_clean"] >= 1.35):
        score += 0.50

    if s_h["ft_2plus_rate"] >= 0.75 and s_a["ft_2plus_rate"] >= 0.75:
        score += 0.65
    elif s_h["ft_2plus_rate"] >= 0.62 and s_a["ft_2plus_rate"] >= 0.62:
        score += 0.35

    if s_h["ft_3plus_rate"] >= 0.50 and s_a["ft_3plus_rate"] >= 0.50:
        score += 0.25

    # =========================
    # MERCATO BOOST
    # fascia core = match perfetto
    # fascia soft = match buono
    # =========================
    if 1.55 <= mk["o25"] <= 2.10 and 1.27 <= mk["o05ht"] <= 1.37:
        score += 0.80
    elif 1.50 <= mk["o25"] <= 2.20 and 1.25 <= mk["o05ht"] <= 1.40:
        score += 0.35

    # =========================
    # SUPPORTO COMBINATO
    # =========================
    if combined_ht_clean >= 1.05:
        score += 0.30
    if combined_ft_clean >= 1.75:
        score += 0.35

    # =========================
    # DROP
    # =========================
    score += score_drop(drop_diff) * 0.40

    # =========================
    # PENALITÀ RUMORE
    # =========================
    if s_h["ft_low_rate"] >= 0.38:
        score -= 0.75
    if s_a["ft_low_rate"] >= 0.38:
        score -= 0.75

    if s_h["ht_zero_rate"] >= 0.38:
        score -= 0.70
    if s_a["ht_zero_rate"] >= 0.38:
        score -= 0.70

    if s_h["avg_ht_clean"] < 0.85:
        score -= 0.60
    if s_a["avg_ht_clean"] < 0.85:
        score -= 0.60

    if s_h["avg_total_clean"] < 1.35:
        score -= 0.60
    if s_a["avg_total_clean"] < 1.35:
        score -= 0.60

    return round3(max(score, 0.0))

def score_gold_signal(mk, s_h, s_a, pt_score, over_score, fav, drop_diff, is_gold_zone):
    score = 0.0

    combined_ht_clean = (s_h["avg_ht_clean"] + s_a["avg_ht_clean"]) / 2
    combined_ft_clean = (s_h["avg_total_clean"] + s_a["avg_total_clean"]) / 2

    # =========================
    # BASE: eredita da PT + OVER, NON da BOOST
    # =========================
    score += pt_score * 0.24
    score += over_score * 0.32

    # =========================
    # QUOTE / ZONA GOLD
    # =========================
    if is_gold_zone:
        score += 0.95

    if 1.45 <= fav <= 1.80:
        score += 0.40
    elif 1.40 <= fav <= 1.86:
        score += 0.20

    # =========================
    # CONVERGENZA HT PULITA
    # =========================
    if s_h["avg_ht_clean"] >= 1.00 and s_a["avg_ht_clean"] >= 1.00:
        score += 0.75
    elif (s_h["avg_ht_clean"] >= 1.18 and s_a["avg_ht_clean"] >= 0.92) or \
         (s_a["avg_ht_clean"] >= 1.18 and s_h["avg_ht_clean"] >= 0.92):
        score += 0.35

    if s_h["ht_1plus_rate"] >= 0.75 and s_a["ht_1plus_rate"] >= 0.75:
        score += 0.55
    elif s_h["ht_1plus_rate"] >= 0.62 and s_a["ht_1plus_rate"] >= 0.62:
        score += 0.25

    # =========================
    # CONVERGENZA FT PULITA
    # =========================
    if s_h["avg_total_clean"] >= 1.70 and s_a["avg_total_clean"] >= 1.65:
        score += 0.85
    elif (s_h["avg_total_clean"] >= 1.95 and s_a["avg_total_clean"] >= 1.45) or \
         (s_a["avg_total_clean"] >= 1.95 and s_h["avg_total_clean"] >= 1.45):
        score += 0.40

    if s_h["ft_2plus_rate"] >= 0.75 and s_a["ft_2plus_rate"] >= 0.75:
        score += 0.55
    elif s_h["ft_2plus_rate"] >= 0.62 and s_a["ft_2plus_rate"] >= 0.62:
        score += 0.25

    if s_h["ft_3plus_rate"] >= 0.50 and s_a["ft_3plus_rate"] >= 0.50:
        score += 0.35

    # =========================
    # MERCATO CONVERGENTE
    # =========================
    if 1.60 <= mk["o25"] <= 2.12 and 1.22 <= mk["o05ht"] <= 1.36:
        score += 0.70
    elif 1.54 <= mk["o25"] <= 2.22 and 1.20 <= mk["o05ht"] <= 1.39:
        score += 0.30

    if combined_ht_clean >= 1.05:
        score += 0.30
    if combined_ft_clean >= 1.75:
        score += 0.30

    # =========================
    # DROP
    # =========================
    score += score_drop(drop_diff) * 0.45

    # =========================
    # PENALITÀ RUMORE
    # =========================
    if s_h["ft_low_rate"] >= 0.38:
        score -= 0.75
    if s_a["ft_low_rate"] >= 0.38:
        score -= 0.75

    if s_h["ht_zero_rate"] >= 0.38:
        score -= 0.70
    if s_a["ht_zero_rate"] >= 0.38:
        score -= 0.70

    if s_h["avg_ht_clean"] < 0.85:
        score -= 0.60
    if s_a["avg_ht_clean"] < 0.85:
        score -= 0.60

    if s_h["avg_total_clean"] < 1.35:
        score -= 0.60
    if s_a["avg_total_clean"] < 1.35:
        score -= 0.60

    return round3(max(score, 0.0))


def build_signal_package(fid, mk, s_h, s_a):
    fav = min(mk["q1"], mk["q2"])
    is_gold_zone = (1.40 <= fav <= 1.90)
    drop_diff = compute_drop_diff(fid, mk)

    combined_ht_clean = (s_h["avg_ht_clean"] + s_a["avg_ht_clean"]) / 2
    combined_ft_clean = (s_h["avg_total_clean"] + s_a["avg_total_clean"]) / 2
    combined_ht_scored_clean = (s_h["avg_ht_scored_clean"] + s_a["avg_ht_scored_clean"]) / 2

    ptgg_score = score_ptgg_signal(mk, s_h, s_a, fav, drop_diff)
    pto15_score = score_pto15_signal(mk, s_h, s_a, fav, drop_diff)

    # PT composito: il migliore guida, il secondo aggiunge supporto
    pt_score = max(ptgg_score, pto15_score) + (min(ptgg_score, pto15_score) * 0.18)

    over_score = score_over_signal(mk, s_h, s_a, fav, drop_diff)
    boost_score = score_boost_signal(mk, s_h, s_a, pt_score, over_score, drop_diff)
    gold_score = score_gold_signal(
        mk, s_h, s_a, pt_score, over_score,
        fav, drop_diff, is_gold_zone
    )
    tags = []

    if ptgg_score >= 4.00:
        tags.append("🎯PTGG")

    if pto15_score >= 4.00:
        tags.append("🔥PT1.5")

    if over_score >= 4.00 and combined_ht_scored_clean >= 0.66:
        tags.append("⚽ OVER")

    # =========================
    # BOOST GATES
    # =========================
    boost_has_pt = ("🎯PTGG" in tags or "🔥PT1.5" in tags)
    boost_has_over = ("⚽ OVER" in tags)

    boost_gate_ht = (
        (s_h["avg_ht_clean"] >= 1.00 and s_a["avg_ht_clean"] >= 1.00) or
        ((s_h["avg_ht_clean"] >= 1.15 and s_a["avg_ht_clean"] >= 0.90) or
         (s_a["avg_ht_clean"] >= 1.15 and s_h["avg_ht_clean"] >= 0.90))
    )

    boost_gate_ht_rates = (
        s_h["ht_1plus_rate"] >= 0.62 and
        s_a["ht_1plus_rate"] >= 0.62 and
        s_h["ht_zero_rate"] <= 0.38 and
        s_a["ht_zero_rate"] <= 0.38
    )

    boost_gate_ft = (
        (s_h["avg_total_clean"] >= 1.55 and s_a["avg_total_clean"] >= 1.50) or
        (s_a["avg_total_clean"] >= 1.55 and s_h["avg_total_clean"] >= 1.50)
    )

    boost_gate_ft_rates = (
        s_h["ft_2plus_rate"] >= 0.62 and
        s_a["ft_2plus_rate"] >= 0.62 and
        s_h["ft_low_rate"] <= 0.25 and
        s_a["ft_low_rate"] <= 0.25
    )

    boost_gate_market = (
        1.50 <= mk["o25"] <= 2.20 and
        1.25 <= mk["o05ht"] <= 1.40
    )

    if (
        boost_score >= 5.95
        and boost_has_pt
        and boost_has_over
        and pt_score >= 4.00
        and over_score >= 4.00
        and combined_ht_clean >= 1.02
        and combined_ft_clean >= 1.65
        and boost_gate_ht
        and boost_gate_ht_rates
        and boost_gate_ft
        and boost_gate_ft_rates
        and boost_gate_market
    ):
        tags.append("🚀 BOOST")

    # =========================
    # GOLD GATES - INDIPENDENTE
    # =========================
    gold_has_pt = ("🎯PTGG" in tags or "🔥PT1.5" in tags)
    gold_has_over = ("⚽ OVER" in tags)

    gold_gate_ht = (
        (s_h["avg_ht_clean"] >= 1.00 and s_a["avg_ht_clean"] >= 1.00) or
        ((s_h["avg_ht_clean"] >= 1.18 and s_a["avg_ht_clean"] >= 0.92) or
         (s_a["avg_ht_clean"] >= 1.18 and s_h["avg_ht_clean"] >= 0.92))
    )

    gold_gate_ht_rates = (
        s_h["ht_1plus_rate"] >= 0.62 and
        s_a["ht_1plus_rate"] >= 0.62 and
        s_h["ht_zero_rate"] <= 0.38 and
        s_a["ht_zero_rate"] <= 0.38
    )

    gold_gate_ft = (
        (s_h["avg_total_clean"] >= 1.65 and s_a["avg_total_clean"] >= 1.60) or
        (s_a["avg_total_clean"] >= 1.65 and s_h["avg_total_clean"] >= 1.60)
    )

    gold_gate_ft_rates = (
        s_h["ft_2plus_rate"] >= 0.62 and
        s_a["ft_2plus_rate"] >= 0.62 and
        s_h["ft_low_rate"] <= 0.30 and
        s_a["ft_low_rate"] <= 0.30
    )

    gold_gate_market = (
        1.56 <= mk["o25"] <= 2.15 and
        1.21 <= mk["o05ht"] <= 1.38
    )

    gold_extra_ok = (
        drop_diff >= 0.05 or
        (combined_ht_clean >= 1.02 and combined_ft_clean >= 1.72)
    )

    if (
        gold_score >= 6.55
        and gold_has_pt
        and gold_has_over
        and pt_score >= 4.05
        and over_score >= 4.10
        and is_gold_zone
        and combined_ht_clean >= 1.00
        and combined_ft_clean >= 1.68
        and gold_gate_ht
        and gold_gate_ht_rates
        and gold_gate_ft
        and gold_gate_ft_rates
        and gold_gate_market
        and gold_extra_ok
    ):
        tags.insert(0, "⚽⭐ GOLD")

    # =========================
    # PROBE
    # =========================
    if (
        "⚽ OVER" not in tags
        and "🚀 BOOST" not in tags
        and "⚽⭐ GOLD" not in tags
        and combined_ft_clean >= 1.48
        and s_h["ft_2plus_rate"] >= 0.50
        and s_a["ft_2plus_rate"] >= 0.50
        and 1.55 <= mk["o25"] <= 2.30
        and mk["o05ht"] <= 1.42
        and combined_ht_clean >= 0.78
        and over_score >= 3.60
    ):
        tags.append("🐟O")

    if (
        "🚀 BOOST" not in tags
        and "⚽⭐ GOLD" not in tags
        and not (("🎯PTGG" in tags or "🔥PT1.5" in tags) and "⚽ OVER" in tags)
        and 1.38 <= fav <= 2.05
        and combined_ht_clean >= 0.88
        and combined_ft_clean >= 1.52
        and 1.52 <= mk["o25"] <= 2.35
        and pt_score >= 3.70
        and over_score >= 3.70
    ):
        tags.append("🐟G")

    if drop_diff >= 0.05:
        tags.append(f"📉-{drop_diff:.2f}")

    strong_tag_count = (
        int("🎯PTGG" in tags) +
        int("🔥PT1.5" in tags) +
        int("⚽ OVER" in tags) +
        int("🚀 BOOST" in tags) +
        int("⚽⭐ GOLD" in tags)
    )

    max_score = max(ptgg_score, pto15_score, over_score, boost_score, gold_score)

    return {
        "tags": tags,
        "scores": {
            "ptgg": ptgg_score,
            "pto15": pto15_score,
            "pt": pt_score,   # PT composito   
            "over": over_score,
            "boost": boost_score,
            "gold": gold_score,
            "max": round3(max_score),
        },
        "drop_diff": round3(drop_diff),
        "fav_quote": round3(fav),
        "is_gold_zone": is_gold_zone,
        "strong_tag_count": strong_tag_count
    }


def should_keep_match(signal_pack):
    tags = signal_pack.get("tags", [])
    scores = signal_pack.get("scores", {})

    ptgg_score = safe_float(scores.get("ptgg"), 0.0)
    pto15_score = safe_float(scores.get("pto15"), 0.0)
    pt_score = safe_float(scores.get("pt"), 0.0)
    over_score = safe_float(scores.get("over"), 0.0)
    boost_score = safe_float(scores.get("boost"), 0.0)
    gold_score = safe_float(scores.get("gold"), 0.0)
    max_score = safe_float(scores.get("max"), 0.0)

    has_gold = any("GOLD" in t for t in tags)
    has_boost = any("BOOST" in t for t in tags)
    has_ptgg = "🎯PTGG" in tags
    has_pt15 = "🔥PT1.5" in tags
    has_over = any("OVER" in t for t in tags)
    has_probe_o = "🐟O" in tags
    has_probe_g = "🐟G" in tags

    if has_gold and gold_score >= 6.55:
        return True

    if has_boost and boost_score >= 5.95 and (pt_score >= 4.00 or over_score >= 4.00):
        return True

    if has_ptgg and has_over and ptgg_score >= 4.00 and over_score >= 4.00:
        return True

    if has_pt15 and has_over and pto15_score >= 4.00 and over_score >= 4.00:
        return True

    if has_ptgg and not has_over and ptgg_score >= 4.00:
        return True

    if has_pt15 and not has_over and pto15_score >= 4.00:
        return True

    if has_over and not (has_ptgg or has_pt15) and over_score >= 4.00:
        return True

    if has_probe_o and max_score >= 3.00:
        return True

    if has_probe_g and max_score >= 3.25:
        return True

    return False
# ==========================================
# DETAILS / DAY PAYLOAD HELPERS
# ==========================================
def save_match_details_file():
    payload = {
        "updated_at": now_rome().strftime("%Y-%m-%d %H:%M:%S"),
        "details": st.session_state.match_details
    }
    with open(DETAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
    return payload


def get_target_dates():
    return [(now_rome().date() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]


def build_day_results(day_num):
    target_date = get_target_dates()[day_num - 1]
    results = [r for r in st.session_state.scan_results if r.get("Data") == target_date]
    results.sort(key=lambda x: x.get("Ora", "99:99"))
    return results


def build_day_details_payload(day_num):
    target_date = get_target_dates()[day_num - 1]
    details = {
        k: v for k, v in st.session_state.match_details.items()
        if v.get("date") == target_date
    }
    return {
        "updated_at": now_rome().strftime("%Y-%m-%d %H:%M:%S"),
        "day": day_num,
        "date": target_date,
        "details": details
    }


def sync_day_outputs_to_github(day_num, update_main=False):
    day_results = build_day_results(day_num)
    details_payload = build_day_details_payload(day_num)

    status_day = upload_day_to_github(day_num, day_results)
    status_details = upload_details_to_github(day_num, details_payload)

    if update_main:
        status_main = upload_to_github_main(day_results)
    else:
        status_main = None

    return status_main, status_day, status_details
    
def upload_snapshot_to_github(payload):
    try:
        github_write_json(
            REMOTE_SNAPSHOT_FILE,
            payload,
            "Update snapshot database"
        )
    except Exception as e:
        print(f"Snapshot upload error: {e}")

def build_daily_snapshots_from_rolling(snapshot_payload):
    """
    Crea snapshot_day1 ... snapshot_day5 a partire dal rolling snapshot centrale.
    Non tocca le open: usa i record già presenti nel rolling.
    """
    try:
        odds_map = snapshot_payload.get("odds", {}) or {}
    except Exception:
        odds_map = {}

    target_dates = get_target_dates()

    for day_num in range(1, 6):
        day_date = target_dates[day_num - 1]
        day_odds = {}

        for fid, rec in odds_map.items():
            if not isinstance(rec, dict):
                continue

            rec_date = str(rec.get("last_seen_date", "")).strip()
            if rec_date == day_date:
                day_odds[str(fid)] = rec

        day_payload = {
            "day": day_num,
            "date": day_date,
            "updated_at": now_rome().strftime("%Y-%m-%d %H:%M:%S"),
            "odds": day_odds,
        }

        out_file = DATA_DIR / f"snapshot_day{day_num}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(day_payload, f, indent=4, ensure_ascii=False)

        upload_snapshot_day_to_github(day_num, day_payload)

        print(f"📦 snapshot_day{day_num}.json aggiornato: {len(day_odds)} match")

def load_snapshot_from_github():
    """
    Fallback: carica lo snapshot da GitHub se il file locale
    non esiste o non contiene odds valide.
    """
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            try:
                token = st.secrets["GITHUB_TOKEN"]
            except Exception:
                token = None

        if not token:
            print("⚠️ GITHUB_TOKEN mancante: impossibile caricare snapshot da GitHub", flush=True)
            return None

        g = Github(token)
        repo = g.get_repo("dweezil78/arabsniper2")
        contents = repo.get_contents(REMOTE_SNAPSHOT_FILE)
        raw = contents.decoded_content.decode("utf-8")
        payload = json.loads(raw)

        if not isinstance(payload, dict):
            return None

        odds = payload.get("odds", {}) or {}
        if not isinstance(odds, dict):
            return None

        print(f"✅ Snapshot caricato da GitHub: {len(odds)} fixture", flush=True)
        return payload

    except Exception as e:
        print(f"⚠️ Errore load_snapshot_from_github: {e}", flush=True)
        return None

# ==========================================
# MODAL DETTAGLI MATCH
# ==========================================
@st.dialog("🔎 Dettagli partita", width="large")
def show_match_modal(fixture_id: str):
    detail = st.session_state.match_details.get(str(fixture_id))

    if not detail:
        st.warning("Dettagli non disponibili per questa partita.")
        return

    avg = detail.get("averages", {})
    flags = detail.get("flags", {})
    scores = detail.get("scores", {})

    st.markdown(f"## {detail['match']}")
    st.write(f"**Data:** {detail['date']}  |  **Ora:** {detail['time']}")
    st.write(f"**Lega:** {detail['league']} ({detail['country']})")
    st.write(f"**Tag:** {' '.join(detail.get('tags', []))}")

    m1, m2, m3 = st.columns(3)
    m1.metric("1", f"{detail['markets'].get('q1', 0):.2f}")
    m2.metric("X", f"{detail['markets'].get('qx', 0):.2f}")
    m3.metric("2", f"{detail['markets'].get('q2', 0):.2f}")

    m4, m5, m6 = st.columns(3)
    m4.metric("O2.5", f"{detail['markets'].get('o25', 0):.2f}")
    m5.metric("O0.5 HT", f"{detail['markets'].get('o05ht', 0):.2f}")
    m6.metric("O1.5 HT", f"{detail['markets'].get('o15ht', 0):.2f}")

    st.markdown("---")
    st.subheader("📊 Medie e flag")

    a1, a2, a3 = st.columns(3)
    a1.metric("AVG FT Home", f"{avg.get('home_avg_ft', 0):.2f}")
    a2.metric("AVG FT Away", f"{avg.get('away_avg_ft', 0):.2f}")
    a3.metric("AVG HT Combo", f"{avg.get('combined_ht_avg', 0):.2f}")

    st.write(
        f"**AVG HT Home/Away:** "
        f"{avg.get('home_avg_ht', 0):.2f} | "
        f"{avg.get('away_avg_ht', 0):.2f}"
    )

    cross_home_dirty = avg.get("home_avg_ft_scored", 0) + avg.get("away_avg_ft_conceded", 0)
    cross_away_dirty = avg.get("away_avg_ft_scored", 0) + avg.get("home_avg_ft_conceded", 0)

    cross_home_clean = avg.get("home_avg_ft_scored_clean", 0) + avg.get("away_avg_ft_conceded_clean", 0)
    cross_away_clean = avg.get("away_avg_ft_scored_clean", 0) + avg.get("home_avg_ft_conceded_clean", 0)

    st.write(
        f"**CROSS FT DIRTY H/A:** "
        f"{cross_home_dirty:.2f} | {cross_away_dirty:.2f}"
    )
    st.write(
        f"**CROSS FT CLEAN H/A:** "
        f"{cross_home_clean:.2f} | {cross_away_clean:.2f}"
    )
    st.markdown("### 🧪 Metriche pulite e frequenze")

    b1, b2 = st.columns(2)

    with b1:
        st.write(
            f"**AVG FT CLEAN Home/Away:** "
            f"{avg.get('home_avg_ft_clean', 0):.2f} | "
            f"{avg.get('away_avg_ft_clean', 0):.2f}"
        )
        st.write(
            f"**FT 2+ Rate Home/Away:** "
            f"{avg.get('home_ft_2plus_rate', 0):.2f} | "
            f"{avg.get('away_ft_2plus_rate', 0):.2f}"
        )
        st.write(
            f"**FT 3+ Rate Home/Away:** "
            f"{avg.get('home_ft_3plus_rate', 0):.2f} | "
            f"{avg.get('away_ft_3plus_rate', 0):.2f}"
        )
        st.write(
            f"**FT LOW Rate Home/Away:** "
            f"{avg.get('home_ft_low_rate', 0):.2f} | "
            f"{avg.get('away_ft_low_rate', 0):.2f}"
        )
        st.write(
            f"**FT Peak Count Home/Away:** "
            f"{avg.get('home_ft_peak_count', 0)} | "
            f"{avg.get('away_ft_peak_count', 0)}"
        )

    with b2:
        st.write(
            f"**AVG HT CLEAN Home/Away:** "
            f"{avg.get('home_avg_ht_clean', 0):.2f} | "
            f"{avg.get('away_avg_ht_clean', 0):.2f}"
        )
        st.write(
            f"**HT 1+ Rate Home/Away:** "
            f"{avg.get('home_ht_1plus_rate', 0):.2f} | "
            f"{avg.get('away_ht_1plus_rate', 0):.2f}"
        )
        st.write(
            f"**HT ZERO Rate Home/Away:** "
            f"{avg.get('home_ht_zero_rate', 0):.2f} | "
            f"{avg.get('away_ht_zero_rate', 0):.2f}"
        )

    st.write(
        f"**Fav quota:** {flags.get('fav_quote', 0):.2f} | "
        f"**Gold zone:** {'✅' if flags.get('is_gold_zone') else '❌'} | "
        f"**Home last 2H zero:** {'✅' if flags.get('home_last_2h_zero') else '❌'} | "
        f"**Away last 2H zero:** {'✅' if flags.get('away_last_2h_zero') else '❌'} | "
        f"**Drop:** {flags.get('drop_diff', 0):.2f}"
    )

    if scores:
        st.markdown("---")
        st.subheader("🧠 Score interni V24.1")
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("PTGG", f"{scores.get('ptgg', 0):.2f}")
        s2.metric("PT1.5", f"{scores.get('pto15', 0):.2f}")
        s3.metric("OVER", f"{scores.get('over', 0):.2f}")
        s4.metric("BOOST", f"{scores.get('boost', 0):.2f}")
        s5.metric("GOLD", f"{scores.get('gold', 0):.2f}")

    st.markdown("---")
    c_home, c_away = st.columns(2)

    with c_home:
        st.markdown(f"### 🏠 Ultime 8 {detail['home_team']}")
        df_home = pd.DataFrame(detail.get("home_last_8", []))
        if not df_home.empty:
            st.dataframe(df_home, use_container_width=True, hide_index=True)
        else:
            st.info("Nessun dato home disponibile.")

    with c_away:
        st.markdown(f"### ✈️ Ultime 8 {detail['away_team']}")
        df_away = pd.DataFrame(detail.get("away_last_8", []))
        if not df_away.empty:
            st.dataframe(df_away, use_container_width=True, hide_index=True)
        else:
            st.info("Nessun dato away disponibile.")

# ==========================================
# SCAN CORE
# ==========================================
def run_full_scan(horizon=None, snap=False, update_main_site=False, show_success=True):
    use_horizon = horizon if horizon is not None else HORIZON
    target_dates = get_target_dates()

    with st.spinner(f"🚀 Analisi mercati {target_dates[use_horizon - 1]}..."):
        with requests.Session() as s:
            target_date = target_dates[use_horizon - 1]

            # ==========================================
            # 1) CHIAMATA API PROTETTA
            # ==========================================
            res = api_get(s, "fixtures", {"date": target_date, "timezone": "Europe/Rome"})
            if not res or not isinstance(res, dict):
                print(f"❌ API non valida per day {use_horizon} ({target_date}) -> skip totale", flush=True)
                if show_success:
                    st.error(f"❌ API non valida per {target_date}. Nessun file aggiornato.")
                return

            api_response = res.get("response", [])
            if not api_response or not isinstance(api_response, list):
                print(f"❌ API vuota per day {use_horizon} ({target_date}) -> skip totale", flush=True)
                if show_success:
                    st.error(f"❌ API vuota per {target_date}. Nessun file aggiornato.")
                return

            day_fx = [
                f for f in api_response
                if f.get("fixture", {}).get("status", {}).get("short") == "NS"
                and not is_blacklisted_league(f.get("league", {}).get("name", ""))
            ]

            if not day_fx:
                print(f"❌ Nessun fixture NS valido per day {use_horizon} ({target_date}) -> skip totale", flush=True)
                if show_success:
                    st.error(f"❌ Nessun match pre-match valido trovato per {target_date}. Nessun file aggiornato.")
                return

            st.session_state.available_countries = sorted(
                list(set(st.session_state.available_countries) | {
                    fx.get("league", {}).get("country", "N/D") for fx in day_fx
                })
            )

            # ==========================================
            # 2) SNAP SOLO DAY 1, MA PROTETTO
            # ==========================================
            if snap and use_horizon == 1:
                try:
                    snap_bar = st.progress(0, text="📌 SNAPSHOT ROLLING DAY1+DAY2+DAY3+DAY4+DAY5...")
                    build_rolling_multiday_snapshot(s)
                    snap_bar.progress(1.0)
                    time.sleep(0.3)
                    snap_bar.empty()
                except Exception as e:
                    print(f"❌ Errore snapshot rolling: {e}", flush=True)
                    if show_success:
                        st.error(f"❌ Errore snapshot: {e}")
                    return

            final_list = []
            details_map = dict(st.session_state.match_details)

            pb = st.progress(0, text="🚀 ANALISI SEGNALI E MEDIE...")

            # ==========================================
            # 3) ANALISI MATCH
            # ==========================================
            for i, f in enumerate(day_fx):
                pb.progress((i + 1) / len(day_fx) if day_fx else 1.0)

                try:
                    cnt = f.get("league", {}).get("country", "N/D")
                    if cnt in st.session_state.config["excluded"]:
                        continue

                    fid = str(f.get("fixture", {}).get("id"))
                    if not fid or fid == "None":
                        continue

                    mk = extract_elite_markets(s, fid)
                    if not mk or mk == "SKIP" or safe_float(mk.get("q1"), 0.0) == 0:
                        continue

                    home_team = f.get("teams", {}).get("home", {})
                    away_team = f.get("teams", {}).get("away", {})

                    if not home_team.get("id") or not away_team.get("id"):
                        continue

                    fixture_local_dt = fixture_dt_rome(f.get("fixture", {}))
                    ora_local = fixture_local_dt.strftime("%H:%M") if fixture_local_dt else str(
                        f.get("fixture", {}).get("date", "")
                    )[11:16]

                    s_h = get_team_performance(s, home_team["id"])
                    s_a = get_team_performance(s, away_team["id"])
                    if not s_h or not s_a:
                        continue

                    combined_ht_avg = (s_h["avg_ht"] + s_a["avg_ht"]) / 2
                    combined_ht_clean = (s_h["avg_ht_clean"] + s_a["avg_ht_clean"]) / 2
                    combined_ft_clean = (s_h["avg_total_clean"] + s_a["avg_total_clean"]) / 2

                    if (
                        combined_ht_clean < 0.72
                        and combined_ft_clean < 1.20
                        and s_h["ht_1plus_rate"] < 0.38
                        and s_a["ht_1plus_rate"] < 0.38
                        and s_h["ft_2plus_rate"] < 0.38
                        and s_a["ft_2plus_rate"] < 0.38
                    ):
                        continue

                    signal_pack = build_signal_package(fid, mk, s_h, s_a)
                    tags = signal_pack["tags"]

                    if not should_keep_match(signal_pack):
                        continue

                    fav = signal_pack["fav_quote"]
                    is_gold_zone = signal_pack["is_gold_zone"]

                    quote_pack = build_quote_movement_package(fid, mk)

                    row = {
                        "Ora": ora_local,
                        "Lega": f"{f.get('league', {}).get('name', 'N/D')} ({cnt})",
                        "Match": f"{home_team.get('name', 'N/D')} - {away_team.get('name', 'N/D')}",
                        "FAV": "✅" if is_gold_zone else "❌",
                        "1X2": f"{safe_float(mk.get('q1'), 0):.1f}|{safe_float(mk.get('qx'), 0):.1f}|{safe_float(mk.get('q2'), 0):.1f}",
                        "O2.5": f"{safe_float(mk.get('o25'), 0):.2f}",
                        "O0.5H": f"{safe_float(mk.get('o05ht'), 0):.2f}",
                        "O1.5H": f"{safe_float(mk.get('o15ht'), 0):.2f}",
                        "AVG FT": f"{s_h['avg_total']:.1f}|{s_a['avg_total']:.1f}",
                        "AVG HT": f"{s_h['avg_ht']:.1f}|{s_a['avg_ht']:.1f}",
                        "Info": " ".join(tags),
                        "Data": target_date,
                        "Fixture_ID": f.get("fixture", {}).get("id"),
                        
                        "Q1_OPEN": quote_pack["Q1_OPEN"],
                        "QX_OPEN": quote_pack["QX_OPEN"],
                        "Q2_OPEN": quote_pack["Q2_OPEN"],
                        "O25_OPEN": quote_pack["O25_OPEN"],
                        "O05HT_OPEN": quote_pack["O05HT_OPEN"],
                        "O15HT_OPEN": quote_pack["O15HT_OPEN"],

                        "Q1_CURR": quote_pack["Q1_CURR"],
                        "QX_CURR": quote_pack["QX_CURR"],
                        "Q2_CURR": quote_pack["Q2_CURR"],
                        "O25_CURR": quote_pack["O25_CURR"],
                        "O05HT_CURR": quote_pack["O05HT_CURR"],
                        "O15HT_CURR": quote_pack["O15HT_CURR"],

                        "Q1_MOVE": quote_pack["Q1_MOVE"],
                        "QX_MOVE": quote_pack["QX_MOVE"],
                        "Q2_MOVE": quote_pack["Q2_MOVE"],
                        "O25_MOVE": quote_pack["O25_MOVE"],
                        "O05HT_MOVE": quote_pack["O05HT_MOVE"],
                        "O15HT_MOVE": quote_pack["O15HT_MOVE"],

                        "INVERSION": quote_pack["INVERSION"],
                        "INV_FROM": quote_pack["INV_FROM"],
                        "INV_TO": quote_pack["INV_TO"],
                        "FAV_OPEN": quote_pack["FAV_OPEN"],
                        "FAV_CURRENT": quote_pack["FAV_CURRENT"]
                    }
                    
                    row["MOVE_SUMMARY"] = build_movement_summary(row)
                    
                    final_list.append(row)

                    details_map[fid] = {
                        "fixture_id": f.get("fixture", {}).get("id"),
                        "date": target_date,
                        "time": ora_local,
                        "league": f.get("league", {}).get("name", "N/D"),
                        "country": cnt,
                        "match": f"{home_team.get('name', 'N/D')} - {away_team.get('name', 'N/D')}",
                        "home_team": home_team.get("name", "N/D"),
                        "away_team": away_team.get("name", "N/D"),
                        "markets": {
                            "q1": safe_float(mk.get("q1"), 0),
                            "qx": safe_float(mk.get("qx"), 0),
                            "q2": safe_float(mk.get("q2"), 0),
                            "o25": safe_float(mk.get("o25"), 0),
                            "o05ht": safe_float(mk.get("o05ht"), 0),
                            "o15ht": safe_float(mk.get("o15ht"), 0)
                        },
                        "averages": {
                            "home_avg_ft": round(s_h["avg_total"], 3),
                            "away_avg_ft": round(s_a["avg_total"], 3),
                            "home_avg_ht": round(s_h["avg_ht"], 3),
                            "away_avg_ht": round(s_a["avg_ht"], 3),
                            "combined_ht_avg": round(combined_ht_avg, 3),

                            "home_avg_ft_clean": round(s_h["avg_total_clean"], 3),
                            "away_avg_ft_clean": round(s_a["avg_total_clean"], 3),
                            "home_avg_ht_clean": round(s_h["avg_ht_clean"], 3),
                            "away_avg_ht_clean": round(s_a["avg_ht_clean"], 3),

                            "home_ft_2plus_rate": round(s_h["ft_2plus_rate"], 3),
                            "away_ft_2plus_rate": round(s_a["ft_2plus_rate"], 3),
                            "home_ft_3plus_rate": round(s_h["ft_3plus_rate"], 3),
                            "away_ft_3plus_rate": round(s_a["ft_3plus_rate"], 3),
                            "home_ft_low_rate": round(s_h["ft_low_rate"], 3),
                            "away_ft_low_rate": round(s_a["ft_low_rate"], 3),

                            "home_ht_1plus_rate": round(s_h["ht_1plus_rate"], 3),
                            "away_ht_1plus_rate": round(s_a["ht_1plus_rate"], 3),
                            "home_ht_zero_rate": round(s_h["ht_zero_rate"], 3),
                            "away_ht_zero_rate": round(s_a["ht_zero_rate"], 3),

                            "home_ft_peak_count": int(s_h["ft_peak_count"]),
                            "away_ft_peak_count": int(s_a["ft_peak_count"]),

                            "home_avg_ht_scored": round(s_h["avg_ht_scored"], 3),
                            "away_avg_ht_scored": round(s_a["avg_ht_scored"], 3),
                            "home_avg_ht_scored_clean": round(s_h["avg_ht_scored_clean"], 3),
                            "away_avg_ht_scored_clean": round(s_a["avg_ht_scored_clean"], 3),

                            "home_avg_ht_conceded": round(s_h["avg_ht_conceded"], 3),
                            "away_avg_ht_conceded": round(s_a["avg_ht_conceded"], 3),
                            "home_avg_ht_conceded_clean": round(s_h["avg_ht_conceded_clean"], 3),
                            "away_avg_ht_conceded_clean": round(s_a["avg_ht_conceded_clean"], 3),

                            "home_avg_ft_scored": round(s_h["avg_ft_scored"], 3),
                            "away_avg_ft_scored": round(s_a["avg_ft_scored"], 3),
                            "home_avg_ft_scored_clean": round(s_h["avg_ft_scored_clean"], 3),
                            "away_avg_ft_scored_clean": round(s_a["avg_ft_scored_clean"], 3),

                            "home_avg_ft_conceded": round(s_h["avg_ft_conceded"], 3),
                            "away_avg_ft_conceded": round(s_a["avg_ft_conceded"], 3),
                            "home_avg_ft_conceded_clean": round(s_h["avg_ft_conceded_clean"], 3),
                            "away_avg_ft_conceded_clean": round(s_a["avg_ft_conceded_clean"], 3),

                            "home_ht_scored_1plus_rate": round(s_h["ht_scored_1plus_rate"], 3),
                            "away_ht_scored_1plus_rate": round(s_a["ht_scored_1plus_rate"], 3),
                            "home_ht_scored_2plus_rate": round(s_h["ht_scored_2plus_rate"], 3),
                            "away_ht_scored_2plus_rate": round(s_a["ht_scored_2plus_rate"], 3),
                            "home_ht_conceded_1plus_rate": round(s_h["ht_conceded_1plus_rate"], 3),
                            "away_ht_conceded_1plus_rate": round(s_a["ht_conceded_1plus_rate"], 3),

                        },    
                        "flags": {
                            "fav_quote": round(fav, 3),
                            "is_gold_zone": is_gold_zone,
                            "home_last_2h_zero": s_h["last_2h_zero"],
                            "away_last_2h_zero": s_a["last_2h_zero"],
                            "drop_diff": signal_pack["drop_diff"]
                        },
                        "scores": signal_pack["scores"],
                        "tags": tags,
                        "home_last_8": get_team_last_matches(s, home_team["id"]),
                        "away_last_8": get_team_last_matches(s, away_team["id"])
                    }

                    time.sleep(0.2)

                except Exception as e:
                    print(f"⚠️ Errore su fixture {f.get('fixture', {}).get('id', 'N/D')}: {e}", flush=True)
                    continue

            pb.empty()

            # ==========================================
            # 4) PROTEZIONE FORTE CONTRO SALVATAGGI SPORCHI
            # ==========================================
            existing_day_results = [
                r for r in st.session_state.scan_results
                if r.get("Data") == target_date
            ]

            # Se non trova nulla, non distruggere file esistenti
            if not final_list:
                print(
                    f"⚠️ Nessun match valido trovato per day {use_horizon} ({target_date}) "
                    f"-> mantengo i file esistenti, nessuna sovrascrittura.",
                    flush=True
                )
                if show_success:
                    st.warning(f"⚠️ Nessun match valido per {target_date}. File esistenti mantenuti.")
                return

            # Se ci sono già dati per quel giorno e il nuovo scan è troppo povero, non salvare
            if existing_day_results and len(final_list) < 3:
                print(
                    f"⚠️ Troppi pochi match trovati ({len(final_list)}) per day {use_horizon} ({target_date}) "
                    f"con dati già esistenti -> skip salvataggio prudenziale.",
                    flush=True
                )
                if show_success:
                    st.warning(
                        f"⚠️ Trovati solo {len(final_list)} match validi per {target_date}. "
                        f"Per sicurezza non aggiorno i file esistenti."
                    )
                return

            # Se esistono dati per quel giorno e il nuovo scan è molto più piccolo del vecchio, skip
            if existing_day_results and len(final_list) < max(3, int(len(existing_day_results) * 0.35)):
                print(
                    f"⚠️ Nuovo scan troppo ridotto: {len(final_list)} vs vecchio {len(existing_day_results)} "
                    f"per day {use_horizon} ({target_date}) -> skip salvataggio prudenziale.",
                    flush=True
                )
                if show_success:
                    st.warning(
                        f"⚠️ Nuovo scan anomalo per {target_date}: {len(final_list)} match contro "
                        f"{len(existing_day_results)} esistenti. Nessun aggiornamento eseguito."
                    )
                return

            # ==========================================
            # 5) SALVATAGGIO LOCALE SICURO
            # ==========================================
            old_day_results = [
                r for r in st.session_state.scan_results
                if r.get("Data") == target_date
            ]

            merged_day_results = merge_day_rows(old_day_results, final_list)

            other_days_results = [
                r for r in st.session_state.scan_results
                if r.get("Data") != target_date
            ]

            new_scan_results = other_days_results + merged_day_results
            new_scan_results.sort(key=lambda x: (x.get("Data", ""), x.get("Ora", "99:99")))

            try:
                with open(DB_FILE, "w", encoding="utf-8") as f:
                    json.dump({"results": new_scan_results}, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Errore salvataggio DB locale: {e}", flush=True)
                if show_success:
                    st.error(f"❌ Errore salvataggio DB locale: {e}")
                return

            st.session_state.scan_results = new_scan_results
            st.session_state.match_details = details_map

            try:
                save_match_details_file()
            except Exception as e:
                print(f"❌ Errore salvataggio details locale: {e}", flush=True)
                if show_success:
                    st.error(f"❌ Errore salvataggio details locale: {e}")
                return

            # ==========================================
            # 6) SYNC GITHUB
            # ==========================================
            try:
                status_main, status_day, status_details = sync_day_outputs_to_github(
                    day_num=use_horizon,
                    update_main=update_main_site
                )
            except Exception as e:
                print(f"❌ Errore sync GitHub: {e}", flush=True)
                if show_success:
                    st.error(f"❌ Errore sync GitHub: {e}")
                return

            # ==========================================
            # 7) FEEDBACK UI
            # ==========================================
            if show_success:
                if update_main_site:
                    if status_main == "SUCCESS":
                        st.success("✅ data.json aggiornato!")
                    else:
                        st.error(f"❌ Errore data.json: {status_main}")

                if status_day == "SUCCESS":
                    st.success(f"✅ {REMOTE_DAY_FILES[use_horizon]} aggiornato!")
                else:
                    st.error(f"❌ Errore {REMOTE_DAY_FILES[use_horizon]}: {status_day}")

                if status_details == "SUCCESS":
                    st.success(f"✅ {REMOTE_DETAILS_FILES[use_horizon]} aggiornato!")
                else:
                    st.error(f"❌ Errore {REMOTE_DETAILS_FILES[use_horizon]}: {status_details}")

            if "--auto" not in sys.argv and "--fast" not in sys.argv and "--day2-refresh" not in sys.argv:
                time.sleep(2)
                st.rerun()

# ==========================================
# AUTO BUILD 5 GIORNI
# ==========================================
def run_nightly_multiday_build():
    print("🚀 Avvio scan notturno multi-day...")

    print("🔄 Rotazione file day1-day5...")
    try:
        import subprocess
        import sys
        subprocess.run([sys.executable, str(BASE_DIR / "3appdays_runner.py"), "--rotate-live"], check=True)
        print("✅ Rotazione file completata.")
    except Exception as e:
        print(f"❌ Errore rotazione file day: {e}")
        raise

    try:
        print("📌 DAY 1: SNAPSHOT rolling + refresh quote + update data.json/data_day1/details_day1")
        run_full_scan(horizon=1, snap=True, update_main_site=True, show_success=False)

        print("📆 DAY 2: scan statico + update data_day2/details_day2")
        run_full_scan(horizon=2, snap=False, update_main_site=False, show_success=False)

        print("📆 DAY 3: scan statico + update data_day3/details_day3")
        run_full_scan(horizon=3, snap=False, update_main_site=False, show_success=False)

        print("📆 DAY 4: scan statico + update data_day4/details_day4")
        run_full_scan(horizon=4, snap=False, update_main_site=False, show_success=False)

        print("📌 DAY 5: scan statico + update data_day5/details_day5")
        run_full_scan(horizon=5, snap=False, update_main_site=False, show_success=False)

        snapshot_payload = load_existing_snapshot_payload()
        build_daily_snapshots_from_rolling(snapshot_payload)
        
        print("✅ Build multi-day completata.")
        
    except Exception as e:
        print(f"❌ Errore build multi-day: {e}")
        raise

# ==========================================
# UI SIDEBAR
# ==========================================
st.sidebar.header("👑 Arab Sniper V24.1 Multi-Day WEB")
HORIZON = st.sidebar.selectbox("Orizzonte Temporale:", options=[1, 2, 3, 4, 5], index=0)
target_dates = get_target_dates()

all_discovered = sorted(list(set(st.session_state.get("available_countries", []))))
if st.session_state.scan_results:
    historical_cnt = {r["Lega"].split("(")[-1].replace(")", "") for r in st.session_state.scan_results}
    all_discovered = sorted(list(set(all_discovered) | historical_cnt))

if all_discovered:
    new_ex = st.sidebar.multiselect(
        "Escludi Nazioni:",
        options=all_discovered,
        default=[c for c in st.session_state.config.get("excluded", []) if c in all_discovered]
    )
    if st.sidebar.button("💾 SALVA CONFIG"):
        st.session_state.config["excluded"] = new_ex
        save_config()
        st.rerun()

if last_snap_ts:
    st.sidebar.success(f"✅ SNAPSHOT: {last_snap_ts}")
else:
    st.sidebar.warning("⚠️ SNAPSHOT ASSENTE")

st.sidebar.markdown("---")
st.sidebar.caption(f"DB: {Path(DB_FILE).name}")
st.sidebar.caption(f"SNAP: {Path(SNAP_FILE).name}")
st.sidebar.caption(f"DETAILS: {Path(DETAILS_FILE).name}")
st.sidebar.caption("GitHub: data.json + data_day1/2/3/4/5 + details_day1/2/3/4/5")

# ==========================================
# UI MAIN
# ==========================================
c1, c2 = st.columns(2)
if c1.button("📌 SNAP + SCAN"):
    run_full_scan(horizon=HORIZON, snap=(HORIZON == 1), update_main_site=(HORIZON == 1))
if c2.button("🚀 SCAN VELOCE"):
    run_full_scan(horizon=HORIZON, snap=False, update_main_site=(HORIZON == 1))

if st.session_state.selected_fixture_for_modal:
    show_match_modal(st.session_state.selected_fixture_for_modal)

if st.session_state.scan_results:
    df = pd.DataFrame(st.session_state.scan_results)
    full_view = df[df["Data"] == target_dates[HORIZON - 1]]

    if not full_view.empty:
        full_view = full_view.sort_values(by=["Ora", "Match"])
        view = full_view.copy()

        if "MOVE_SUMMARY" not in view.columns:
            view["MOVE_SUMMARY"] = ""

        if "Info" in view.columns and "MOVE_SUMMARY" in view.columns:
            view["Info"] = view.apply(
                lambda r: (
                    f"{r['Info']} | {r['MOVE_SUMMARY']}"
                    if str(r.get("MOVE_SUMMARY", "")).strip()
                    else str(r.get("Info", ""))
                ),
                axis=1
            )
            
        def build_1x2_visual(row):
            q1_open = safe_float(row.get("Q1_OPEN"), 0.0)
            qx_open = safe_float(row.get("QX_OPEN"), 0.0)
            q2_open = safe_float(row.get("Q2_OPEN"), 0.0)

            q1_curr = safe_float(row.get("Q1_CURR"), 0.0)
            qx_curr = safe_float(row.get("QX_CURR"), 0.0)
            q2_curr = safe_float(row.get("Q2_CURR"), 0.0)

            q1_move = str(row.get("Q1_MOVE", "")).strip()
            qx_move = str(row.get("QX_MOVE", "")).strip()
            q2_move = str(row.get("Q2_MOVE", "")).strip()

            def fmt_line(label, open_q, move_txt, curr_q):
                open_s = f"{open_q:.2f}" if open_q > 0 else "-"
                curr_s = f"{curr_q:.2f}" if curr_q > 0 else "-"
                mid = move_txt if move_txt else "→0.00"
                return f"<div><b>{label}</b> {open_s} {mid} {curr_s}</div>"

            return f"""
            <div style="line-height:1.25; white-space:pre-line; text-align:left;">
                {fmt_line("1", q1_open, q1_move, q1_curr)}
                {fmt_line("X", qx_open, qx_move, qx_curr)}
                {fmt_line("2", q2_open, q2_move, q2_curr)}
            </div>
            """

            return f"""
            <div style="
                display:flex;
                align-items:flex-start;
                justify-content:center;
                gap:10px;
                white-space:nowrap;
            ">
                {outcome_block("1", q1_open, q1_curr, q1_data)}
                {outcome_block("X", qx_open, qx_curr, qx_data)}
                {outcome_block("2", q2_open, q2_curr, q2_data)}
            </div>
            """

        def build_o25_visual(row):
            move = str(row.get("O25_MOVE", "")).strip()
            current = str(row.get("O2.5", "")).strip()

            if move:
                return f"""
                <div style="line-height:1.15; white-space:pre-line;">
                    {move}
                </div>
                """
            return current

        view["1X2_VIS"] = view.apply(build_1x2_visual, axis=1)
        view["O25_VIS"] = view.apply(build_o25_visual, axis=1)

        # Rimuoviamo colonne tecniche che non vogliamo mostrare in tabella
        cols_to_drop = [
            "Data", "Fixture_ID",

            "Q1_OPEN", "QX_OPEN", "Q2_OPEN",
            "O25_OPEN", "O05HT_OPEN", "O15HT_OPEN",

            "Q1_CURR", "QX_CURR", "Q2_CURR",
            "O25_CURR", "O05HT_CURR", "O15HT_CURR",

            "Q1_MOVE_DATA", "QX_MOVE_DATA", "Q2_MOVE_DATA",
            "O25_MOVE_DATA", "O05HT_MOVE_DATA", "O15HT_MOVE_DATA",

            "Q1_MOVE", "QX_MOVE", "Q2_MOVE",
            "O25_MOVE", "O05HT_MOVE", "O15HT_MOVE",

            "INVERSION", "INV_FROM", "INV_TO",
            "FAV_OPEN", "FAV_CURRENT",

            "MOVE_SUMMARY"
        ]
        view = view.drop(columns=[c for c in cols_to_drop if c in view.columns], errors="ignore")

        if "1X2" in view.columns:
            view["1X2"] = view["1X2_VIS"]

        if "O2.5" in view.columns:
            view["O2.5"] = view["O25_VIS"]

        view = view.drop(columns=["1X2_VIS", "O25_VIS"], errors="ignore")

        st.markdown("""
            <style>
                .main-container { width: 100%; max-height: 800px; overflow: auto; border: 1px solid #444; border-radius: 8px; background-color: #0e1117; }
                .mobile-table { width: 100%; min-width: 1000px; border-collapse: separate; border-spacing: 0; font-family: sans-serif; font-size: 11px; }
                .mobile-table th { position: sticky; top: 0; background: #1a1c23; color: #00e5ff; z-index: 10; padding: 12px 5px; border-bottom: 2px solid #333; border-right: 1px solid #333; }
                .mobile-table td { padding: 8px 5px; border-bottom: 1px solid #333; border-right: 1px solid #333; text-align: center; white-space: nowrap; vertical-align: middle; }
                .mobile-table td div { white-space: pre-line; }
                .row-gold {
                    background: linear-gradient(90deg, #FFD700, #FFC300) !important;
                    color: #000000 !important;
                    font-weight: 700;
                    border-left: 5px solid #ff9900;
                }

                .row-gold td {
                    box-shadow: inset 0 0 6px rgba(255, 200, 0, 0.6);
                }

                .row-boost {
                    background: linear-gradient(90deg, #0f5132, #198754) !important;
                    color: #ffffff !important;
                    font-weight: 600;
                    border-left: 5px solid #00ff88;
                }

                .row-over {
                    background-color: #d1f7e3 !important;
                    color: #003d2e !important;
                    font-weight: 500;
                }

                .row-pt {
                    background-color: #d6e4ff !important;
                    color: #002b5c !important;
                    font-weight: 500;
                }

                .row-probe {
                    background-color: #f3e8ff !important;
                    color: #4b0082 !important;
                    font-style: italic;
                    opacity: 0.92;
                }

                .row-std {
                    background-color: #ffffff !important;
                    color: #000000 !important;
                }
            </style>
        """, unsafe_allow_html=True)

        def get_row_class(info):
            if "GOLD" in info:
                return "row-gold"
            if "BOOST" in info:
                return "row-boost"
            if "OVER" in info:
                return "row-over"
            if "PT" in info:
                return "row-pt"
            if "🐟" in info:
                return "row-probe"
            return "row-std"

        html = '<div class="main-container"><table class="mobile-table"><thead><tr>'
        html += ''.join(f'<th>{c}</th>' for c in view.columns)
        html += '</tr></thead><tbody>'

        for _, row in view.iterrows():
            cls = get_row_class(row["Info"])
            html += f'<tr class="{cls}">' + ''.join(f'<td>{v}</td>' for v in row) + '</tr>'

        html += '</tbody></table></div>'
        st.markdown(html, unsafe_allow_html=True)

        st.markdown("---")
        st.subheader("🔎 Dettagli partite")

        for _, row in full_view.iterrows():
            fid = str(row["Fixture_ID"])
            c_btn, c_ora, c_match, c_lega = st.columns([1, 1.3, 4, 3])

            with c_btn:
                if st.button("🔎", key=f"open_modal_{fid}", help="Apri dettagli match"):
                    st.session_state.selected_fixture_for_modal = fid
                    st.rerun()

            with c_ora:
                st.write(row["Ora"])

            with c_match:
                st.write(row["Match"])

            with c_lega:
                st.write(row["Lega"])

        st.markdown("---")
        d1, d2, d3 = st.columns(3)
        d1.download_button(
            "💾 CSV",
            full_view.to_csv(index=False).encode("utf-8"),
            f"arab_{target_dates[HORIZON - 1]}.csv"
        )
        d2.download_button(
            "🌐 HTML",
            html.encode("utf-8"),
            f"arab_{target_dates[HORIZON - 1]}.html"
        )
        d3.download_button(
            "🧠 DETAILS JSON",
            json.dumps(
                {
                    k: v for k, v in st.session_state.match_details.items()
                    if v.get("date") == target_dates[HORIZON - 1]
                },
                indent=4,
                ensure_ascii=False
            ).encode("utf-8"),
            f"details_{target_dates[HORIZON - 1]}.json"
        )
else:
    st.info("Esegui uno scan.")

# =========================================================
# MERGE HELPERS (STEP 3)
# =========================================================

def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(str(x).replace(",", "."))
    except Exception:
        return default


def build_curr_pack_from_row(row: dict) -> dict:
    return {
        "Q1_CURR": safe_float(row.get("Q1", row.get("Q1_CURR", 0))),
        "QX_CURR": safe_float(row.get("QX", row.get("QX_CURR", 0))),
        "Q2_CURR": safe_float(row.get("Q2", row.get("Q2_CURR", 0))),
        "O25_CURR": safe_float(row.get("O2.5", row.get("O25_CURR", 0))),
        "O05HT_CURR": safe_float(row.get("O0.5H", row.get("O05HT_CURR", 0))),
        "O15HT_CURR": safe_float(row.get("O1.5H", row.get("O15HT_CURR", 0))),
    }

def build_open_pack_from_row(row: dict) -> dict:
    return {
        "Q1_OPEN": safe_float(row.get("Q1_OPEN", row.get("Q1", 0))),
        "QX_OPEN": safe_float(row.get("QX_OPEN", row.get("QX", 0))),
        "Q2_OPEN": safe_float(row.get("Q2_OPEN", row.get("Q2", 0))),
        "O25_OPEN": safe_float(row.get("O25_OPEN", row.get("O2.5", 0))),
        "O05HT_OPEN": safe_float(row.get("O05HT_OPEN", row.get("O0.5H", 0))),
        "O15HT_OPEN": safe_float(row.get("O15HT_OPEN", row.get("O1.5H", 0))),
    }

def build_merge_base_row(row: dict) -> dict:
    curr_pack = build_curr_pack_from_row(row)
    open_pack = build_open_pack_from_row(row)

    merged = dict(row)

    merged.update(open_pack)
    merged.update(curr_pack)

    if "status" not in merged:
        merged["status"] = "active"

    if "missing_count" not in merged:
        merged["missing_count"] = 0

    if "Fixture_ID" in merged and "fixture_id" not in merged:
        merged["fixture_id"] = merged["Fixture_ID"]

    return merged

def merge_existing_and_new_row(old_row: dict, new_row: dict) -> dict:
    old_row = dict(old_row or {})
    new_row = build_merge_base_row(new_row or {})

    merged = dict(old_row)
    merged.update(new_row)

    # Le OPEN non vanno sovrascritte se esistono già nel vecchio record
    for key in ["Q1_OPEN", "QX_OPEN", "Q2_OPEN", "O25_OPEN", "O05HT_OPEN", "O15HT_OPEN"]:
        old_val = safe_float(old_row.get(key), 0.0)
        new_val = safe_float(new_row.get(key), 0.0)
        merged[key] = old_val if old_val > 0 else new_val

    # Le CURRENT invece si aggiornano sempre dal nuovo scan
    for key in ["Q1_CURR", "QX_CURR", "Q2_CURR", "O25_CURR", "O05HT_CURR", "O15HT_CURR"]:
        merged[key] = safe_float(new_row.get(key), 0.0)

    # Tracking base
    merged["status"] = "active"
    merged["missing_count"] = 0

    if "Fixture_ID" in new_row:
        merged["Fixture_ID"] = new_row["Fixture_ID"]

    if "fixture_id" not in merged and "Fixture_ID" in merged:
        merged["fixture_id"] = merged["Fixture_ID"]

    return merged

def mark_row_as_stale(row: dict) -> dict:
    stale = dict(row or {})

    stale["status"] = "stale"
    stale["missing_count"] = int(stale.get("missing_count", 0)) + 1

    if "fixture_id" not in stale and "Fixture_ID" in stale:
        stale["fixture_id"] = stale["Fixture_ID"]

    return stale

def merge_day_rows(old_rows: list, new_rows: list) -> list:
    old_rows = old_rows or []
    new_rows = new_rows or []

    old_map = {}
    for row in old_rows:
        fid = row.get("Fixture_ID", row.get("fixture_id"))
        if fid is not None:
            old_map[str(fid)] = dict(row)

    new_map = {}
    for row in new_rows:
        fid = row.get("Fixture_ID", row.get("fixture_id"))
        if fid is not None:
            new_map[str(fid)] = build_merge_base_row(row)

    merged_map = {}

    # 1) aggiorna o crea le fixture presenti nel nuovo scan
    for fid, new_row in new_map.items():
        if fid in old_map:
            merged_map[fid] = merge_existing_and_new_row(old_map[fid], new_row)
        else:
            merged_map[fid] = build_merge_base_row(new_row)

    # 2) le fixture vecchie assenti nel nuovo scan non si cancellano
    for fid, old_row in old_map.items():
        if fid not in merged_map:
            merged_map[fid] = mark_row_as_stale(old_row)

    merged_rows = list(merged_map.values())

    merged_rows.sort(
        key=lambda r: (
            str(r.get("Data", "")),
            str(r.get("Ora", "99:99")),
            str(r.get("Match", "")),
        )
    )

    return merged_rows

def load_results_from_json_file(path: str) -> list:
    try:
        if not os.path.exists(path):
            return []

        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if isinstance(payload, dict):
            results = payload.get("results", [])
            if isinstance(results, list):
                return results

        return []
    except Exception:
        return []

def build_merged_day_payload(existing_file_path: str, new_rows: list, day_num: int = None, target_date: str = None) -> dict:
    old_rows = load_results_from_json_file(existing_file_path)
    merged_rows = merge_day_rows(old_rows, new_rows)

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "results": merged_rows,
    }

    if day_num is not None:
        payload["day"] = day_num

    if target_date is not None:
        payload["date"] = target_date

    return payload

def save_merged_day_json(existing_file_path: str, new_rows: list, day_num: int = None, target_date: str = None) -> dict:
    payload = build_merged_day_payload(
        existing_file_path=existing_file_path,
        new_rows=new_rows,
        day_num=day_num,
        target_date=target_date,
    )

    with open(existing_file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload

# ==========================================
# LOGICA ESECUZIONE AUTOMATICA GITHUB ACTIONS
# ==========================================
if __name__ == "__main__":
    if "--auto" in sys.argv:
        print("🚀 Avvio Scan Automatico Notturno Multi-Day...")
        HORIZON = 1
        run_nightly_multiday_build()
        print("✅ Scan completo terminato: data.json + data_day1/2/3/4/5 + details_day1/2/3/4/5 aggiornati.")

    elif "--fast" in sys.argv:
        HORIZON = 1
        print("⚡ Avvio Scan Veloce Automatico (solo Day 1)...")
        run_full_scan(horizon=1, snap=False, update_main_site=True, show_success=False)
        print("✅ Scan veloce terminato: data.json + data_day1 + details_day1 aggiornati.")

    elif "--day2-refresh" in sys.argv:
        HORIZON = 2
        print("🌙 Avvio Refresh Serale Day 2...")
        run_full_scan(horizon=2, snap=False, update_main_site=False, show_success=False)
        print("✅ Refresh Day 2 terminato: data_day2 + details_day2 aggiornati.")
