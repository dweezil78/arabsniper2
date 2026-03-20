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
DB_FILE = str(BASE_DIR / "arab_sniper_database.json")
SNAP_FILE = str(BASE_DIR / "arab_snapshot_database.json")
CONFIG_FILE = str(BASE_DIR / "nazioni_config.json")
DETAILS_FILE = str(BASE_DIR / "match_details.json")

DEFAULT_EXCLUDED = ["Thailand", "Indonesia", "India", "Kenya", "Morocco", "Rwanda", "Nigeria", "Oman", "Algeria", "UAE"]
LEAGUE_BLACKLIST = ["u19", "u20", "youth", "women", "friendly", "carioca", "paulista", "mineiro"]
ROLLING_SNAPSHOT_HORIZONS = [1, 2, 3, 4, 5]

REMOTE_MAIN_FILE = "data/data.json"

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


def load_db():
    today = now_rome().strftime("%Y-%m-%d")
    ts = None

    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f).get("results", [])
                st.session_state.scan_results = [r for r in data if r.get("Data", "") >= today]
        except Exception:
            pass

    if os.path.exists(SNAP_FILE):
        try:
            with open(SNAP_FILE, "r", encoding="utf-8") as f:
                snap_data = json.load(f)
                st.session_state.odds_memory = snap_data.get("odds", {})
                ts = snap_data.get("timestamp", "N/D")
        except Exception:
            pass

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
        return None

    for attempt in range(2):
        try:
            r = session.get(
                f"https://v3.football.api-sports.io/{path}",
                headers=HEADERS,
                params=params,
                timeout=20
            )
            if r.status_code == 200:
                return r.json()
            time.sleep(1)
        except Exception:
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


