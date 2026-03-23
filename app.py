# app.py

import json
from pathlib import Path

import streamlit as st

from odds_logic import (
    detect_inversion,
    get_movement_signal,
    normalize_match_data,
)


st.set_page_config(
    page_title="ArabSniper2 - Gold Signals Test",
    layout="wide",
)

st.title("ArabSniper2 – Visualizzazione movimenti quote")
st.caption("Ambiente test Streamlit/GitHub – non produzione HTML Gold")


DATA_PATH = Path("data/data_day1.json")


def load_matches():
    if not DATA_PATH.exists():
        return []

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return data.get("matches", [])

    return []


def signal_html(label, signal, compact=False):
    color_map = {
        "green": "#1faa59",
        "yellow": "#c79a1b",
        "red": "#d64c4c",
        "gray": "#7f8c8d",
    }

    bg_map = {
        "green": "rgba(31,170,89,0.12)",
        "yellow": "rgba(199,154,27,0.12)",
        "red": "rgba(214,76,76,0.12)",
        "gray": "rgba(127,140,141,0.10)",
    }

    border_map = {
        "green": "rgba(31,170,89,0.35)",
        "yellow": "rgba(199,154,27,0.35)",
        "red": "rgba(214,76,76,0.35)",
        "gray": "rgba(127,140,141,0.22)",
    }

    pct = ""
    if isinstance(signal["delta_pct"], (int, float)):
        pct_val = round(signal["delta_pct"], 1)
        pct = f"{'+' if pct_val > 0 else ''}{pct_val}%"

    font_size = "12px" if compact else "13px"
    pad = "5px 8px" if compact else "7px 10px"

    return f"""
    <div style="
        display:inline-flex;
        align-items:center;
        gap:8px;
        padding:{pad};
        border-radius:10px;
        border:1px solid {border_map[signal['color']]};
        background:{bg_map[signal['color']]};
        color:{color_map[signal['color']]};
        font-size:{font_size};
        font-weight:600;
        margin:2px 6px 2px 0;
    ">
        <span>{label}</span>
        <span style="font-size:15px;font-weight:800;">{signal['arrow']}</span>
        <span style="font-size:11px;opacity:0.9;">{pct}</span>
    </div>
    """


def inv_badge_html(inv_labels):
    if not inv_labels:
        return ""

    title = ", ".join(inv_labels)
    return f"""
    <div title="Inversione quota rilevata su: {title}" style="
        display:inline-flex;
        align-items:center;
        justify-content:center;
        padding:6px 10px;
        border-radius:10px;
        border:1px solid rgba(194,109,255,0.42);
        background:rgba(194,109,255,0.15);
        color:#d28cff;
        font-size:12px;
        font-weight:800;
        margin-left:6px;
    ">
        INV
    </div>
    """


matches_raw = load_matches()

if not matches_raw:
    st.warning("Nessun dato disponibile in data/data_day1.json")
    st.stop()

matches = [normalize_match_data(m) for m in matches_raw]

st.sidebar.header("Controlli")
show_secondary_pct = st.sidebar.checkbox("Mostra % mercati secondari", value=True)
only_inversion = st.sidebar.checkbox("Mostra solo match con inversione", value=False)

visible_matches = []

for match in matches:
    inv_one = detect_inversion(match["one"]["history"], "main")
    inv_draw = detect_inversion(match["draw"]["history"], "main")
    inv_two = detect_inversion(match["two"]["history"], "main")

    has_inv = inv_one["is_inversion"] or inv_draw["is_inversion"] or inv_two["is_inversion"]

    if only_inversion and not has_inv:
        continue

    visible_matches.append((match, inv_one, inv_draw, inv_two))

st.subheader(f"Match visibili: {len(visible_matches)}")

for match, inv_one, inv_draw, inv_two in visible_matches:
    sig_one = get_movement_signal(match["one"]["open"], match["one"]["current"], "main")
    sig_draw = get_movement_signal(match["draw"]["open"], match["draw"]["current"], "main")
    sig_two = get_movement_signal(match["two"]["open"], match["two"]["current"], "main")

    sig_o05 = get_movement_signal(match["o05ht"]["open"], match["o05ht"]["current"], "secondary")
    sig_o25 = get_movement_signal(match["o25ft"]["open"], match["o25ft"]["current"], "secondary")

    inv_labels = []
    if inv_one["is_inversion"]:
        inv_labels.append("1")
    if inv_draw["is_inversion"]:
        inv_labels.append("X")
    if inv_two["is_inversion"]:
        inv_labels.append("2")

    with st.container():
        st.markdown(
            f"""
            <div style="
                border:1px solid rgba(212,175,55,0.22);
                border-radius:14px;
                padding:14px;
                margin-bottom:14px;
                background:linear-gradient(180deg,#111 0%, #0c0c0c 100%);
            ">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px;">
                    <div style="color:#f4e7b7;font-weight:700;font-size:16px;">
                        {match['home_team']} <span style="color:#a28a3d;font-size:12px;">vs</span> {match['away_team']}
                    </div>
                    <div style="color:#bda85d;font-size:12px;">
                        {match['kickoff'] or ''}
                    </div>
                </div>

                <div style="color:#cfb86a;font-size:12px;font-weight:700;margin-bottom:6px;">1X2</div>
                <div style="display:flex;flex-wrap:wrap;align-items:center;margin-bottom:12px;">
                    {signal_html("1", sig_one)}
                    {signal_html("X", sig_draw)}
                    {signal_html("2", sig_two)}
                    {inv_badge_html(inv_labels)}
                </div>

                <div style="color:#cfb86a;font-size:12px;font-weight:700;margin-bottom:6px;">Secondarie</div>
                <div style="display:flex;flex-wrap:wrap;align-items:center;">
                    {signal_html("O0.5HT", sig_o05, compact=True) if show_secondary_pct else signal_html("O0.5HT", {**sig_o05, "delta_pct": None}, compact=True)}
                    {signal_html("O2.5FT", sig_o25, compact=True) if show_secondary_pct else signal_html("O2.5FT", {**sig_o25, "delta_pct": None}, compact=True)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
