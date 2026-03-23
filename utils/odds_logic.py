# odds_logic.py

from typing import Any, Dict, List, Optional


CONFIG = {
    "main": {
        "neutral_pct": 1.0,
        "strong_pct": 3.0,
        "inversion_swing_pct": 2.5,
    },
    "secondary": {
        "neutral_pct": 1.5,
        "strong_pct": 3.5,
        "inversion_swing_pct": 3.0,
    },
}


def is_valid_odd(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 1.0


def calc_delta_pct(open_odd: Optional[float], current_odd: Optional[float]) -> Optional[float]:
    if not is_valid_odd(open_odd) or not is_valid_odd(current_odd):
        return None
    return ((current_odd - open_odd) / open_odd) * 100.0


def calc_delta_abs(open_odd: Optional[float], current_odd: Optional[float]) -> Optional[float]:
    if not is_valid_odd(open_odd) or not is_valid_odd(current_odd):
        return None
    return current_odd - open_odd


def get_thresholds(market_type: str = "main") -> Dict[str, float]:
    return CONFIG["secondary"] if market_type == "secondary" else CONFIG["main"]


def get_movement_signal(
    open_odd: Optional[float],
    current_odd: Optional[float],
    market_type: str = "main",
) -> Dict[str, Any]:
    th = get_thresholds(market_type)

    if not is_valid_odd(open_odd) or not is_valid_odd(current_odd):
        return {
            "direction": "flat",
            "color": "gray",
            "arrow": "→",
            "delta_pct": None,
            "delta_abs": None,
            "label": "n.d.",
            "strength": "none",
        }

    delta_pct = calc_delta_pct(open_odd, current_odd)
    delta_abs = calc_delta_abs(open_odd, current_odd)
    abs_pct = abs(delta_pct)

    if abs_pct < th["neutral_pct"]:
        return {
            "direction": "flat",
            "color": "gray",
            "arrow": "→",
            "delta_pct": delta_pct,
            "delta_abs": delta_abs,
            "label": "stabile",
            "strength": "none",
        }

    if delta_pct < 0:
        return {
            "direction": "down",
            "color": "green" if abs_pct >= th["strong_pct"] else "yellow",
            "arrow": "↓",
            "delta_pct": delta_pct,
            "delta_abs": delta_abs,
            "label": "in calo",
            "strength": "strong" if abs_pct >= th["strong_pct"] else "soft",
        }

    return {
        "direction": "up",
        "color": "red" if abs_pct >= th["strong_pct"] else "yellow",
        "arrow": "↑",
        "delta_pct": delta_pct,
        "delta_abs": delta_abs,
        "label": "in salita",
        "strength": "strong" if abs_pct >= th["strong_pct"] else "soft",
    }


def normalize_history(history: Any) -> List[Dict[str, Any]]:
    if not isinstance(history, list):
        return []

    out = []
    for p in history:
        odd = p.get("odd") if isinstance(p, dict) else None
        ts = p.get("ts") if isinstance(p, dict) else None
        if is_valid_odd(odd):
            out.append({"ts": ts, "odd": odd})
    return out


def direction_from_pair(a: float, b: float, neutral_pct: float = 0.8) -> str:
    if not is_valid_odd(a) or not is_valid_odd(b):
        return "flat"

    pct = ((b - a) / a) * 100.0
    if abs(pct) < neutral_pct:
        return "flat"
    return "up" if pct > 0 else "down"


def detect_inversion(history: Any, market_type: str = "main") -> Dict[str, Any]:
    th = get_thresholds(market_type)
    points = normalize_history(history)

    if len(points) < 3:
        return {
            "is_inversion": False,
            "reason": "not-enough-history",
            "first_direction": "flat",
            "final_direction": "flat",
            "swing_pct": 0.0,
        }

    open_odd = points[0]["odd"]
    last_odd = points[-1]["odd"]

    first_direction = "flat"
    for i in range(1, len(points)):
        first_direction = direction_from_pair(open_odd, points[i]["odd"], th["neutral_pct"] * 0.8)
        if first_direction != "flat":
            break

    final_direction = direction_from_pair(open_odd, last_odd, th["neutral_pct"])

    odds = [p["odd"] for p in points]
    min_odd = min(odds)
    max_odd = max(odds)

    swing_down_pct = abs(((min_odd - open_odd) / open_odd) * 100.0)
    swing_up_pct = abs(((max_odd - open_odd) / open_odd) * 100.0)
    swing_pct = max(swing_down_pct, swing_up_pct)

    is_opposite = (
        first_direction != "flat"
        and final_direction != "flat"
        and first_direction != final_direction
    )
    is_meaningful = swing_pct >= th["inversion_swing_pct"]

    return {
        "is_inversion": bool(is_opposite and is_meaningful),
        "reason": "direction-reversal" if (is_opposite and is_meaningful) else "no-reversal",
        "first_direction": first_direction,
        "final_direction": final_direction,
        "swing_pct": round(swing_pct, 1),
    }


def read_outcome(raw_node: Any) -> Dict[str, Any]:
    if not isinstance(raw_node, dict):
        return {"open": None, "current": None, "history": []}

    open_odd = raw_node.get("open")
    current_odd = raw_node.get("current")
    history = raw_node.get("history", [])

    return {
        "open": open_odd if is_valid_odd(open_odd) else None,
        "current": current_odd if is_valid_odd(current_odd) else None,
        "history": normalize_history(history),
    }


def normalize_match_data(raw_match: Dict[str, Any]) -> Dict[str, Any]:
    odds = raw_match.get("odds", {})

    one_node = odds.get("1x2", {}).get("1") or odds.get("one") or odds.get("home")
    draw_node = odds.get("1x2", {}).get("X") or odds.get("draw")
    two_node = odds.get("1x2", {}).get("2") or odds.get("two") or odds.get("away")

    o05ht_node = (
        odds.get("o05ht", {}).get("over")
        or odds.get("O0.5HT", {}).get("over")
        or odds.get("O0.5HT")
    )

    o25ft_node = (
        odds.get("o25ft", {}).get("over")
        or odds.get("O2.5FT", {}).get("over")
        or odds.get("O2.5FT")
    )

    return {
        "match_id": raw_match.get("match_id") or raw_match.get("id"),
        "home_team": raw_match.get("home_team") or raw_match.get("home") or "Home",
        "away_team": raw_match.get("away_team") or raw_match.get("away") or "Away",
        "kickoff": raw_match.get("kickoff") or raw_match.get("match_time"),
        "one": read_outcome(one_node),
        "draw": read_outcome(draw_node),
        "two": read_outcome(two_node),
        "o05ht": read_outcome(o05ht_node),
        "o25ft": read_outcome(o25ft_node),
    }