def load_existing_snapshot_payload():
    if os.path.exists(SNAP_FILE):
        try:
            with open(SNAP_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
                if isinstance(payload, dict):
                    payload.setdefault("odds", {})
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
    Salva la baseline quote di tutti i fixture Day1+Day2+Day3+Day4+Day5.
    Se un fixture_id esiste già, NON lo sovrascrive:
    così il drop resta ancorato alla prima quota vista.
    """
    target_dates = get_target_dates()
    existing_payload = load_existing_snapshot_payload()
    existing_odds = existing_payload.get("odds", {}) or {}

    new_odds = dict(existing_odds)
    active_fixture_ids = set()

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

            if fid not in new_odds:
                new_odds[fid] = {
                    "q1": mk["q1"],
                    "q2": mk["q2"],
                    "first_seen_date": target_date,
                    "first_seen_horizon": horizon,
                    "first_seen_ts": now_rome().strftime("%Y-%m-%d %H:%M:%S")
                }
            else:
                if isinstance(new_odds[fid], dict):
                    new_odds[fid]["last_seen_date"] = target_date
                    new_odds[fid]["last_seen_horizon"] = horizon
                    new_odds[fid]["last_seen_ts"] = now_rome().strftime("%Y-%m-%d %H:%M:%S")

        time.sleep(0.15)

    cleaned_odds = {}
    for fid, data in new_odds.items():
        if fid in active_fixture_ids:
            cleaned_odds[fid] = data

    payload = {
        "odds": cleaned_odds,
        "timestamp": now_rome().strftime("%H:%M"),
        "updated_at": now_rome().strftime("%Y-%m-%d %H:%M:%S"),
        "coverage": "rolling_day1_day5"
    }

    st.session_state.odds_memory = cleaned_odds
    save_snapshot_file(payload)
    return payload


def get_team_last_matches(session, tid):
    cache_key = str(tid)
    if cache_key in st.session_state.team_last_matches_cache:
        return st.session_state.team_last_matches_cache[cache_key]

    res = api_get(session, "fixtures", {"team": tid, "last": 8, "status": "FT"})
    fx = res.get("response", []) if res else []

    last_matches = []
    for f in fx:
        home_name = f.get("teams", {}).get("home", {}).get("name", "N/D")
        away_name = f.get("teams", {}).get("away", {}).get("name", "N/D")
        gh = f.get("goals", {}).get("home", 0)
        ga = f.get("goals", {}).get("away", 0)
        hth = f.get("score", {}).get("halftime", {}).get("home", 0)
        hta = f.get("score", {}).get("halftime", {}).get("away", 0)

        last_matches.append({
            "date": str(f.get("fixture", {}).get("date", ""))[:10],
            "league": f.get("league", {}).get("name", "N/D"),
            "match": f"{home_name} - {away_name}",
            "ht": f"{hth}-{hta}",
            "ft": f"{gh}-{ga}",
            "total_ht_goals": (hth or 0) + (hta or 0),
            "total_ft_goals": (gh or 0) + (ga or 0)
        })

    st.session_state.team_last_matches_cache[cache_key] = last_matches
    return last_matches


def get_team_performance(session, tid):
    if str(tid) in st.session_state.team_stats_cache:
        return st.session_state.team_stats_cache[str(tid)]

    res = api_get(session, "fixtures", {"team": tid, "last": 8, "status": "FT"})
    fx = res.get("response", []) if res else []
    if not fx:
        return None

    act = len(fx)
    tht, gf, gs = 0, 0, 0

    for f in fx:
        ht_data = f.get("score", {}).get("halftime", {})
        tht += (ht_data.get("home") or 0) + (ht_data.get("away") or 0)

        is_home = f["teams"]["home"]["id"] == tid
        gf += (f["goals"]["home"] or 0) if is_home else (f["goals"]["away"] or 0)
        gs += (f["goals"]["away"] or 0) if is_home else (f["goals"]["home"] or 0)

    last_f = fx[0]
    ft_sum = (last_f.get("goals", {}).get("home") or 0) + (last_f.get("goals", {}).get("away") or 0)
    ht_sum = (last_f.get("score", {}).get("halftime", {}).get("home") or 0) + (last_f.get("score", {}).get("halftime", {}).get("away") or 0)
    last_2h_zero = ((ft_sum - ht_sum) == 0)

    stats = {
        "avg_ht": tht / act,
        "avg_total": (gf + gs) / act,
        "last_2h_zero": last_2h_zero
    }

    st.session_state.team_stats_cache[str(tid)] = stats
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


def score_pt_signal(mk, s_h, s_a, combined_ht_avg):
    score = 0.0

    score += band_score(combined_ht_avg, 1.12, 1.70, 1.05, 1.90, core_pts=1.5, soft_pts=0.8)

    if s_h["avg_ht"] >= 1.10 and s_a["avg_ht"] >= 1.10:
        score += 1.6
    elif (s_h["avg_ht"] >= 1.25 and s_a["avg_ht"] >= 0.95) or (s_a["avg_ht"] >= 1.25 and s_h["avg_ht"] >= 0.95):
        score += 1.0

    score += symmetry_bonus(s_h["avg_ht"], s_a["avg_ht"], tight=0.20, medium=0.40)

    score += band_score(mk["o05ht"], 1.20, 1.40, 1.15, 1.48, core_pts=1.6, soft_pts=0.7)
    score += band_score(mk["o15ht"], 2.00, 3.60, 1.80, 4.20, core_pts=0.8, soft_pts=0.3)

    if s_h["last_2h_zero"] or s_a["last_2h_zero"]:
        score += 0.8

    if s_h["avg_total"] >= 1.20 and s_a["avg_total"] >= 1.20:
        score += 0.5

    return round3(score)


def score_over_signal(mk, s_h, s_a, combined_ht_avg, fav, drop_diff):
    score = 0.0

    if s_h["avg_total"] >= 1.55 and s_a["avg_total"] >= 1.55:
        score += 2.2
    elif s_h["avg_total"] >= 1.45 and s_a["avg_total"] >= 1.45:
        score += 1.4
    elif (s_h["avg_total"] >= 1.80 and s_a["avg_total"] >= 1.20) or (s_a["avg_total"] >= 1.80 and s_h["avg_total"] >= 1.20):
        score += 1.0

    score += symmetry_bonus(s_h["avg_total"], s_a["avg_total"], tight=0.28, medium=0.50)

    score += band_score(mk["o25"], 1.51, 2.37, 1.40, 2.55, core_pts=1.8, soft_pts=0.8)

    if combined_ht_avg >= 1.10:
        score += 0.7
    if combined_ht_avg >= 1.20:
        score += 0.3

    if 1.35 <= fav <= 2.20:
        score += 0.4

    score += score_drop(drop_diff) * 0.7

    return round3(score)


def score_boost_signal(mk, s_h, s_a, pt_score, over_score, drop_diff, combined_ht_avg):
    score = 0.0
    score += pt_score * 0.38
    score += over_score * 0.48

    if (s_h["avg_ht"] >= 1.30 and s_a["avg_ht"] >= 1.00) or (s_a["avg_ht"] >= 1.30 and s_h["avg_ht"] >= 1.00):
        score += 0.55
    elif s_h["avg_ht"] >= 1.15 and s_a["avg_ht"] >= 1.15:
        score += 0.35

    if s_h["avg_total"] >= 1.65 and s_a["avg_total"] >= 1.65:
        score += 0.55
    elif (s_h["avg_total"] >= 1.95 and s_a["avg_total"] >= 1.35) or (s_a["avg_total"] >= 1.95 and s_h["avg_total"] >= 1.35):
        score += 0.25

    if 1.60 <= mk["o25"] <= 2.12 and 1.22 <= mk["o05ht"] <= 1.36:
        score += 0.55
    elif 1.55 <= mk["o25"] <= 2.20 and 1.20 <= mk["o05ht"] <= 1.38:
        score += 0.20

    if combined_ht_avg >= 1.16:
        score += 0.35

    score += score_drop(drop_diff) * 0.45
    return round3(score)


def score_gold_signal(mk, s_h, s_a, pt_score, over_score, boost_score, fav, drop_diff, is_gold_zone, combined_ht_avg):
    score = 0.0
    score += pt_score * 0.22
    score += over_score * 0.30
    score += boost_score * 0.34

    if is_gold_zone:
        score += 0.85

    if combined_ht_avg >= 1.18 and s_h["avg_total"] >= 1.55 and s_a["avg_total"] >= 1.50:
        score += 0.45

    if 1.42 <= fav <= 1.82:
        score += 0.35

    if drop_diff >= 0.10:
        score += 0.55
    elif drop_diff >= 0.05:
        score += 0.25

    return round3(score)


def build_signal_package(fid, mk, s_h, s_a, combined_ht_avg):
    fav = min(mk["q1"], mk["q2"])
    is_gold_zone = (1.40 <= fav <= 1.90)
    drop_diff = compute_drop_diff(fid, mk)

    pt_score = score_pt_signal(mk, s_h, s_a, combined_ht_avg)
    over_score = score_over_signal(mk, s_h, s_a, combined_ht_avg, fav, drop_diff)
    boost_score = score_boost_signal(mk, s_h, s_a, pt_score, over_score, drop_diff, combined_ht_avg)
    gold_score = score_gold_signal(mk, s_h, s_a, pt_score, over_score, boost_score, fav, drop_diff, is_gold_zone, combined_ht_avg)

    tags = []
    probe_tags = []

    if (fav < 1.75) and (s_h["avg_total"] >= 1.0 and s_a["avg_total"] >= 1.0):
        probe_tags.append("🐟O")

    if (2.0 <= mk["q1"] <= 3.5) and (2.0 <= mk["q2"] <= 3.5) and (s_h["avg_total"] >= 1.0 and s_a["avg_total"] >= 1.0):
        probe_tags.append("🐟G")

    if pt_score >= 4.1:
        tags.append("🎯PT")

    if over_score >= 4.0:
        tags.append("⚽ OVER")

    boost_gate_ht = (
        (s_h["avg_ht"] >= 1.28 and s_a["avg_ht"] >= 1.00) or
        (s_a["avg_ht"] >= 1.28 and s_h["avg_ht"] >= 1.00) or
        (s_h["avg_ht"] >= 1.12 and s_a["avg_ht"] >= 1.12)
    )
    boost_gate_ft = (
        (s_h["avg_total"] >= 1.60 and s_a["avg_total"] >= 1.55) or
        (s_a["avg_total"] >= 1.60 and s_h["avg_total"] >= 1.55)
    )
    boost_gate_market = (1.58 <= mk["o25"] <= 2.18 and 1.21 <= mk["o05ht"] <= 1.37)
    ft_convergence = (
        (s_h["avg_total"] >= 1.45 and s_a["avg_total"] >= 1.45)
        or
        (s_h["avg_total"] >= 1.80 and s_a["avg_total"] >= 1.20)
        or
        (s_a["avg_total"] >= 1.80 and s_h["avg_total"] >= 1.20)
    )

    if (
        boost_score >= 5.85
        and pt_score >= 4.00
        and over_score >= 4.15
        and combined_ht_avg >= 1.14
        and boost_gate_ht
        and boost_gate_ft
        and boost_gate_market
        and ft_convergence
    ):
        tags.append("🚀 BOOST")

    gold_gate_core = (
        (s_h["avg_total"] >= 1.55 and s_a["avg_total"] >= 1.50)
        and (s_h["avg_ht"] >= 1.05 and s_a["avg_ht"] >= 1.05)
        and combined_ht_avg >= 1.16
    )
    gold_gate_quote = (1.42 <= fav <= 1.85)
    gold_gate_extra = (
        drop_diff >= 0.05 or
        (
            s_h["avg_total"] >= 1.75 and
            s_a["avg_total"] >= 1.65 and
            combined_ht_avg >= 1.20
        )
    )

    if (
        gold_score >= 6.75
        and boost_score >= 5.95
        and pt_score >= 4.00
        and over_score >= 4.20
        and is_gold_zone
        and gold_gate_core
        and gold_gate_quote
        and gold_gate_extra
    ):
        tags.insert(0, "⚽⭐ GOLD")

    if drop_diff >= 0.05:
        tags.append(f"📉-{drop_diff:.2f}")

    tags.extend(probe_tags)

    primary_signal_count = sum(1 for t in tags if any(k in t for k in ["GOLD", "BOOST", "OVER", "PT"]))
    max_score = max(pt_score, over_score, boost_score, gold_score)

    return {
        "tags": tags,
        "scores": {
            "pt": pt_score,
            "over": over_score,
            "boost": boost_score,
            "gold": gold_score,
            "max": round3(max_score),
        },
        "drop_diff": round3(drop_diff),
        "fav_quote": round3(fav),
        "is_gold_zone": is_gold_zone,
        "primary_signal_count": primary_signal_count
    }


def should_keep_match(signal_pack):
    if signal_pack["primary_signal_count"] >= 1:
        return True

    has_probe = any(t in signal_pack["tags"] for t in ["🐟O", "🐟G"])
    if has_probe and signal_pack["scores"]["max"] >= 3.4:
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

# ==========================================
# MODAL DETTAGLI MATCH
# ==========================================
@st.dialog("🔎 Dettagli partita", width="large")
def show_match_modal(fixture_id: str):
    detail = st.session_state.match_details.get(str(fixture_id))

    if not detail:
        st.warning("Dettagli non disponibili per questa partita.")
        return

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
    a1.metric("AVG FT Home", f"{detail['averages'].get('home_avg_ft', 0):.2f}")
    a2.metric("AVG FT Away", f"{detail['averages'].get('away_avg_ft', 0):.2f}")
    a3.metric("AVG HT Combo", f"{detail['averages'].get('combined_ht_avg', 0):.2f}")

    st.write(
        f"**AVG HT Home/Away:** "
        f"{detail['averages'].get('home_avg_ht', 0):.2f} | "
        f"{detail['averages'].get('away_avg_ht', 0):.2f}"
    )

    st.write(
        f"**Fav quota:** {detail['flags'].get('fav_quote', 0):.2f} | "
        f"**Gold zone:** {'✅' if detail['flags'].get('is_gold_zone') else '❌'} | "
        f"**Home last 2H zero:** {'✅' if detail['flags'].get('home_last_2h_zero') else '❌'} | "
        f"**Away last 2H zero:** {'✅' if detail['flags'].get('away_last_2h_zero') else '❌'} | "
        f"**Drop:** {detail['flags'].get('drop_diff', 0):.2f}"
    )

    scores = detail.get("scores", {})
    if scores:
        st.markdown("---")
        st.subheader("🧠 Score interni V24.1")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("PT", f"{scores.get('pt', 0):.2f}")
        s2.metric("OVER", f"{scores.get('over', 0):.2f}")
        s3.metric("BOOST", f"{scores.get('boost', 0):.2f}")
        s4.metric("GOLD", f"{scores.get('gold', 0):.2f}")

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
                    if combined_ht_avg < 1.03:
                        continue

                    signal_pack = build_signal_package(fid, mk, s_h, s_a, combined_ht_avg)
                    tags = signal_pack["tags"]

                    if not should_keep_match(signal_pack):
                        continue

                    fav = signal_pack["fav_quote"]
                    is_gold_zone = signal_pack["is_gold_zone"]

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
                        "Fixture_ID": f.get("fixture", {}).get("id")
                    }
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
                            "combined_ht_avg": round(combined_ht_avg, 3)
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
            current_db = {str(r["Fixture_ID"]): r for r in st.session_state.scan_results}
            target_date_ids = {str(r["Fixture_ID"]) for r in final_list}

            for existing in list(current_db.keys()):
                existing_row = current_db[existing]
                if existing_row.get("Data") == target_date and existing not in target_date_ids:
                    del current_db[existing]

            for r in final_list:
                current_db[str(r["Fixture_ID"])] = r

            new_scan_results = list(current_db.values())
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

    print("📌 DAY 1: SNAP + SCAN + update data.json/data_day1/details_day1")
    run_full_scan(horizon=1, snap=True, update_main_site=True, show_success=False)

    print("📆 DAY 2: scan statico + update data_day2/details_day2")
    run_full_scan(horizon=2, snap=False, update_main_site=False, show_success=False)

    print("📆 DAY 3: scan statico + update data_day3/details_day3")
    run_full_scan(horizon=3, snap=False, update_main_site=False, show_success=False)

    print("📆 DAY 4: scan statico + update data_day4/details_day4")
    run_full_scan(horizon=4, snap=False, update_main_site=False, show_success=False)

    print("📆 DAY 5: scan statico + update data_day5/details_day5")
    run_full_scan(horizon=5, snap=False, update_main_site=False, show_success=False)

    print("✅ Build multi-day completata.")

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
        def build_1x2_visual(row):
            q1 = str(row.get("Q1_MOVE", "")).strip()
            qx = str(row.get("QX_MOVE", "")).strip()
            q2 = str(row.get("Q2_MOVE", "")).strip()

            base_1x2 = str(row.get("1X2", "")).split("|")
            while len(base_1x2) < 3:
                base_1x2.append("")

            left = q1 if q1 else base_1x2[0]
            mid = qx if qx else base_1x2[1]
            right = q2 if q2 else base_1x2[2]

            return f"""
            <div style="line-height:1.15; white-space:pre-line;">
                <div><b>1</b> {left}</div>
                <div><b>X</b> {mid}</div>
                <div><b>2</b> {right}</div>
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
            "Q1_OPEN", "QX_OPEN", "Q2_OPEN", "O25_OPEN",
            "Q1_CURR", "QX_CURR", "Q2_CURR", "O25_CURR",
            "Q1_MOVE", "QX_MOVE", "Q2_MOVE", "O25_MOVE",
            "INVERSION", "INV_FROM", "INV_TO"
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
                .row-gold { background-color: #FFD700 !important; color: black !important; font-weight: bold; }
                .row-boost { background-color: #006400 !important; color: white !important; font-weight: bold; }
                .row-over { background-color: #90EE90 !important; color: black !important; font-weight: bold; }
                .row-std { background-color: #FFFFFF !important; color: #000000 !important; }
            </style>
        """, unsafe_allow_html=True)

        def get_row_class(info):
            if "GOLD" in info:
                return "row-gold"
            if "BOOST" in info:
                return "row-boost"
            if "OVER" in info:
                return "row-over"
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
