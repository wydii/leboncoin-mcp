import asyncio
import json
import hashlib
import math
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, parse_qsl, quote, unquote_plus, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import lbc
from fastmcp import FastMCP

mcp = FastMCP("leboncoin", instructions=(
    "MCP server for searching Leboncoin (French classifieds). "
    "Use search_ads to find listings, get_ad for details on a specific ad, "
    "and get_user for seller info. "
    "Locations can be a city (lat/lng/radius), a region name, or a department name. "
    "Categories map to Leboncoin sections (VEHICULES, IMMOBILIER, ELECTRONIQUE, etc.)."
))

_client = lbc.Client()

BEN_DEFAULT_CAR_URL = (
    "https://www.leboncoin.fr/recherche?"
    "category=2&locations=Aix-en-Provence_13100__43.52638_5.44614_5000_100000"
    "&mileage=min-130000&price=3000-6000&regdate=2015-max&doors=5"
    "&fuel=1%2C6%2C8%2C4%2C9%2C3%2C7%2C5&owner_type=all&sort=time&order=desc"
)

STATE_DIR = Path.home() / ".codex" / "state" / "leboncoin-mcp"
ACCOUNT_LOGIN_URL = "https://www.leboncoin.fr/"
ACCOUNT_FAVORITES_URL = "https://www.leboncoin.fr/favorites"
ACCOUNT_MESSAGES_URL = "https://www.leboncoin.fr/messages"
ACCOUNT_BROWSER_PROFILE_DIR = Path.home() / ".codex" / "browser" / "leboncoin-agent"
ACCOUNT_BROWSER_DEBUG_HOST = "127.0.0.1"
ACCOUNT_BROWSER_DEBUG_PORT = 9224
ACCOUNT_BROWSER_DEBUG_URL = f"http://{ACCOUNT_BROWSER_DEBUG_HOST}:{ACCOUNT_BROWSER_DEBUG_PORT}"
COMET_DEBUG_URL = "http://127.0.0.1:9223"
ACCOUNT_SNAPSHOT_TTL_DAYS = 30
ACCOUNT_BROWSER_APPS = (
    "Brave Browser",
    "Google Chrome",
    "Chromium",
    "Microsoft Edge",
)

SENSITIVE_KEY_RE = re.compile(
    r"(cookie|token|secret|password|passwd|authorization|bearer|csrf|session)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(authorization\s*:\s*[^\r\n]+|cookie\s*:\s*[^\r\n]+|bearer\s+[a-z0-9._~+/=-]+)"
)
GO_PHRASES = {
    "go",
    "ok envoie",
    "valide pour envoi",
    "oui envoie",
    "c'est bon envoie",
    "vas-y envoie",
}
BLOCKING_MESSAGE_STATUSES = {
    "awaiting_approval",
    "approved_dry_run",
    "manual_required",
    "sent",
    "manual_sent",
}
RAW_SNAPSHOT_FIELDS = {
    "raw",
    "html",
    "page_html",
    "page_text",
    "body_text",
    "full_text",
}

CATEGORY_MAP = {item.name: item for item in lbc.Category}
SORT_MAP = {item.name: item for item in lbc.Sort}
AD_TYPE_MAP = {item.name: item for item in lbc.AdType}
OWNER_TYPE_MAP = {item.name: item for item in lbc.OwnerType}
REGION_MAP = {item.name: item for item in lbc.Region}
DEPARTMENT_MAP = {item.name: item for item in lbc.Department}

BAD_STYLE_PATTERNS = {
    "panda": "Fiat Panda : rejet style explicite de Ben",
    "twingo": "Renault Twingo : rejet style explicite de Ben",
    "zoe": "Renault Zoe : rejet usage/style explicite de Ben",
    "zoé": "Renault Zoe : rejet usage/style explicite de Ben",
    "micra": "Nissan Micra : arrière/look rejeté par Ben",
    "c3 picasso": "Citroën C3 Picasso : format monospace/familial rejeté par Ben",
    "citroen c3 picasso": "Citroën C3 Picasso : format monospace/familial rejeté par Ben",
    "citroën c3 picasso": "Citroën C3 Picasso : format monospace/familial rejeté par Ben",
    "modus": "Renault Modus : look trop voiture de maman",
    "grand modus": "Renault Grand Modus : look trop voiture de maman",
    "meriva": "Opel Meriva : mini-monospace fortement pénalisé",
    "500l": "Fiat 500L : format familial haut et peu désirable pour Ben",
    "b-max": "Ford B-Max : format familial/monospace pénalisé",
    "b max": "Ford B-Max : format familial/monospace pénalisé",
    "nissan note": "Nissan Note ancien : silhouette trop familiale",
    "kia venga": "Kia Venga : mini-monospace pénalisé",
    "hyundai ix20": "Hyundai ix20 : mini-monospace pénalisé",
    "lodgy": "Dacia Lodgy : format familial rejeté",
    "dokker": "Dacia Dokker : utilitaire/familial rejeté",
    "c4 picasso": "Citroën C4 Picasso : monospace rejeté",
    "touran": "VW Touran : monospace/familial",
    "scenic": "Renault Scénic : monospace/familial",
    "scénic": "Renault Scénic : monospace/familial",
}

AIX_LAT = 43.52638
AIX_LNG = 5.44614

GOOD_STYLE_PATTERNS = {
    "polo": "format compact moderne apprécié",
    "golf": "format compact qualitatif",
    "ibiza": "format compact jeune",
    "fabia": "format compact rationnel mais acceptable",
    "yaris": "format citadine fiable et moderne",
    "swift": "format compact léger et sympa",
    "mazda 2": "format compact moderne",
    "fiesta": "format compact jeune",
    "clio 4": "format compact moderne",
    "clio iv": "format compact moderne",
    "i20": "format citadine moderne",
    "rio": "format citadine moderne",
    "corsa": "format citadine compacte",
    "peugeot 208": "format citadine moderne, moteur à vérifier",
}

EQUIPMENT_PATTERNS = {
    "carplay": "CarPlay",
    "android auto": "Android Auto",
    "bluetooth": "Bluetooth",
    "gps": "GPS",
    "navigation": "navigation",
    "ecran": "écran",
    "écran": "écran",
    "tactile": "écran tactile",
    "camera": "caméra",
    "caméra": "caméra",
    "radar de recul": "radar de recul",
    "regulateur": "régulateur",
    "régulateur": "régulateur",
    "clim auto": "clim auto",
    "volant multifonction": "volant multifonction",
}

# Wet belt / known bad engines : capped at score 30 to push them out of top
BANNED_ENGINE_PATTERNS = [
    (re.compile(r"puretech\s*1[.,]?2", re.IGNORECASE), "PureTech 1.2 wet belt"),
    (re.compile(r"puretech\s+(82|110|130|155)\b", re.IGNORECASE), "PureTech 82/110/130/155 wet belt"),
    (re.compile(r"\bvti\s+1[.,]?[26]?\b", re.IGNORECASE), "PSA VTi 1.2/1.6 (timing chain)"),
    (re.compile(r"ecoboost\s+1[.,]?0", re.IGNORECASE), "Ford EcoBoost 1.0 wet belt"),
    (re.compile(r"ecoboost\s+1[.,]?6", re.IGNORECASE), "Ford EcoBoost 1.6 (head gasket)"),
    (re.compile(r"\btce\s+(0[.,]?9|1[.,]?2|90|115)\b", re.IGNORECASE), "Renault/Nissan/Dacia TCe (oil burn)"),
    (re.compile(r"tsi\s+blue\s*motion", re.IGNORECASE), "VW TSI BlueMotion (timing chain)"),
    (re.compile(r"\b(cbza|cbzb|cava|cavd)\b", re.IGNORECASE), "VW TSI code CBZA/CBZB/CAVA/CAVD"),
    (re.compile(r"\bn47\b", re.IGNORECASE), "BMW N47 diesel (timing chain)"),
    (re.compile(r"dsg7?\s*sec", re.IGNORECASE), "VW DSG7 sec (DQ200) reliability"),
    (re.compile(r"\bdq200\b", re.IGNORECASE), "VW DQ200 DSG7 sec (mechatronic)"),
    (re.compile(r"m271\s+kompressor", re.IGNORECASE), "Mercedes M271 Kompressor (timing chain)"),
]


def _is_banned_engine(ad: dict) -> tuple[bool, Optional[str]]:
    """Check if ad title/body matches a banned engine pattern. Returns (banned, reason)."""
    title = str(ad.get("title") or "")
    body = str(ad.get("body") or "")
    haystack = f"{title}\n{body}"
    for pattern, reason in BANNED_ENGINE_PATTERNS:
        if pattern.search(haystack):
            return True, reason
    return False, None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_iso(days: int) -> str:
    return datetime.fromtimestamp(time.time() + days * 24 * 60 * 60, timezone.utc).isoformat()


def _state_path(name: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STATE_DIR.chmod(0o700)
    except OSError:
        pass
    return STATE_DIR / name


def _browser_profile_path() -> Path:
    ACCOUNT_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ACCOUNT_BROWSER_PROFILE_DIR.chmod(0o700)
    except OSError:
        pass
    return ACCOUNT_BROWSER_PROFILE_DIR


def _normalize_browser_mode(browser_mode: str) -> tuple[Optional[str], Optional[str]]:
    mode = str(browser_mode or "comet").lower().strip()
    if mode in {"comet", "external"}:
        return mode, None
    return None, "invalid_browser_mode"


def _require_leboncoin_url(
    raw_url: str,
    allowed_path_prefixes: tuple[str, ...] = ("/",),
    strip_query: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    safe_url = _redact_text(raw_url, 1200)
    parsed = urlparse(safe_url)
    if parsed.scheme != "https" or parsed.netloc != "www.leboncoin.fr":
        return None, "invalid_leboncoin_origin"
    if not any(parsed.path.startswith(prefix) for prefix in allowed_path_prefixes):
        return None, "invalid_leboncoin_path"
    if strip_query:
        parsed = parsed._replace(query="", fragment="")
    return urlunparse(parsed), None


def _require_leboncoin_message_url(raw_url: str) -> tuple[Optional[str], Optional[str]]:
    return _require_leboncoin_url(raw_url, ("/messages/id/",))


def _is_leboncoin_tab(tab: dict) -> bool:
    parsed = urlparse(str(tab.get("url") or ""))
    return parsed.scheme == "https" and parsed.netloc == "www.leboncoin.fr"


def _http_json(url: str, timeout: float = 3.0, method: str = "GET") -> Any:
    request = Request(url, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _debug_browser_tabs(debug_url: str) -> list[dict]:
    try:
        tabs = _http_json(f"{debug_url}/json/list", timeout=2.0)
    except Exception:
        return []
    clean_tabs = []
    for tab in tabs if isinstance(tabs, list) else []:
        if not isinstance(tab, dict):
            continue
        clean_tabs.append({
            "id": tab.get("id"),
            "type": tab.get("type"),
            "title": _redact_text(tab.get("title") or "", 300),
            "url": _redact_text(tab.get("url") or "", 1200),
            "webSocketDebuggerUrl": tab.get("webSocketDebuggerUrl"),
        })
    return clean_tabs


def _debug_browser_reachable(debug_url: str) -> bool:
    try:
        version = _http_json(f"{debug_url}/json/version", timeout=1.5)
        return isinstance(version, dict)
    except Exception:
        return False


def _account_browser_tabs() -> list[dict]:
    return _debug_browser_tabs(ACCOUNT_BROWSER_DEBUG_URL)


def _account_browser_reachable() -> bool:
    return _debug_browser_reachable(ACCOUNT_BROWSER_DEBUG_URL)


def _browser_mode_debug_url(browser_mode: str) -> str:
    mode, _ = _normalize_browser_mode(browser_mode)
    return COMET_DEBUG_URL if mode == "comet" else ACCOUNT_BROWSER_DEBUG_URL


def _account_browser_pids() -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            timeout=5,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    pids = []
    profile_marker = str(_browser_profile_path())
    for line in result.stdout.splitlines():
        if profile_marker not in line:
            continue
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        try:
            pids.append(int(parts[0]))
        except ValueError:
            continue
    return sorted(set(pids))


async def _cdp_commands(websocket_url: str, commands: list[tuple[str, dict]], delay_after_nav: float = 2.5) -> Any:
    import websockets

    async with websockets.connect(websocket_url, max_size=8 * 1024 * 1024) as ws:
        last_response = None
        for idx, (method, params) in enumerate(commands, start=1):
            await ws.send(json.dumps({"id": idx, "method": method, "params": params}))
            while True:
                response = json.loads(await ws.recv())
                if response.get("id") == idx:
                    last_response = response
                    break
            if method == "Page.navigate":
                await asyncio.sleep(delay_after_nav)
        return last_response


def _cdp_eval(
    url: str,
    expression: str,
    wait_seconds: float = 2.5,
    debug_url: str = ACCOUNT_BROWSER_DEBUG_URL,
) -> dict:
    safe_url, url_error = _require_leboncoin_url(url, ("/",))
    if url_error:
        return {"ok": False, "error": url_error}
    tabs = [tab for tab in _debug_browser_tabs(debug_url) if tab.get("type") == "page"]
    if not tabs:
        return {"ok": False, "error": "no_debuggable_tab"}
    leboncoin_tabs = [tab for tab in tabs if _is_leboncoin_tab(tab)]
    if not leboncoin_tabs:
        return {"ok": False, "error": "no_leboncoin_debuggable_tab"}
    websocket_url = leboncoin_tabs[0].get("webSocketDebuggerUrl")
    if not websocket_url:
        return {"ok": False, "error": "missing_websocket_url"}
    commands = [
        ("Page.enable", {}),
        ("Runtime.enable", {}),
        ("Page.navigate", {"url": safe_url}),
        ("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        }),
    ]
    try:
        response = asyncio.run(_cdp_commands(websocket_url, commands, wait_seconds))
    except Exception as exc:
        return {"ok": False, "error": _redact_text(f"{type(exc).__name__}: {exc}", 500)}
    if response and "error" in response:
        return {"ok": False, "error": _redact_text(json.dumps(response["error"], ensure_ascii=False), 500)}
    result = ((response or {}).get("result") or {}).get("result") or {}
    if "exceptionDetails" in (response or {}).get("result", {}):
        return {
            "ok": False,
            "error": _redact_text(json.dumps(response["result"]["exceptionDetails"], ensure_ascii=False), 500),
        }
    return {"ok": True, "value": result.get("value")}


def _read_state(name: str, default):
    path = _state_path(name)
    if not path.exists():
        return default
    if path.is_symlink():
        return default
    try:
        path.chmod(0o600)
    except OSError:
        pass
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        backup_path = path.with_name(f"{path.name}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.bak")
        try:
            backup_path.write_text(path.read_text(errors="replace"), encoding="utf-8")
            backup_path.chmod(0o600)
        except OSError:
            pass
        return default


def _write_state(name: str, value) -> None:
    path = _state_path(name)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    try:
        tmp_path.chmod(0o600)
    except OSError:
        pass
    tmp_path.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _redact_url_secrets(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value

    netloc = parsed.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = f"[REDACTED]@{host}"

    query = urlencode([
        (key, "[REDACTED]" if SENSITIVE_KEY_RE.search(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ])
    fragment = "[REDACTED]" if SENSITIVE_KEY_RE.search(parsed.fragment) else parsed.fragment
    return urlunparse(parsed._replace(netloc=netloc, query=query, fragment=fragment))


def _redact_text(value: Any, max_len: int = 2500) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text).strip()
    text = SENSITIVE_VALUE_RE.sub("[REDACTED]", text)
    text = re.sub(r"https?://[^\s<>'\"]+", lambda match: _redact_url_secrets(match.group(0)), text)
    return text[:max_len]


def _sanitize_for_storage(value: Any, max_string_len: int = 2500):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            key_text = str(key)
            if SENSITIVE_KEY_RE.search(key_text):
                clean[key_text] = "[REDACTED]"
            else:
                clean[key_text] = _sanitize_for_storage(item, max_string_len)
        return clean
    if isinstance(value, list):
        return [_sanitize_for_storage(item, max_string_len) for item in value[:100]]
    if isinstance(value, str):
        return _redact_text(value, max_string_len)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(value, max_string_len)


def _drop_raw_snapshot_fields(item: dict) -> dict:
    return {
        key: value
        for key, value in item.items()
        if str(key).lower() not in RAW_SNAPSHOT_FIELDS
    }


def _is_snapshot_active(item: dict) -> bool:
    expires_at = item.get("expires_at")
    if not expires_at:
        return True
    try:
        expires = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)


def _message_hash(message: str) -> str:
    normalized = re.sub(r"\s+", " ", _redact_text(message, 2000)).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _normalize_message_item(message: Any) -> dict:
    if isinstance(message, dict):
        clean = _sanitize_for_storage(message, 1200)
        return {
            "sender": clean.get("sender"),
            "timestamp": clean.get("timestamp"),
            "text": _redact_text(clean.get("text") or clean.get("body") or clean.get("message"), 1200),
        }
    return {
        "sender": None,
        "timestamp": None,
        "text": _redact_text(message, 1200),
    }


def _conversation_messages(raw: dict, limit: Optional[int] = None) -> list[dict]:
    messages = raw.get("messages") or []
    if not isinstance(messages, list):
        return []
    normalized = [_normalize_message_item(message) for message in messages]
    if limit is None:
        return normalized
    limit = max(0, min(int(limit), 50))
    if limit == 0:
        return []
    return normalized[-limit:]


def _is_explicit_go(text: Optional[str]) -> bool:
    normalized = _normalize_text(text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip(" .!?:;")
    return normalized in {_normalize_text(phrase) for phrase in GO_PHRASES}


def _contains_explicit_go(text: Optional[str]) -> bool:
    normalized = _normalize_text(text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip(" .!?:;")
    return any(_normalize_text(phrase) in normalized for phrase in GO_PHRASES)


def _approval_confirms_draft(approval_text: str, draft: dict) -> bool:
    text = str(approval_text or "")
    return bool(
        draft.get("draft_id")
        and draft.get("message_hash")
        and str(draft["draft_id"]) in text
        and str(draft["message_hash"]) in text
    )


def _has_blocking_message_entry(journal: list[dict], dedupe_key: str, draft_id: Optional[str] = None) -> bool:
    for entry in journal:
        if entry.get("dedupe_key") != dedupe_key:
            continue
        if draft_id and entry.get("draft_id") == draft_id:
            continue
        if entry.get("status") in BLOCKING_MESSAGE_STATUSES:
            return True
    return False


def _first(query: dict[str, list[str]], key: str) -> Optional[str]:
    values = query.get(key)
    return values[0] if values else None


def _safe_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).replace("\xa0", " ")
    match = re.search(r"\d[\d\s.]*", text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


def _parse_range_token(value: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not value:
        return None, None
    parts = value.split("-", 1)
    if len(parts) == 1:
        number = _safe_int(parts[0])
        return number, number
    left, right = parts
    minimum = None if left in ("", "min") else _safe_int(left)
    maximum = None if right in ("", "max") else _safe_int(right)
    return minimum, maximum


def _parse_location_token(value: Optional[str]) -> dict:
    if not value:
        return {}

    first_location = unquote_plus(value).split(",", 1)[0]
    label_part, _, area_part = first_location.partition("__")
    label_bits = label_part.split("_")
    city = label_bits[0].replace("-", " ") if label_bits else None
    zipcode = label_bits[1] if len(label_bits) > 1 and label_bits[1].isdigit() else None

    area_bits = [bit for bit in area_part.split("_") if bit]
    latitude = longitude = None
    radius = None
    if len(area_bits) >= 2:
        try:
            latitude = float(area_bits[0])
            longitude = float(area_bits[1])
        except ValueError:
            latitude = longitude = None
    radii = [_safe_int(bit) for bit in area_bits[2:]]
    radii = [r for r in radii if r is not None]
    if radii:
        radius = max(radii)

    return {
        "raw": first_location,
        "city": city,
        "zipcode": zipcode,
        "latitude": latitude,
        "longitude": longitude,
        "radius": radius or 30_000,
    }


def _normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value).lower()
    text = text.replace("é", "e").replace("è", "e").replace("ê", "e")
    text = text.replace("à", "a").replace("ç", "c").replace("ï", "i")
    return text


def _contains_pattern(text: str, pattern: str) -> bool:
    normalized = _normalize_text(pattern).strip()
    if not normalized:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None


def _distance_km(lat1: Optional[float], lng1: Optional[float], lat2: Optional[float], lng2: Optional[float]) -> Optional[float]:
    if None in (lat1, lng1, lat2, lng2):
        return None
    try:
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        d_phi = math.radians(float(lat2) - float(lat1))
        d_lambda = math.radians(float(lng2) - float(lng1))
    except (TypeError, ValueError):
        return None
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return round(6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def _ad_distance_from_profile(ad: dict, profile: dict) -> Optional[float]:
    ad_location = ad.get("location") or {}
    profile_location = profile.get("location") or {}
    return _distance_km(
        profile_location.get("latitude") or AIX_LAT,
        profile_location.get("longitude") or AIX_LNG,
        ad_location.get("lat"),
        ad_location.get("lng"),
    )


def _combined_ad_text(ad: dict) -> str:
    ignored_attr_fragments = [
        "url", "profile_picture", "object_id", "payment", "eligible", "warranty",
        "rating", "licence_plate", "history_report_status", "car_price_",
        "rotation", "is_import",
    ]
    attr_parts = []
    for key, value in (ad.get("attributes") or {}).items():
        key_text = _normalize_text(key)
        if any(fragment in key_text for fragment in ignored_attr_fragments):
            continue
        value_text = str(value or "")
        if value_text.startswith(("http://", "https://")):
            continue
        attr_parts.append(f"{key} {value_text}")
    attrs = " ".join(attr_parts)
    return _normalize_text(" ".join([
        str(ad.get("title") or ""),
        str(ad.get("body") or ""),
        attrs,
    ]))


def _style_score(text: str) -> tuple[int, list[str], list[str], list[str]]:
    score = 58
    plus = []
    minus = []
    hard_flags = []

    for pattern, reason in BAD_STYLE_PATTERNS.items():
        if _contains_pattern(text, pattern):
            score -= 45
            minus.append(reason)
            hard_flags.append(reason)
            break

    matched_good = []
    for pattern, reason in GOOD_STYLE_PATTERNS.items():
        if _contains_pattern(text, pattern):
            matched_good.append(reason)
    if matched_good:
        score += min(25, 12 + len(matched_good) * 3)
        plus.extend(matched_good[:3])

    family_words = [
        "monospace", "familiale", "break", "ludospace", "utilitaire",
        "grand coffre", "7 places", "sept places",
    ]
    found_family = [word for word in family_words if _contains_pattern(text, word)]
    if found_family:
        score -= 22
        minus.append("format familial/monospace détecté")

    modern_words = [
        "phase 2", "restylee", "restylée", "pack style", "business",
        "allure", "intens", "connect", "beats", "r-line", "gt line",
    ]
    if any(_contains_pattern(text, word) for word in modern_words):
        score += 8
        plus.append("finition ou présentation moderne détectée")

    return max(0, min(100, score)), plus[:4], minus[:4], hard_flags[:3]


def _equipment_score(text: str, image_count: int) -> tuple[int, list[str], list[str]]:
    found = []
    for pattern, label in EQUIPMENT_PATTERNS.items():
        if _contains_pattern(text, pattern) and label not in found:
            found.append(label)

    score = 35 + min(55, len(found) * 12)
    plus = [f"{label} mentionné" for label in found[:5]]
    minus = []

    if image_count >= 5:
        score += 8
        plus.append("photos nombreuses")
    elif image_count == 0:
        score -= 12
        minus.append("pas de photos")

    if not found:
        minus.append("équipement moderne non visible dans le texte")

    return max(0, min(100, score)), plus[:6], minus[:3]


def _attr(ad: dict, *names: str):
    attrs = ad.get("attributes") or {}
    wanted = {_normalize_text(name) for name in names}
    for key, value in attrs.items():
        if _normalize_text(key) in wanted:
            return value
    for key, value in attrs.items():
        key_norm = _normalize_text(key)
        if any(name in key_norm for name in wanted):
            return value
    return None


def _price_value(ad: dict) -> Optional[int]:
    price = ad.get("price")
    if isinstance(price, list) and price:
        return _safe_int(price[0])
    if isinstance(price, dict):
        return _safe_int(price.get("value") or price.get("amount"))
    return _safe_int(price)


def _year_value(ad: dict) -> Optional[int]:
    value = _attr(ad, "Année modèle", "Année", "Mise en circulation")
    text = str(value or "") + " " + str(ad.get("title") or "")
    years = [int(x) for x in re.findall(r"\b(20\d{2}|19\d{2})\b", text)]
    years = [y for y in years if 1990 <= y <= datetime.now().year + 1]
    return max(years) if years else None


def _mileage_value(ad: dict) -> Optional[int]:
    return _safe_int(_attr(ad, "Kilométrage", "kilometrage", "mileage"))


def _doors_value(ad: dict) -> Optional[int]:
    return _safe_int(_attr(ad, "Nombre de portes", "portes", "doors"))


def _fuel_value(ad: dict) -> str:
    return str(_attr(ad, "Énergie", "Energie", "fuel") or "")


def _power_value(ad: dict) -> Optional[int]:
    din = _safe_int(_attr(ad, "Puissance DIN", "Puissance DIN réelle"))
    if din is not None:
        return din
    text = str(ad.get("title") or "") + " " + str(ad.get("body") or "")
    match = re.search(r"\b([7-9]\d|1\d{2})\s*(?:ch|cv)\b", _normalize_text(text))
    return int(match.group(1)) if match else None


def _missing_car_fields(ad: dict) -> list[str]:
    missing = []
    if _price_value(ad) is None:
        missing.append("prix")
    if _year_value(ad) is None:
        missing.append("année")
    if _mileage_value(ad) is None:
        missing.append("kilométrage")
    if _doors_value(ad) is None:
        missing.append("portes")
    if not _fuel_value(ad):
        missing.append("énergie")
    return missing


def _profile_from_url(url: str) -> dict:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    price_min, price_max = _parse_range_token(_first(query, "price"))
    mileage_min, mileage_max = _parse_range_token(_first(query, "mileage"))
    regdate_min, regdate_max = _parse_range_token(_first(query, "regdate"))
    doors = _safe_int(_first(query, "doors"))
    fuel = (_first(query, "fuel") or "").split(",") if _first(query, "fuel") else []

    sort_raw = _first(query, "sort") or "time"
    order_raw = _first(query, "order") or "desc"
    sort = "NEWEST" if sort_raw == "time" and order_raw == "desc" else "RELEVANCE"

    return {
        "profile_id": "ben-voiture",
        "name": "Ben - Voiture Aix/PACA",
        "source_url": url,
        "category": _first(query, "category") or "2",
        "location": _parse_location_token(_first(query, "locations")),
        "price_min": price_min,
        "price_max": price_max,
        "mileage_min": mileage_min,
        "mileage_max": mileage_max,
        "regdate_min": regdate_min,
        "regdate_max": regdate_max,
        "doors": doors,
        "fuel_codes": [code for code in fuel if code],
        "owner_type": _first(query, "owner_type") or "all",
        "sort": sort,
        "raw_sort": sort_raw,
        "raw_order": order_raw,
        "karim_preferences": {
            "budget_target": 5500,
            "budget_strict_max": 6000,
            "strict_doors": 5,
            "year_ideal_min": 2015,
            "year_exception_min": 2014,
            "mileage_flexible_max": 150000,
            "power_preferred_min": 80,
            "fuel_preferred": ["essence", "hybride"],
            "brand_note": "Volkswagen apprécié, mais aucun modèle forcé.",
            "desirability_rule": "Compacte moderne, écran/Bluetooth bonus, pas monospace/familiale/voiture maman.",
            "distance_standard_km": 100,
            "distance_good_deal_max_km": 150,
            "distance_exception_rule": "Au-delà de 150 km seulement pour une pépite exceptionnelle, quelle que soit la marque.",
        },
    }


def _post_filter_ad(ad: dict, profile: dict) -> tuple[bool, list[str]]:
    reasons = []
    price = _price_value(ad)
    year = _year_value(ad)
    mileage = _mileage_value(ad)
    doors = _doors_value(ad)

    if profile.get("price_min") is not None and price is not None and price < profile["price_min"]:
        reasons.append("prix_sous_filtre")
    if profile.get("price_max") is not None and price is not None and price > profile["price_max"]:
        reasons.append("prix_trop_haut")
    if profile.get("regdate_min") is not None and year is not None and year < profile["regdate_min"]:
        reasons.append("annee_trop_ancienne")
    if profile.get("regdate_max") is not None and year is not None and year > profile["regdate_max"]:
        reasons.append("annee_hors_filtre")
    if profile.get("mileage_min") is not None and mileage is not None and mileage < profile["mileage_min"]:
        reasons.append("km_sous_filtre")
    if profile.get("mileage_max") is not None and mileage is not None and mileage > profile["mileage_max"]:
        reasons.append("km_trop_haut")
    if profile.get("doors") is not None and doors is not None and doors != profile["doors"]:
        reasons.append("pas_5_portes")

    return not reasons, reasons


def _score_car(ad: dict, profile: Optional[dict] = None) -> dict:
    profile = profile or _profile_from_url(BEN_DEFAULT_CAR_URL)
    prefs = profile.get("karim_preferences", {})
    score = 35
    plus = []
    minus = []
    hard_flags = []

    price = _price_value(ad)
    year = _year_value(ad)
    mileage = _mileage_value(ad)
    doors = _doors_value(ad)
    fuel = _fuel_value(ad)
    fuel_norm = _normalize_text(fuel)
    power = _power_value(ad)
    text = _combined_ad_text(ad)
    attrs = ad.get("attributes") or {}
    brand_norm = _normalize_text(attrs.get("Marque") or attrs.get("Marque constructeur") or "")
    model_norm = _normalize_text(attrs.get("Modèle") or attrs.get("Modele") or "")
    title_norm = _normalize_text(ad.get("title") or "")
    identity_text = " ".join([brand_norm, model_norm, title_norm])
    image_count = len(ad.get("images") or [])
    distance_km = _ad_distance_from_profile(ad, profile)

    if price is None:
        score -= 10
        minus.append("prix absent")
    elif price <= prefs.get("budget_target", 5500):
        score += 12
        plus.append("prix dans la cible de négociation")
    elif price <= prefs.get("budget_strict_max", 6000):
        score += 5
        plus.append("prix acceptable si le dossier est solide")
    else:
        score -= 35
        hard_flags.append("au-dessus du budget strict")

    if year is None:
        score -= 6
        minus.append("année absente")
    elif year >= prefs.get("year_ideal_min", 2015):
        score += 10
        plus.append("année récente pour le budget")
    elif year >= prefs.get("year_exception_min", 2014):
        score += 2
        minus.append("2014 acceptable seulement si dossier propre")
    else:
        score -= 30
        hard_flags.append("année trop ancienne")

    if mileage is None:
        score -= 8
        minus.append("kilométrage absent")
    elif year and year < 2023 and mileage < 1000:
        score -= 30
        hard_flags.append("kilométrage anormalement bas ou mal parsé : annonce à vérifier")
    elif mileage <= 100_000:
        score += 12
        plus.append("kilométrage excellent pour la cible")
    elif mileage <= 130_000:
        score += 8
        plus.append("kilométrage très bon pour la cible")
    elif mileage <= prefs.get("mileage_flexible_max", 150000):
        score += 4
        plus.append("kilométrage encore jouable selon entretien")
    elif mileage <= 180_000:
        score -= 10
        minus.append("kilométrage haut")
    else:
        score -= 28
        hard_flags.append("kilométrage très élevé")

    if doors is None:
        score -= 5
        minus.append("nombre de portes absent")
    elif doors == prefs.get("strict_doors", 5):
        score += 10
        plus.append("5 portes obligatoire respecté")
    else:
        score -= 40
        hard_flags.append("pas 5 portes")

    if "diesel" in fuel_norm:
        score -= 18
        minus.append("diesel moins aligné avec la recherche")
    elif "electrique" in fuel_norm or "électrique" in fuel_norm:
        score -= 28
        minus.append("électrique non prioritaire pour Ben")
    elif "hybride" in fuel_norm or "essence" in fuel_norm:
        score += 8
        plus.append("énergie cohérente avec la recherche")
    elif fuel_norm:
        score -= 3
        minus.append(f"énergie à vérifier: {fuel}")
    else:
        score -= 4
        minus.append("énergie absente")

    if power is not None:
        if power >= prefs.get("power_preferred_min", 80):
            score += 5
            plus.append("moteur correct")
        elif power >= 70:
            score += 1
            minus.append("puissance modeste mais possible")
        else:
            score -= 8
            minus.append("moteur probablement trop juste")
    else:
        score -= 3
        minus.append("puissance moteur absente")

    red_flags = [
        "voyant moteur", "contre visite", "contre-visite", "joint de culasse",
        "boite a changer", "boîte à changer", "embrayage a prevoir",
        "embrayage à prévoir", "distribution a prevoir", "distribution à prévoir",
        "pas de controle technique", "pas de contrôle technique", "accidente",
        "accidenté", "moteur hs", "probleme moteur", "problème moteur",
        "2 places", "deux places", "prix ht", "hors taxe", "societe", "société",
        "commerciale", "utilitaire", "lld", "loa", "leasing", "loyer",
        "reprise lld", "reprise loa", "apport", "mensualite", "mensualité",
    ]
    found_red_flags = [flag for flag in red_flags if _normalize_text(flag) in text]
    if found_red_flags:
        score -= 20 + min(20, len(found_red_flags) * 5)
        hard_flags.extend(found_red_flags[:3])

    engine_watch = {
        "puretech": "moteur PureTech à vérifier : courroie humide, historique et factures indispensables",
        "pure tech": "moteur PureTech à vérifier : courroie humide, historique et factures indispensables",
        "thp": "moteur THP à vérifier : distribution, turbo et entretien à contrôler",
        "tce": "moteur TCe à vérifier selon version : conso huile/chaîne/entretien",
        "ecoboost": "moteur EcoBoost à vérifier : distribution/courroie et historique",
    }
    engine_penalty_applied = False
    engine_risk_marker = None
    for needle, warning in engine_watch.items():
        if needle in text:
            score -= 12
            minus.append(warning)
            engine_penalty_applied = True
            engine_risk_marker = needle
            break

    psa_brand = (
        brand_norm in {"peugeot", "citroen", "citroën", "ds"}
        or _contains_pattern(title_norm, "peugeot")
        or _contains_pattern(title_norm, "citroen")
        or _contains_pattern(title_norm, "citroën")
        or _contains_pattern(title_norm, "ds")
    )
    psa_small_engine = any(marker in text for marker in ["1.2", "1,2", "1 2", "82ch", "82 ch", "82 cv", "vti"])
    psa_urban_unknown_engine = psa_brand and any(_contains_pattern(identity_text, model) for model in [
        "208", "c3", "c 3",
    ]) and not any(_contains_pattern(text, marker) for marker in [
        "hdi", "bluehdi", "diesel", "electrique", "électrique", "hybride",
        "1.6", "1,6",
    ])
    has_engine_proof = any(marker in text for marker in [
        "courroie changee", "courroie changée", "distri ok", "distribution ok",
        "distribution faite", "factures", "garantie 12 mois",
    ])
    if not engine_penalty_applied and psa_brand and psa_small_engine:
        score -= 8 if has_engine_proof else 18
        minus.append(
            "moteur PSA 1.2/VTi à contrôler : courroie/distribution et factures indispensables"
        )
    elif not engine_penalty_applied and psa_urban_unknown_engine:
        score -= 8
        minus.append("moteur PSA exact à confirmer : risque PureTech/VTi même si l'annonce est séduisante")

    style_score, style_plus, style_minus, style_hard_flags = _style_score(text)
    equipment_score, equipment_plus, equipment_minus = _equipment_score(text, image_count)

    if style_hard_flags:
        score -= 28
        hard_flags.extend(style_hard_flags)
    elif style_score < 45:
        score -= 18
        minus.extend(style_minus)
    elif style_score < 60:
        score -= 8
        minus.extend(style_minus)
    elif style_score >= 75:
        score += 8
        plus.extend(style_plus)

    if equipment_score >= 75:
        score += 7
        plus.extend(equipment_plus)
    elif equipment_score >= 55:
        score += 3
        plus.extend(equipment_plus[:3])
    elif equipment_score < 40:
        score -= 6
        minus.extend(equipment_minus)

    good_flags = ["factures", "entretien", "ct ok", "controle technique ok", "contrôle technique ok"]
    found_good_flags = [flag for flag in good_flags if _normalize_text(flag) in text]
    if found_good_flags:
        score += min(7, len(found_good_flags) * 2)
        plus.append("indices d'entretien dans l'annonce")

    if image_count >= 4:
        score += 2
        plus.append("photos suffisantes")
    elif not image_count:
        score -= 4
        minus.append("pas ou peu de photos")

    if distance_km is None:
        score -= 4
        minus.append("distance exacte inconnue")
    elif distance_km <= 50:
        score += 5
        plus.append("très proche d'Aix")
    elif distance_km <= prefs.get("distance_standard_km", 100):
        score += 2
        plus.append("distance OK autour d'Aix")
    elif distance_km <= prefs.get("distance_good_deal_max_km", 150):
        score -= 7
        minus.append("distance 100-150 km : à justifier par un bon dossier")
    else:
        score -= 18
        minus.append("plus de 150 km : seulement pépite exceptionnelle")

    score = max(0, min(100, score))
    if style_hard_flags:
        score = min(score, 42)
    if style_score < 60:
        score = min(score, 65)
        minus.append("style trop neutre/vieillot pour être prioritaire Ben")
    elif style_score < 70 and equipment_score < 70:
        score = min(score, 82)
        minus.append("pas assez désirable pour Ben : style ou équipement trop basique")
    elif style_score < 70:
        score = min(score, 85)
        minus.append("style correct mais pas vraiment coup de cœur")
    elif equipment_score < 55:
        score = min(score, 86)
        minus.append("équipement trop léger : écran/Bluetooth/CarPlay à vérifier")
    puretech_like_risk = engine_risk_marker in {"puretech", "pure tech"} or (psa_brand and psa_small_engine)
    if puretech_like_risk:
        if has_engine_proof:
            score = min(score, 72)
            minus.append("moteur PureTech/VTi : jamais GO automatique même avec preuve")
        else:
            score = min(score, 54)
            hard_flags.append("PureTech/VTi sans preuve courroie/distribution béton")
    if psa_urban_unknown_engine:
        score = min(score, 72 if has_engine_proof else 64)
        minus.append("moteur PSA urbain à clarifier avant contact sérieux")
    if engine_penalty_applied and not puretech_like_risk:
        score = min(score, 76)
        minus.append("moteur à surveiller : contact uniquement si dossier clair")
    if "electrique" in fuel_norm or "électrique" in fuel_norm:
        score = min(score, 58)
        minus.append("électrique mis de côté par préférence Ben")
    if year and price and year >= 2021 and price <= prefs.get("budget_strict_max", 6000):
        score = min(score, 48)
        hard_flags.append("prix anormalement bas pour l'année : vérifier LLD/LOA/reprise ou annonce piège")
    mechanical_score = 72
    if found_red_flags:
        mechanical_score -= 35
    if engine_penalty_applied or (psa_brand and psa_small_engine):
        mechanical_score -= 18 if has_engine_proof else 30
    if fuel_norm and "diesel" in fuel_norm:
        mechanical_score -= 10
    if found_good_flags:
        mechanical_score += 10
    if mileage and mileage <= 130_000:
        mechanical_score += 8
    elif mileage and mileage > 160_000:
        mechanical_score -= 12
    mechanical_score = max(0, min(100, mechanical_score))

    if distance_km and distance_km > prefs.get("distance_good_deal_max_km", 150):
        exceptional_far_deal = (
            price is not None
            and price <= prefs.get("budget_target", 5500)
            and year is not None
            and year >= prefs.get("year_ideal_min", 2015)
            and mileage is not None
            and mileage <= 120_000
            and style_score >= 80
            and equipment_score >= 65
            and mechanical_score >= 80
            and found_good_flags
            and not hard_flags
            and not engine_penalty_applied
        )
        if exceptional_far_deal:
            plus.append("distance élevée acceptée : dossier potentiellement pépite")
            score = min(score + 5, 90)
        else:
            score = min(score, 58)
            hard_flags.append("trop loin pour Ben sans pépite exceptionnelle prouvée")
    elif distance_km and distance_km > prefs.get("distance_standard_km", 100):
        strong_mid_distance_deal = (
            score >= 80
            and price is not None
            and price <= 5600
            and style_score >= 70
            and mechanical_score >= 70
            and not hard_flags
        )
        if not strong_mid_distance_deal:
            score = min(score, 75)
            minus.append("100-150 km : pas assez fort pour être prioritaire")

    # FIX #1: Hard cap banned engines (wet belt PureTech/EcoBoost/TCe etc.) at 30
    banned, ban_reason = _is_banned_engine(ad)
    if banned:
        score = min(score, 30)
        if "moteur_banni" not in hard_flags:
            hard_flags.append("moteur_banni")
        minus.append(f"BANNI: {ban_reason}")

    if hard_flags or score < 45:
        verdict = "à éviter"
    elif (
        score >= 88
        and not hard_flags
        and not psa_urban_unknown_engine
        and style_score >= 70
        and equipment_score >= 65
    ):
        verdict = "pépite"
    elif score >= 78:
        verdict = "à contacter"
    elif score >= 65:
        verdict = "à creuser"
    else:
        verdict = "à éviter"

    return {
        "score": score,
        "verdict": verdict,
        "positive_points": plus[:6],
        "negative_points": minus[:6],
        "hard_flags": hard_flags[:6],
        "missing_fields": _missing_car_fields(ad),
        "ben_scores": {
            "mechanical": mechanical_score,
            "style": style_score,
            "equipment": equipment_score,
            "final_ben": score,
        },
        "style": {
            "positive_points": style_plus,
            "negative_points": style_minus,
        },
        "equipment": {
            "positive_points": equipment_plus,
            "negative_points": equipment_minus,
        },
        "normalized": {
            "price": price,
            "year": year,
            "mileage": mileage,
            "doors": doors,
            "fuel": fuel,
            "power": power,
            "distance_km": distance_km,
        },
    }


def _seller_message(ad: dict, score: dict) -> str:
    title = ad.get("title") or "votre voiture"
    city = (ad.get("location") or {}).get("city")
    intro = f"Bonjour, votre annonce {title} m'intéresse"
    if city:
        intro += f" à {city}"
    text = _combined_ad_text(ad)
    hard_flags = " ".join(score.get("hard_flags") or [])
    missing = set(score.get("missing_fields") or [])
    normalized = score.get("normalized") or {}

    if "pas 5 portes" in hard_flags or "portes" in missing:
        question = "Pouvez-vous me confirmer qu'elle est bien en 5 portes ?"
    elif any(marker in text or marker in _normalize_text(hard_flags) for marker in ["puretech", "pure tech", "vti", "courroie"]):
        question = "Avez-vous une facture récente pour la distribution ou la courroie ?"
    elif "contrôle technique" not in text and "controle technique" not in text and "ct ok" not in text:
        question = "Le contrôle technique est-il récent et sans contre-visite ?"
    elif not any(marker in text for marker in ["factures", "entretien", "carnet"]):
        question = "Avez-vous les factures d'entretien disponibles ?"
    elif normalized.get("distance_km") is None or (normalized.get("distance_km") or 0) > 100:
        question = "Dans quelle ville exacte est-elle visible ?"
    elif (score.get("ben_scores") or {}).get("equipment", 0) < 55:
        question = "Le Bluetooth ou l'écran fonctionne-t-il correctement ?"
    elif score.get("verdict") in {"pépite", "à contacter"}:
        question = "Est-elle toujours disponible pour une visite rapide ?"
    else:
        question = "Est-ce que le prix est légèrement négociable si le dossier est clair ?"

    return f"{intro}. {question}\n\nMerci."


def _extract_ad_id(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\b(\d{8,})\b", text)
    return match.group(1) if match else None


def _conversation_id(raw: dict) -> str:
    for key in ("conversation_id", "thread_id", "id"):
        if raw.get(key):
            return _redact_text(raw.get(key), 120)
    basis = " ".join([
        str(raw.get("seller_name") or raw.get("seller") or ""),
        str(raw.get("ad_id") or _extract_ad_id(str(raw.get("ad_url") or "")) or ""),
        str(raw.get("title") or raw.get("ad_title") or ""),
    ]).strip() or json.dumps(raw, ensure_ascii=False, sort_keys=True)[:500]
    return "snapshot-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _message_dedupe_key(target: dict, message: str) -> str:
    parts = [
        "leboncoin",
        str(target.get("conversation_id") or ""),
        str(target.get("ad_id") or ""),
        str(target.get("seller_id") or ""),
        str(target.get("ad_url") or ""),
        _message_hash(message),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _read_message_journal() -> list[dict]:
    return _read_state("account_message_journal.json", [])


def _append_message_journal(entry: dict) -> dict:
    journal = _read_message_journal()
    clean = _sanitize_for_storage(entry, 1200)
    clean["updated_at"] = _now_iso()
    journal.append(clean)
    _write_state("account_message_journal.json", journal[-500:])
    return clean


def _conversation_summary(raw: dict, include_messages: bool = False) -> dict:
    messages = _conversation_messages(raw)
    last_message = messages[-1] if messages else {}
    summary = {
        "conversation_id": raw.get("conversation_id"),
        "seller_name": raw.get("seller_name"),
        "seller_id": raw.get("seller_id"),
        "ad_id": raw.get("ad_id"),
        "ad_url": raw.get("ad_url"),
        "title": raw.get("title") or raw.get("ad_title"),
        "price": raw.get("price"),
        "city": raw.get("city"),
        "last_message_at": last_message.get("timestamp") or raw.get("last_message_at"),
        "last_sender": last_message.get("sender"),
        "last_message_preview": _redact_text(last_message.get("text"), 300),
        "message_count": len(messages),
        "status": raw.get("status") or "snapshot",
    }
    if include_messages:
        summary["messages"] = [_sanitize_for_storage(message, 1200) for message in messages[-12:]]
    return _sanitize_for_storage(summary, 1200)


def _conversation_action(conversation: dict) -> dict:
    messages = _conversation_messages(conversation)
    last_text = _normalize_text(messages[-1].get("text") if messages else "")
    joined = _normalize_text(" ".join(str(m.get("text") or "") for m in messages[-6:]))
    title = str(conversation.get("title") or conversation.get("ad_title") or "l'annonce")

    if any(flag in joined for flag in ["acompte", "western union", "virement avant", "mandat cash"]):
        status = "abandonner"
        reason = "signal paiement/pression suspect"
        next_question = None
    elif any(flag in joined for flag in ["pas de facture", "aucune facture", "pas d'entretien"]):
        status = "abandonner"
        reason = "dossier entretien trop faible"
        next_question = None
    elif "controle technique" not in joined and "contrôle technique" not in joined and "ct" not in joined:
        status = "continuer"
        reason = "CT à vérifier avant d'aller plus loin"
        next_question = "Bonjour, le contrôle technique est-il récent et sans contre-visite ?"
    elif not any(flag in joined for flag in ["facture", "entretien", "carnet"]):
        status = "continuer"
        reason = "historique d'entretien à demander"
        next_question = "Bonjour, avez-vous les factures d'entretien disponibles ?"
    elif any(flag in last_text for flag in ["disponible", "oui", "possible", "ok"]):
        status = "continuer"
        reason = "vendeur répond positivement"
        next_question = "Bonjour, est-il possible de la voir avec un démarrage à froid ?"
    elif not messages:
        status = "relancer"
        reason = "conversation vide ou snapshot incomplet"
        next_question = f"Bonjour, votre annonce {title} m'intéresse. Est-elle toujours disponible ?"
    else:
        status = "relancer"
        reason = "information utile manquante ou réponse incomplète"
        next_question = "Bonjour, pouvez-vous me confirmer que le dossier d'entretien est clair ?"

    return {
        "conversation_id": conversation.get("conversation_id"),
        "ad_id": conversation.get("ad_id"),
        "title": conversation.get("title") or conversation.get("ad_title"),
        "decision": status,
        "reason": reason,
        "next_message": next_question,
        "requires_ben_go_before_send": True,
    }


_OWNERS_ATTR_KEYS = (
    "Propriétaires précédents",
    "Nombre de propriétaires précédents",
    "Nombre de propriétaires",
    "owners_previous",
)
_AUTOVIZA_ATTR_KEYS = (
    "vehicle_history_report_public_url",
    "Autoviza",
    "autoviza_report_url",
)


def _extract_owners_count(attrs: dict) -> tuple[Optional[int], Optional[str]]:
    """FIX #2: Find 'Propriétaires précédents' (or variants) in attributes and parse the int."""
    if not isinstance(attrs, dict):
        return None, None
    for key in _OWNERS_ATTR_KEYS:
        if key in attrs and attrs[key] is not None:
            raw = str(attrs[key])
            match = re.search(r"\d+", raw)
            if match:
                return int(match.group(0)), raw
            return None, raw
    # fallback : scan keys with normalized matching (case/accent tolerant)
    for k, v in attrs.items():
        kn = _normalize_text(str(k))
        if "proprietaire" in kn and v is not None:
            raw = str(v)
            match = re.search(r"\d+", raw)
            if match:
                return int(match.group(0)), raw
            return None, raw
    return None, None


def _extract_autoviza_url(ad_obj: Any, attrs: dict) -> Optional[str]:
    """FIX #3: Try multiple shapes to find an Autoviza public URL associated with the ad."""
    for key in _AUTOVIZA_ATTR_KEYS:
        if isinstance(attrs, dict) and key in attrs and attrs[key]:
            url = str(attrs[key]).strip()
            if url.startswith("https://autoviza.fr/"):
                return url
    for attr_name in ("vehicle_history_report_public_url", "autoviza_url"):
        try:
            value = getattr(ad_obj, attr_name, None)
        except Exception:
            value = None
        if value:
            url = str(value).strip()
            if url.startswith("https://autoviza.fr/"):
                return url
    return None


def _ad_to_dict(ad: lbc.Ad) -> dict:
    attrs = {}
    for a in ad.attributes:
        label = a.key_label or a.key
        attrs[label] = a.value_label or a.value

    loc = ad.location
    result = {
        "id": ad.id,
        "title": ad.subject,
        "price": ad.price,
        "url": ad.url,
        "category": ad.category_name,
        "ad_type": ad.ad_type,
        "body": ad.body,
        "images": ad.images or [],
        "first_publication_date": ad.first_publication_date,
        "location": {
            "city": loc.city_label,
            "zipcode": loc.zipcode,
            "department": loc.department_name,
            "region": loc.region_name,
            "lat": loc.lat,
            "lng": loc.lng,
        },
        "attributes": attrs,
        "has_phone": ad.has_phone,
    }

    # FIX #2: surface owners_count / owners_raw
    owners_count, owners_raw = _extract_owners_count(attrs)
    result["owners_count"] = owners_count
    result["owners_raw"] = owners_raw

    # FIX #3: surface Autoviza public URL if exposed
    autoviza_url = _extract_autoviza_url(ad, attrs)
    if autoviza_url:
        result["autoviza_url"] = autoviza_url

    return result


def _user_to_dict(user: lbc.User) -> dict:
    result = {
        "id": user.id,
        "name": user.name,
        "is_pro": user.is_pro,
        "account_type": user.account_type,
        "registered_at": user.registered_at,
        "total_ads": user.total_ads,
        "description": user.description,
        "profile_picture": user.profile_picture,
    }
    if user.feedback and user.feedback.overall_score:
        result["feedback_score"] = user.feedback.score
        result["feedback_count"] = user.feedback.received_count
    if user.pro:
        result["pro_info"] = {
            "store_name": user.pro.online_store_name,
            "activity_sector": user.pro.activity_sector,
            "siren": user.pro.siren,
            "website": user.pro.website_url,
            "slogan": user.pro.slogan,
        }
    return result


def _build_location(
    city: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
    radius: Optional[int],
    region: Optional[str],
    department: Optional[str],
) -> list | None:
    locations = []
    if latitude is not None and longitude is not None:
        locations.append(lbc.City(
            lat=latitude,
            lng=longitude,
            radius=radius or 30_000,
            city=city or "",
        ))
    if region and region.upper() in REGION_MAP:
        locations.append(REGION_MAP[region.upper()])
    if department and department.upper() in DEPARTMENT_MAP:
        locations.append(DEPARTMENT_MAP[department.upper()])
    return locations or None


@mcp.tool()
def search_ads(
    text: Optional[str] = None,
    url: Optional[str] = None,
    category: Optional[str] = None,
    city: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius: Optional[int] = None,
    region: Optional[str] = None,
    department: Optional[str] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    sort: str = "NEWEST",
    ad_type: str = "OFFER",
    owner_type: Optional[str] = None,
    shippable: Optional[bool] = None,
    page: int = 1,
    limit: int = 10,
) -> dict:
    """Search for ads on Leboncoin.

    Args:
        text: Search query (e.g. "vélo électrique", "appartement 3 pièces").
        url: Full Leboncoin search URL. Overrides text/category/location params.
        category: Category name like VEHICULES, IMMOBILIER, ELECTRONIQUE, LOISIRS, MODE, etc.
            Full list: TOUTES_CATEGORIES, EMPLOI, VEHICULES, VEHICULES_VOITURES, VEHICULES_MOTOS,
            IMMOBILIER, IMMOBILIER_VENTES_IMMOBILIERES, IMMOBILIER_LOCATIONS, ELECTRONIQUE,
            MAISON_ET_JARDIN, MODE, LOISIRS, ANIMAUX, SERVICES, DONS, DIVERS.
        city: City name (informational, used with lat/lng).
        latitude: Latitude for location search.
        longitude: Longitude for location search.
        radius: Search radius in meters (default 30000 = 30km).
        region: Region name (e.g. ILE_DE_FRANCE, BRETAGNE, PROVENCE_ALPES_COTE_D_AZUR).
        department: Department name (e.g. PARIS, GIRONDE, BOUCHES_DU_RHONE).
        price_min: Minimum price in euros.
        price_max: Maximum price in euros.
        sort: Sort order: NEWEST, OLDEST, CHEAPEST, EXPENSIVE, RELEVANCE.
        ad_type: OFFER or DEMAND.
        owner_type: PRO, PRIVATE, or ALL.
        shippable: Filter for shippable items only.
        page: Page number (starts at 1).
        limit: Results per page (max 35).
    """
    kwargs = {}

    if url:
        kwargs["url"] = url
    else:
        if text:
            kwargs["text"] = text
        if category and category.upper() in CATEGORY_MAP:
            kwargs["category"] = CATEGORY_MAP[category.upper()]

        locations = _build_location(city, latitude, longitude, radius, region, department)
        if locations:
            kwargs["locations"] = locations

    price = None
    if price_min is not None or price_max is not None:
        price = [price_min or 0, price_max or 999_999_999]
    if price:
        kwargs["price"] = price

    kwargs["sort"] = SORT_MAP.get(sort.upper(), lbc.Sort.NEWEST)
    kwargs["ad_type"] = AD_TYPE_MAP.get(ad_type.upper(), lbc.AdType.OFFER)
    if owner_type and owner_type.upper() in OWNER_TYPE_MAP:
        kwargs["owner_type"] = OWNER_TYPE_MAP[owner_type.upper()]
    if shippable is not None:
        kwargs["shippable"] = shippable
    kwargs["page"] = page
    kwargs["limit"] = min(limit, 35)
    kwargs["limit_alu"] = 0

    result = _client.search(**kwargs)

    return {
        "total": result.total,
        "total_pro": result.total_pro,
        "total_private": result.total_private,
        "max_pages": result.max_pages,
        "page": page,
        "ads": [_ad_to_dict(ad) for ad in result.ads],
    }


@mcp.tool()
def parse_car_search_url(url: str = BEN_DEFAULT_CAR_URL) -> dict:
    """Parse a Leboncoin car search URL into a transparent Karim/Ben profile."""
    safe_url, url_error = _require_leboncoin_url(url, ("/recherche", "/recherche/"), strip_query=False)
    if url_error:
        return {"ok": False, "error": url_error}
    profile = _profile_from_url(safe_url)
    profile["ok"] = True
    return profile


@mcp.tool()
def parse_search_url(url: str) -> dict:
    """Parse a generic Leboncoin search URL without applying car-specific rules."""
    safe_url, url_error = _require_leboncoin_url(url, ("/recherche", "/recherche/"))
    if url_error:
        return {"ok": False, "error": url_error}
    parsed = urlparse(_redact_text(url, 2000))
    query = parse_qs(parsed.query)
    price_min, price_max = _parse_range_token(_first(query, "price"))
    return {
        "ok": True,
        "url": safe_url,
        "criteria": {
            "text": _first(query, "text") or _first(query, "q"),
            "category": _first(query, "category"),
            "locations": query.get("locations", []),
            "price_min": price_min,
            "price_max": price_max,
            "sort": _first(query, "sort"),
            "order": _first(query, "order"),
            "owner_type": _first(query, "owner_type"),
            "raw_params": _sanitize_for_storage({key: values[:5] for key, values in query.items()}, 300),
        },
        "safety": {
            "cookies_or_passwords_stored": False,
            "car_rules_applied": False,
        },
    }


@mcp.tool()
def save_car_search_profile(
    profile_id: str = "ben-voiture",
    name: str = "Ben - Voiture Aix/PACA",
    url: str = BEN_DEFAULT_CAR_URL,
) -> dict:
    """Save a reusable local car search profile without storing cookies or secrets."""
    safe_url, url_error = _require_leboncoin_url(url, ("/recherche", "/recherche/"), strip_query=False)
    if url_error:
        return {"ok": False, "error": url_error}
    profiles = _read_state("car_profiles.json", {})
    profile = _profile_from_url(safe_url)
    profile["profile_id"] = profile_id
    profile["name"] = name
    profiles[profile_id] = {
        "ok": True,
        "profile_id": profile_id,
        "name": name,
        "url": safe_url,
        "profile": profile,
        "updated_at": _now_iso(),
    }
    _write_state("car_profiles.json", profiles)
    return profiles[profile_id]


@mcp.tool()
def list_car_search_profiles() -> dict:
    """List saved local car search profiles."""
    return _read_state("car_profiles.json", {})


@mcp.tool()
def get_car_search_profile(profile_id: str = "ben-voiture") -> dict:
    """Get one saved car search profile, or the Ben default if none exists yet."""
    profiles = _read_state("car_profiles.json", {})
    if profile_id in profiles:
        return profiles[profile_id]
    profile = _profile_from_url(BEN_DEFAULT_CAR_URL)
    profile["profile_id"] = profile_id
    profile["name"] = "Ben - Voiture Aix/PACA"
    return {
        "ok": True,
        "profile_id": profile_id,
        "name": "Ben - Voiture Aix/PACA",
        "url": BEN_DEFAULT_CAR_URL,
        "profile": profile,
        "stored": False,
        "note": "Profil par défaut retourné sans écriture locale. Utiliser save_car_search_profile pour l'enregistrer.",
    }


@mcp.tool()
def run_car_search_profile(
    profile_id: str = "ben-voiture",
    url: Optional[str] = None,
    max_pages: int = 8,
    limit: int = 10,
    commit_seen: bool = False,
    save_digest: bool = False,
) -> dict:
    """Preview Ben's car profile search; local seen/digest writes require explicit commit flags."""
    profile_record = get_car_search_profile(profile_id)
    profile = profile_record["profile"]
    if url:
        safe_url, url_error = _require_leboncoin_url(url, ("/recherche", "/recherche/"), strip_query=False)
        if url_error:
            return {"ok": False, "error": url_error, "messages_sent": 0}
        profile = _profile_from_url(safe_url)

    location = profile.get("location") or {}
    locations = _build_location(
        location.get("city"),
        location.get("latitude"),
        location.get("longitude"),
        location.get("radius"),
        None,
        None,
    )

    advanced_filters = {}
    if profile.get("price_min") is not None or profile.get("price_max") is not None:
        advanced_filters["price"] = [profile.get("price_min") or 0, profile.get("price_max") or 999999999]
    if profile.get("mileage_min") is not None or profile.get("mileage_max") is not None:
        advanced_filters["mileage"] = [profile.get("mileage_min") or 0, profile.get("mileage_max") or 999999999]
    if profile.get("regdate_min") is not None or profile.get("regdate_max") is not None:
        advanced_filters["regdate"] = [profile.get("regdate_min") or 0, profile.get("regdate_max") or datetime.now().year + 1]
    if profile.get("doors") is not None:
        advanced_filters["doors"] = [str(profile["doors"])]
    if profile.get("fuel_codes"):
        advanced_filters["fuel"] = [str(code) for code in profile["fuel_codes"]]

    seen = _read_state("car_seen_ads.json", {})
    seen_for_profile = set(seen.get(profile_id, []))
    decisions = _read_state("car_decisions.json", [])
    rejected_ids = {
        str(item.get("ad_id"))
        for item in decisions
        if item.get("profile_id") == profile_id and item.get("status") == "rejected"
    }
    # FIX #4: also exclude already-contacted / archived ads from the top
    contacted_ids = {
        str(item.get("ad_id"))
        for item in decisions
        if item.get("profile_id") == profile_id and item.get("status") == "contacted_manual"
    }
    archived_ids = {
        str(item.get("ad_id"))
        for item in decisions
        if item.get("profile_id") == profile_id and item.get("status") == "archived"
    }
    candidates = []
    rejected_counts = {}
    scanned = 0
    pages_used = 0
    search_errors = []

    for page in range(1, max(1, min(max_pages, 25)) + 1):
        pages_used = page
        try:
            result = _client.search(
                category=lbc.Category.VEHICULES_VOITURES,
                locations=locations,
                sort=lbc.Sort.NEWEST,
                owner_type=OWNER_TYPE_MAP.get(str(profile.get("owner_type", "all")).upper()),
                ad_type=lbc.AdType.OFFER,
                page=page,
                limit=35,
                limit_alu=0,
                **advanced_filters,
            )
        except Exception as exc:
            search_errors.append(_redact_text(f"{type(exc).__name__}: {exc}", 400))
            break

        ads = [_ad_to_dict(ad) for ad in result.ads]
        if not ads:
            break
        scanned += len(ads)

        for ad in ads:
            accepted, reasons = _post_filter_ad(ad, profile)
            if not accepted:
                for reason in reasons:
                    rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
                continue
            ad_id = str(ad.get("id"))
            if ad_id in rejected_ids:
                rejected_counts["deja_rejete_par_ben"] = rejected_counts.get("deja_rejete_par_ben", 0) + 1
                continue
            # FIX #4: skip already-contacted ads (so they don't pollute the top)
            if ad_id in contacted_ids:
                rejected_counts["deja_contacte_par_ben"] = rejected_counts.get("deja_contacte_par_ben", 0) + 1
                continue
            if ad_id in archived_ids:
                rejected_counts["deja_archive_par_ben"] = rejected_counts.get("deja_archive_par_ben", 0) + 1
                continue
            score = _score_car(ad, profile)
            enriched = {
                "id": ad_id,
                "title": ad.get("title"),
                "url": ad.get("url"),
                "price": score["normalized"]["price"],
                "year": score["normalized"]["year"],
                "mileage": score["normalized"]["mileage"],
                "doors": score["normalized"]["doors"],
                "fuel": score["normalized"]["fuel"],
                "city": (ad.get("location") or {}).get("city"),
                "first_publication_date": ad.get("first_publication_date"),
                "already_seen": ad_id in seen_for_profile,
                "score": score,
                "draft_message": _seller_message(ad, score),
            }
            candidates.append(enriched)

    candidates.sort(key=lambda item: (item["score"]["score"], not item["already_seen"]), reverse=True)
    top = candidates[: max(1, min(limit, 25))]

    if commit_seen and top:
        seen_for_profile.update(item["id"] for item in top)
        seen[profile_id] = sorted(seen_for_profile)
        _write_state("car_seen_ads.json", seen)

    digest = {
        "profile": profile,
        "generated_at": _now_iso(),
        "pages_used": pages_used,
        "scanned_ads": scanned,
        "matched_ads": len(candidates),
        "returned_ads": len(top),
        "rejected_counts": rejected_counts,
        "search_errors": search_errors,
        "top_ads": top,
        "safety": {
            "messages_sent": 0,
            "requires_ben_go_before_send": True,
            "cookies_or_passwords_stored": False,
            "read_only_by_default": True,
            "commit_seen": bool(commit_seen),
            "save_digest": bool(save_digest),
        },
    }
    if save_digest:
        _write_state("last_car_digest.json", digest)
    return digest


@mcp.tool()
def rank_car_candidates(
    profile_id: str = "ben-voiture",
    url: Optional[str] = None,
    max_pages: int = 8,
    limit: int = 15,
    include_seen: bool = False,
) -> dict:
    """Rank Ben-compatible car candidates with desirability, equipment, and draft seller messages."""
    digest = run_car_search_profile(
        profile_id=profile_id,
        url=url,
        max_pages=max_pages,
        limit=max(limit, 25) if include_seen else limit,
    )
    if not include_seen:
        digest["top_ads"] = [ad for ad in digest["top_ads"] if not ad.get("already_seen")][:limit]
        digest["returned_ads"] = len(digest["top_ads"])
    digest["ranking_note"] = (
        "Score V3: mécanique + fit Ben + style + équipement + distance. "
        "Monospaces/familiales/voitures vieillottes sont fortement pénalisés."
    )
    return digest


@mcp.tool()
def score_car_desirability(ad_id: str, profile_id: str = "ben-voiture") -> dict:
    """Score one Leboncoin car ad for Ben using Karim V3 desirability rules."""
    return score_car_ad(ad_id=ad_id, profile_id=profile_id)


@mcp.tool()
def score_car_ad(ad_id: str, profile_id: str = "ben-voiture") -> dict:
    """Fetch and score one Leboncoin car ad with Karim's current criteria."""
    ad = get_ad(ad_id)
    profile = get_car_search_profile(profile_id)["profile"]
    score = _score_car(ad, profile)
    return {
        "ad": {
            "id": ad.get("id"),
            "title": ad.get("title"),
            "url": ad.get("url"),
            "city": (ad.get("location") or {}).get("city"),
        },
        "score": score,
        "draft_message": _seller_message(ad, score),
    }


@mcp.tool()
def import_car_favorites(
    items: list[str],
    profile_id: str = "ben-voiture",
    source_name: str = "manual",
) -> dict:
    """Import Ben's pasted Leboncoin favorite URLs or ad IDs locally, without cookies, login, or secrets."""
    favorites = _read_state("car_favorites.json", {})
    profile_favorites = favorites.get(profile_id, [])
    existing = {str(item.get("ad_id")) for item in profile_favorites}
    imported = []
    ignored = []

    for item in items:
        safe_item = _redact_text(item, 1200)
        ad_id = _extract_ad_id(safe_item)
        if not ad_id:
            ignored.append({"item": safe_item, "reason": "ad_id_introuvable"})
            continue
        if ad_id in existing:
            ignored.append({"item": safe_item, "ad_id": ad_id, "reason": "deja_importe"})
            continue
        entry = {
            "profile_id": profile_id,
            "ad_id": ad_id,
            "source": source_name,
            "status": "favorite",
            "added_at": _now_iso(),
        }
        profile_favorites.append(entry)
        existing.add(ad_id)
        imported.append(entry)

    favorites[profile_id] = profile_favorites
    _write_state("car_favorites.json", favorites)
    return {
        "ok": True,
        "profile_id": profile_id,
        "imported_count": len(imported),
        "ignored_count": len(ignored),
        "imported": imported,
        "ignored": ignored,
        "safety": {
            "cookies_or_passwords_stored": False,
            "stores_only_ad_ids_and_source": True,
        },
    }


@mcp.tool()
def list_car_favorites(profile_id: str = "ben-voiture", status: Optional[str] = None) -> dict:
    """List locally imported car favorites."""
    favorites = _read_state("car_favorites.json", {})
    entries = favorites.get(profile_id, [])
    if status:
        entries = [entry for entry in entries if entry.get("status") == status]
    return {
        "profile_id": profile_id,
        "count": len(entries),
        "favorites": entries,
    }


@mcp.tool()
def score_car_favorites(
    profile_id: str = "ben-voiture",
    limit: int = 10,
    refresh: bool = True,
) -> dict:
    """Fetch, score, and rank imported favorite car ads. Never logs into Leboncoin."""
    profile = get_car_search_profile(profile_id)["profile"]
    favorites = _read_state("car_favorites.json", {}).get(profile_id, [])
    ranked = []
    errors = []

    for fav in favorites:
        ad_id = str(fav.get("ad_id"))
        try:
            ad = get_ad(ad_id) if refresh else {"id": ad_id}
            score = _score_car(ad, profile)
            ranked.append({
                "favorite": fav,
                "ad": {
                    "id": ad.get("id"),
                    "title": ad.get("title"),
                    "url": ad.get("url"),
                    "city": (ad.get("location") or {}).get("city"),
                    "price": score["normalized"]["price"],
                    "year": score["normalized"]["year"],
                    "mileage": score["normalized"]["mileage"],
                    "doors": score["normalized"]["doors"],
                    "fuel": score["normalized"]["fuel"],
                },
                "score": score,
                "draft_message": _seller_message(ad, score),
            })
        except Exception as exc:
            errors.append({"ad_id": ad_id, "error": _redact_text(f"{type(exc).__name__}: {exc}", 400)})

    ranked.sort(key=lambda item: item["score"]["score"], reverse=True)
    digest = {
        "profile_id": profile_id,
        "generated_at": _now_iso(),
        "input_favorites": len(favorites),
        "ranked_count": len(ranked),
        "returned_count": min(limit, len(ranked)),
        "top_favorites": ranked[: max(1, min(limit, 25))],
        "errors": errors,
        "safety": {
            "messages_sent": 0,
            "requires_ben_go_before_send": True,
            "cookies_or_passwords_stored": False,
        },
    }
    _write_state("last_car_favorites_digest.json", digest)
    return digest


@mcp.tool()
def record_car_decision(
    ad_id: str,
    status: str,
    note: Optional[str] = None,
    profile_id: str = "ben-voiture",
) -> dict:
    """Record Ben's decision for one ad: rejected, watchlist, contact_ready, contacted_manual, archived."""
    allowed = {
        "rejected", "watchlist", "contact_ready", "contacted_manual",
        "archived", "shortlisted", "duplicate",
    }
    status_norm = status.lower().strip()
    if status_norm not in allowed:
        return {"ok": False, "error": f"status must be one of: {sorted(allowed)}"}
    decisions = _read_state("car_decisions.json", [])
    entry = {
        "profile_id": profile_id,
        "ad_id": str(ad_id),
        "status": status_norm,
        "note": note,
        "updated_at": _now_iso(),
    }
    decisions.append(entry)
    _write_state("car_decisions.json", decisions)
    return {"ok": True, "entry": entry}


@mcp.tool()
def list_car_decisions(profile_id: str = "ben-voiture") -> dict:
    """List local decisions already recorded for Ben's car search profile."""
    decisions = _read_state("car_decisions.json", [])
    return {
        "profile_id": profile_id,
        "decisions": [d for d in decisions if d.get("profile_id") == profile_id],
    }


@mcp.tool()
def login_status() -> dict:
    """Report Leboncoin account mode without exposing cookies, tokens, or passwords."""
    favorites = _read_state("account_favorites.json", {})
    conversations = _read_state("account_conversations.json", {})
    status = _read_state("account_login_status.json", {})
    comet_tabs = _debug_browser_tabs(COMET_DEBUG_URL)
    external_tabs = _debug_browser_tabs(ACCOUNT_BROWSER_DEBUG_URL)
    leboncoin_tab_count = len([tab for tab in comet_tabs + external_tabs if _is_leboncoin_tab(tab)])
    snapshot_count = len(favorites.get("items", [])) + len(conversations.get("items", []))
    account_status = "browser_visible" if leboncoin_tab_count else ("snapshot_available" if snapshot_count else "not_connected")
    return {
        "connected": False,
        "account_status": account_status,
        "mode": "browser_snapshot_safe_v2",
        "reason": (
            "La librairie lbc locale ne fournit pas de login compte, favoris privés, "
            "conversations ou envoi. Le MCP accepte donc des snapshots navigateur "
            "sanitisés et bloque tout envoi réel tant qu'une passerelle compte n'est pas prouvée. "
            "Un navigateur visible ne suffit pas à déclarer connected=true."
        ),
        "login_url": ACCOUNT_LOGIN_URL,
        "favorites_snapshot_count": len(favorites.get("items", [])),
        "conversations_snapshot_count": len(conversations.get("items", [])),
        "leboncoin_browser_tab_count": leboncoin_tab_count,
        "last_login_opened_at": status.get("last_login_opened_at"),
        "safety": {
            "cookies_or_passwords_stored": False,
            "send_requires_explicit_ben_go": True,
            "real_send_enabled": False,
        },
    }


@mcp.tool()
def open_account_login(
    target: str = "messages",
    browser_mode: str = "comet",
    force_reopen: bool = False,
    enable_control: bool = True,
) -> dict:
    """Open Leboncoin login/messages/favorites in Comet by default, without storing secrets."""
    target_urls = {
        "login": ACCOUNT_LOGIN_URL,
        "messages": ACCOUNT_MESSAGES_URL,
        "favorites": ACCOUNT_FAVORITES_URL,
    }
    url = target_urls.get(str(target).lower(), ACCOUNT_MESSAGES_URL)
    opened = False
    error = None
    browser = None
    profile_dir = str(_browser_profile_path())
    browser_mode, mode_error = _normalize_browser_mode(browser_mode)
    if mode_error:
        return {"ok": False, "error": mode_error, "messages_sent": 0}

    try:
        if browser_mode == "comet":
            browser = "Comet"
            result = subprocess.run(["open", "-a", browser, url], check=False, timeout=5)
            opened = result.returncode == 0
            if not opened:
                error = "comet_not_available"
        else:
            if force_reopen and enable_control:
                for pid in _account_browser_pids():
                    subprocess.run(["kill", str(pid)], check=False, timeout=2)
                time.sleep(1.0)
            for candidate in ACCOUNT_BROWSER_APPS:
                browser_args = [
                    f"--user-data-dir={profile_dir}",
                    "--new-window",
                ]
                if enable_control:
                    browser_args.extend([
                        f"--remote-debugging-address={ACCOUNT_BROWSER_DEBUG_HOST}",
                        f"--remote-debugging-port={ACCOUNT_BROWSER_DEBUG_PORT}",
                    ])
                browser_args.append(url)
                result = subprocess.run(
                    ["open", "-na", candidate, "--args", *browser_args],
                    check=False,
                    timeout=5,
                )
                if result.returncode == 0:
                    browser = candidate
                    opened = True
                    break
            if not opened:
                error = "dedicated_external_browser_not_available"
    except Exception as exc:
        error = _redact_text(f"{type(exc).__name__}: {exc}", 500)

    status = _read_state("account_login_status.json", {})
    status.update({
        "last_login_opened_at": _now_iso(),
        "last_target": target,
        "last_url": url,
        "browser": browser,
        "browser_mode": browser_mode,
        "profile_dir": profile_dir,
        "control_enabled": bool(enable_control),
        "debug_url": ACCOUNT_BROWSER_DEBUG_URL if enable_control and browser_mode == "external" else None,
        "debug_reachable": _account_browser_reachable() if enable_control and browser_mode == "external" else False,
        "opened": opened,
        "error": error,
    })
    _write_state("account_login_status.json", status)
    return {
        "ok": opened,
        "url": url,
        "browser": browser,
        "browser_mode": browser_mode,
        "profile_dir": profile_dir,
        "control_enabled": bool(enable_control),
        "debug_url": ACCOUNT_BROWSER_DEBUG_URL if enable_control and browser_mode == "external" else None,
        "debug_reachable": _account_browser_reachable() if enable_control and browser_mode == "external" else False,
        "error": error,
        "next_step": (
            "Connecte-toi dans Comet si nécessaire. Une fois les favoris ou conversations "
            "visibles, Codex peut lire la page via Comet Bridge et importer un snapshot "
            "sanitisé avec import_account_favorites_snapshot ou import_account_conversations_snapshot."
        ),
        "safety": {
            "cookies_or_passwords_stored": False,
            "browser_only": True,
            "comet_default": browser_mode == "comet",
            "dedicated_profile": browser_mode == "external",
            "profile_permissions": "0700",
            "debug_port_local_only": enable_control and browser_mode == "external",
        },
    }


@mcp.tool()
def account_browser_status(browser_mode: str = "comet") -> dict:
    """Report whether Comet or the dedicated external browser is locally readable."""
    browser_mode, mode_error = _normalize_browser_mode(browser_mode)
    if mode_error:
        return {"browser_mode": browser_mode, "debug_reachable": False, "error": mode_error}
    debug_url = _browser_mode_debug_url(browser_mode)
    tabs = _debug_browser_tabs(debug_url)
    leboncoin_tabs = [tab for tab in tabs if _is_leboncoin_tab(tab)]
    return {
        "browser_mode": browser_mode,
        "running": True if browser_mode == "comet" and tabs else bool(_account_browser_pids()),
        "debug_reachable": _debug_browser_reachable(debug_url),
        "debug_url": debug_url,
        "profile_dir": None if browser_mode == "comet" else str(_browser_profile_path()),
        "tab_count": len(tabs),
        "leboncoin_tab_count": len(leboncoin_tabs),
        "account_surface_visible": bool(leboncoin_tabs),
        "safety": {
            "cookies_or_passwords_stored": False,
            "comet_default": browser_mode == "comet",
            "profile_isolated": browser_mode == "external",
            "debug_localhost_only": True,
            "tab_titles_or_urls_returned": False,
        },
    }


FAVORITES_EXTRACTOR_JS = r"""
(async () => {
  await new Promise(resolve => setTimeout(resolve, 1800));
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const adUrl = href => /\/ad\/|\/offre\/|\/voitures\/|\/vehicules\/|\d{8,}/i.test(href || '');
  const anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => adUrl(a.href));
  const seen = new Set();
  const items = [];
  for (const anchor of anchors) {
    const card = anchor.closest('article, li, [data-test-id], [data-testid], [class*="card"], [class*="item"]') || anchor;
    const url = new URL(anchor.href, location.href).href;
    const text = norm(card.innerText || anchor.innerText || anchor.getAttribute('aria-label') || '');
    const key = url.split('?')[0];
    if (!text || seen.has(key)) continue;
    seen.add(key);
    items.push({
      title: norm(anchor.innerText || text.split(' ').slice(0, 18).join(' ')),
      url,
      raw: text.slice(0, 900),
    });
  }
  return {
    page_url: location.href,
    page_title: document.title,
    item_count: items.length,
    items: items.slice(0, 80),
  };
})()
"""


CONVERSATIONS_EXTRACTOR_JS = r"""
(async () => {
  await new Promise(resolve => setTimeout(resolve, 2200));
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const messageLinks = Array.from(document.querySelectorAll('a[href*="/messages/id/"]'));
  const nodes = messageLinks
    .map(link => link.closest('article, li, [role="listitem"], [data-test-id], [data-testid], [class*="conversation"], [class*="thread"]') || link);
  const seen = new Set();
  const items = [];
  for (const node of nodes) {
    const link = node.matches && node.matches('a[href]') ? node : node.querySelector && node.querySelector('a[href]');
    const href = link ? new URL(link.href, location.href).href : '';
    if (!/\/messages\/id\//.test(href) || href.includes('#')) continue;
    const text = norm(node.innerText || node.textContent || '');
    if (text.length < 12) continue;
    const key = href.split('?')[0];
    if (seen.has(key)) continue;
    seen.add(key);
    const lines = text
      .split(/(?<=\S)\s{2,}|[\n\r]+/)
      .map(norm)
      .filter(Boolean)
      .filter(line => !/^(aller au contenu|aller au pied de page|renforcer les contrastes|maison & jardin|électronique|bons plans !)$/i.test(line));
    if (!lines.length) continue;
    items.push({
      conversation_id: href || undefined,
      url: href || undefined,
      seller_name: lines[0] || undefined,
      ad_title: lines.find(line => /€|km|clio|corsa|208|polo|voiture|citro|renault|peugeot|opel|suzuki|volkswagen|vw/i.test(line)) || lines[1],
      last_message: lines.slice(-3).join(' | ') || text.slice(0, 500),
      messages: lines.slice(-8).map((line, index) => ({index, text: line})),
      raw: text.slice(0, 1200),
    });
  }
  return {
    page_url: location.href,
    page_title: document.title,
    item_count: items.length,
    items: items.slice(0, 80),
  };
})()
"""


CONVERSATION_DETAIL_EXTRACTOR_JS = r"""
(async () => {
  await new Promise(resolve => setTimeout(resolve, 2200));
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const rawBody = document.body.innerText || '';
  const body = norm(rawBody);
  const lines = body ? rawBody
    .split(/[\n\r]+/)
    .map(norm)
    .filter(Boolean)
    .filter(line => !/^(aller au contenu|aller au pied de page|renforcer les contrastes|déposer une annonce|mes recherches|favoris|messages|immobilier|véhicules|vacances|emploi|mode|maison & jardin|famille|électronique|loisirs|autres|bons plans !|active|tout|non lus|sélectionner)$/i.test(line))
    : [];
  const messageLike = lines.filter(line =>
    !/^à propos de ce vendeur/i.test(line) &&
    !/^nos conseils pour acheter/i.test(line) &&
    !/^ici, nous garantissons/i.test(line) &&
    !/^lancer la réservation/i.test(line) &&
    !/^dernière activité/i.test(line) &&
    !/^temps de réponse/i.test(line)
  );
  const reversed = [...lines].reverse();
  const titleLine = reversed.find(line =>
    /clio|corsa|208|swift|c3|c4|sandero|fiesta|polo|golf|ibiza|fabia|yaris|voiture/i.test(line) &&
    !/bonjour|facture|réparation|distribution|contrôle|entretien|disponible|vérifiez|prix:/i.test(line)
  );
  const priceLine = reversed.find(line => /^\d[\d\s]*\s*€$/.test(line));
  return {
    page_url: location.href,
    page_title: document.title,
    title: titleLine,
    price_line: priceLine,
    lines: messageLike.slice(0, 80),
    raw: body.slice(0, 6000),
  };
})()
"""


@mcp.tool()
def read_account_favorites_from_browser(
    profile_id: str = "ben-voiture",
    limit: int = 50,
    import_snapshot: bool = False,
    browser_mode: str = "comet",
) -> dict:
    """Read visible Leboncoin favorites from Comet by default, or the external browser if requested."""
    browser_mode, mode_error = _normalize_browser_mode(browser_mode)
    if mode_error:
        return {"ok": False, "error": mode_error, "messages_sent": 0}
    debug_url = _browser_mode_debug_url(browser_mode)
    if not _debug_browser_reachable(debug_url):
        return {
            "ok": False,
            "browser_mode": browser_mode,
            "error": "connected_browser_not_readable",
            "fix": "Ouvrir Leboncoin dans Comet via open_account_login, puis relancer la lecture.",
            "messages_sent": 0,
        }
    result = _cdp_eval(ACCOUNT_FAVORITES_URL, FAVORITES_EXTRACTOR_JS, wait_seconds=3.0, debug_url=debug_url)
    if not result.get("ok"):
        return {**result, "browser_mode": browser_mode, "messages_sent": 0}
    value = result.get("value") or {}
    items = (value.get("items") or [])[: max(1, min(limit, 100))]
    imported = None
    if import_snapshot:
        imported = import_account_favorites_snapshot(
            items=items,
            source_name=f"{browser_mode}_visible_favorites",
            profile_id=profile_id,
        )
    return {
        "ok": True,
        "browser_mode": browser_mode,
        "page_title": _redact_text(value.get("page_title") or "", 300),
        "page_url": _redact_text(value.get("page_url") or "", 1200),
        "visible_count": len(items),
        "favorites": _sanitize_for_storage(items, 1200),
        "import": imported,
        "safety": {
            "cookies_or_passwords_stored": False,
            "messages_sent": 0,
        },
    }


@mcp.tool()
def read_account_favorites(
    profile_id: str = "ben-voiture",
    limit: int = 50,
    import_snapshot: bool = False,
    browser_mode: str = "comet",
) -> dict:
    """Read visible account favorites; import is explicit and disabled by default."""
    return read_account_favorites_from_browser(
        profile_id=profile_id,
        limit=limit,
        import_snapshot=import_snapshot,
        browser_mode=browser_mode,
    )


@mcp.tool()
def read_account_conversations_from_browser(
    limit: int = 50,
    import_snapshot: bool = False,
    browser_mode: str = "comet",
) -> dict:
    """Read visible Leboncoin conversations from Comet by default, or the external browser if requested."""
    browser_mode, mode_error = _normalize_browser_mode(browser_mode)
    if mode_error:
        return {"ok": False, "error": mode_error, "messages_sent": 0}
    debug_url = _browser_mode_debug_url(browser_mode)
    if not _debug_browser_reachable(debug_url):
        return {
            "ok": False,
            "browser_mode": browser_mode,
            "error": "connected_browser_not_readable",
            "fix": "Ouvrir Leboncoin dans Comet via open_account_login, puis relancer la lecture.",
            "messages_sent": 0,
        }
    result = _cdp_eval(ACCOUNT_MESSAGES_URL, CONVERSATIONS_EXTRACTOR_JS, wait_seconds=3.5, debug_url=debug_url)
    if not result.get("ok"):
        return {**result, "browser_mode": browser_mode, "messages_sent": 0}
    value = result.get("value") or {}
    conversations = (value.get("items") or [])[: max(1, min(limit, 100))]
    imported = None
    if import_snapshot:
        imported = import_account_conversations_snapshot(
            conversations=conversations,
            source_name=f"{browser_mode}_visible_conversations",
            max_messages_per_conversation=8,
        )
    return {
        "ok": True,
        "browser_mode": browser_mode,
        "page_title": _redact_text(value.get("page_title") or "", 300),
        "page_url": _redact_text(value.get("page_url") or "", 1200),
        "visible_count": len(conversations),
        "conversations": _sanitize_for_storage(conversations, 1400),
        "import": imported,
        "safety": {
            "cookies_or_passwords_stored": False,
            "messages_sent": 0,
        },
    }


@mcp.tool()
def read_account_conversations(
    limit: int = 50,
    import_snapshot: bool = False,
    browser_mode: str = "comet",
) -> dict:
    """Read visible account conversations; import is explicit and disabled by default."""
    return read_account_conversations_from_browser(
        limit=limit,
        import_snapshot=import_snapshot,
        browser_mode=browser_mode,
    )


@mcp.tool()
def read_account_conversation_detail_from_browser(
    conversation_url: str,
    import_snapshot: bool = False,
    browser_mode: str = "comet",
) -> dict:
    """Read one full visible Leboncoin conversation detail from Comet by default."""
    browser_mode, mode_error = _normalize_browser_mode(browser_mode)
    if mode_error:
        return {"ok": False, "error": mode_error, "messages_sent": 0}
    debug_url = _browser_mode_debug_url(browser_mode)
    safe_url, url_error = _require_leboncoin_message_url(conversation_url)
    if url_error:
        return {
            "ok": False,
            "browser_mode": browser_mode,
            "error": "invalid_conversation_url",
            "messages_sent": 0,
        }
    if not _debug_browser_reachable(debug_url):
        return {
            "ok": False,
            "browser_mode": browser_mode,
            "error": "connected_browser_not_readable",
            "messages_sent": 0,
        }
    result = _cdp_eval(safe_url, CONVERSATION_DETAIL_EXTRACTOR_JS, wait_seconds=3.5, debug_url=debug_url)
    if not result.get("ok"):
        return {**result, "browser_mode": browser_mode, "messages_sent": 0}
    value = result.get("value") or {}
    lines = value.get("lines") or []
    conversation = {
        "conversation_id": safe_url,
        "url": safe_url,
        "ad_title": value.get("title"),
        "title": value.get("title"),
        "price": value.get("price_line"),
        "messages": [{"index": idx, "text": line} for idx, line in enumerate(lines[:80])],
        "source_page_title": value.get("page_title"),
    }
    imported = None
    if import_snapshot:
        imported = import_account_conversations_snapshot(
            conversations=[conversation],
            source_name=f"{browser_mode}_visible_conversation_detail",
            max_messages_per_conversation=20,
        )
    return {
        "ok": True,
        "browser_mode": browser_mode,
        "conversation": _sanitize_for_storage(conversation, 1800),
        "import": imported,
        "safety": {
            "cookies_or_passwords_stored": False,
            "messages_sent": 0,
        },
    }


@mcp.tool()
def read_account_conversation(
    conversation_url: str,
    import_snapshot: bool = False,
    browser_mode: str = "comet",
) -> dict:
    """Read one visible account conversation after strict Leboncoin URL validation."""
    return read_account_conversation_detail_from_browser(
        conversation_url=conversation_url,
        import_snapshot=import_snapshot,
        browser_mode=browser_mode,
    )


@mcp.tool()
def read_account_conversation_details_from_browser(
    limit: int = 10,
    import_snapshot: bool = False,
    browser_mode: str = "comet",
    accept_inbox_only: bool = False,
) -> dict:
    """Read visible Leboncoin conversation list, then fetch each detail sequentially.

    FIX #5: If the debuggable Comet/external browser is not reachable, returns an
    actionable error hint pointing at open_account_login(force_reopen=True). When
    accept_inbox_only=True, the tool returns the inbox listing alone instead of
    erroring out — useful when no debuggable browser tab is available.
    """
    listing = read_account_conversations_from_browser(
        limit=max(1, min(limit, 30)),
        import_snapshot=False,
        browser_mode=browser_mode,
    )
    if not listing.get("ok"):
        # FIX #5: give a clearer next-step when the debuggable tab is missing
        err = listing.get("error")
        if err in {"no_leboncoin_debuggable_tab", "connected_browser_not_readable"}:
            hint = (
                "No debuggable Leboncoin tab found. Run open_account_login(force_reopen=True) "
                "to relaunch the agent browser, or pass accept_inbox_only=True to skip details."
            )
            return {
                **listing,
                "error_hint": hint,
                "suggested_action": "open_account_login(force_reopen=True)",
            }
        return listing
    details = []
    errors = []
    inbox = listing.get("conversations", []) or []
    if accept_inbox_only:
        return {
            "ok": True,
            "browser_mode": browser_mode,
            "input_visible_count": listing.get("visible_count"),
            "detail_count": 0,
            "details": [],
            "inbox": inbox[: max(1, min(limit, 30))],
            "accept_inbox_only": True,
            "errors": [],
            "safety": {
                "cookies_or_passwords_stored": False,
                "messages_sent": 0,
                "sequential_read": True,
                "inbox_only_fallback": True,
            },
        }
    for item in inbox[: max(1, min(limit, 30))]:
        url = item.get("url") or item.get("conversation_id")
        if not url:
            continue
        detail = read_account_conversation_detail_from_browser(
            conversation_url=url,
            import_snapshot=import_snapshot,
            browser_mode=browser_mode,
        )
        if detail.get("ok"):
            details.append(detail.get("conversation"))
        else:
            errors.append({"url": _redact_text(url, 1200), "error": detail.get("error")})
    return {
        "ok": True,
        "browser_mode": browser_mode,
        "input_visible_count": listing.get("visible_count"),
        "detail_count": len(details),
        "details": details,
        "errors": errors,
        "safety": {
            "cookies_or_passwords_stored": False,
            "messages_sent": 0,
            "sequential_read": True,
        },
    }


@mcp.tool()
def import_account_favorites_snapshot(
    items: list[dict | str],
    source_name: str = "browser_snapshot",
    profile_id: str = "ben-voiture",
) -> dict:
    """Import account favorites extracted from the browser, without storing cookies or tokens."""
    existing = _read_state("account_favorites.json", {"items": []})
    by_id = {
        str(item.get("ad_id") or item.get("url"))
        for item in existing.get("items", [])
        if _is_snapshot_active(item)
    }
    imported = []
    ignored = []
    for raw in items:
        if isinstance(raw, str):
            clean = {"url": _redact_text(raw) if raw.startswith("http") else None}
        else:
            clean = _drop_raw_snapshot_fields(_sanitize_for_storage(raw))
        if clean.get("url"):
            safe_url, url_error = _require_leboncoin_url(str(clean["url"]), ("/",))
            if url_error:
                ignored.append({"item": {"url": _redact_text(clean["url"], 1200)}, "reason": url_error})
                continue
            clean["url"] = safe_url
        ad_id = _extract_ad_id(str(clean.get("ad_id") or clean.get("url") or ""))
        clean["profile_id"] = profile_id
        clean["ad_id"] = ad_id
        clean["source"] = source_name
        clean["imported_at"] = _now_iso()
        clean["expires_at"] = _future_iso(ACCOUNT_SNAPSHOT_TTL_DAYS)
        identifier = ad_id or clean.get("url")
        key = str(identifier).strip() if identifier is not None else ""
        if not key or key in by_id:
            ignored.append({"item": clean, "reason": "duplicate_or_missing_identifier"})
            continue
        by_id.add(key)
        imported.append(clean)
    existing["items"] = [item for item in existing.get("items", []) if _is_snapshot_active(item)] + imported
    existing["updated_at"] = _now_iso()
    _write_state("account_favorites.json", existing)
    return {
        "ok": True,
        "profile_id": profile_id,
        "imported_count": len(imported),
        "ignored_count": len(ignored),
        "imported": imported,
        "ignored": ignored[:10],
        "safety": {
            "cookies_or_passwords_stored": False,
            "stores_snapshot_only": True,
        },
    }


@mcp.tool()
def list_account_favorites(profile_id: str = "ben-voiture", include_scores: bool = True, limit: int = 30) -> dict:
    """List account favorites imported from a safe browser snapshot."""
    profile = get_car_search_profile(profile_id)["profile"]
    state = _read_state("account_favorites.json", {"items": []})
    results = []
    errors = []
    source_items = [
        item for item in state.get("items", [])
        if item.get("profile_id") == profile_id and _is_snapshot_active(item)
    ]
    for item in source_items[: max(1, min(limit, 100))]:
        entry = _sanitize_for_storage(item)
        ad_id = str(entry.get("ad_id") or "")
        if include_scores and ad_id:
            try:
                ad = get_ad(ad_id)
                entry["ad"] = {
                    "id": ad.get("id"),
                    "title": ad.get("title"),
                    "url": ad.get("url"),
                    "city": (ad.get("location") or {}).get("city"),
                }
                entry["score"] = _score_car(ad, profile)
                entry["draft_message"] = _seller_message(ad, entry["score"])
            except Exception as exc:
                errors.append({"ad_id": ad_id, "error": _redact_text(f"{type(exc).__name__}: {exc}", 400)})
        results.append(entry)
    return {
        "profile_id": profile_id,
        "count": len(results),
        "favorites": results,
        "errors": errors,
        "safety": {
            "messages_sent": 0,
            "cookies_or_passwords_stored": False,
        },
    }


@mcp.tool()
def import_account_conversations_snapshot(
    conversations: list[dict],
    source_name: str = "browser_snapshot",
    max_messages_per_conversation: int = 12,
) -> dict:
    """Import Leboncoin conversations extracted from the browser, sanitized and local-only."""
    state = _read_state("account_conversations.json", {"items": []})
    by_id = {
        str(item.get("conversation_id")): item
        for item in state.get("items", [])
        if _is_snapshot_active(item)
    }
    imported = []
    ignored = []
    for raw in conversations:
        clean = _drop_raw_snapshot_fields(_sanitize_for_storage(raw, 2000))
        clean["messages"] = _conversation_messages(clean, max_messages_per_conversation)
        candidate_url = clean.get("conversation_id") or clean.get("url")
        parsed_candidate = urlparse(str(candidate_url or ""))
        if candidate_url and parsed_candidate.scheme in {"http", "https"}:
            safe_url, url_error = _require_leboncoin_message_url(str(candidate_url))
            if url_error:
                ignored.append({"conversation_id": _redact_text(candidate_url, 300), "reason": url_error})
                continue
            clean["conversation_id"] = safe_url
            clean["url"] = safe_url
        else:
            clean["conversation_id"] = _conversation_id(clean)
        clean["ad_id"] = clean.get("ad_id") or _extract_ad_id(str(clean.get("ad_url") or clean.get("url") or ""))
        clean["source"] = source_name
        clean["imported_at"] = _now_iso()
        clean["expires_at"] = _future_iso(ACCOUNT_SNAPSHOT_TTL_DAYS)
        by_id[clean["conversation_id"]] = clean
        imported.append(_conversation_summary(clean, include_messages=False))
    state["items"] = list(by_id.values())
    state["updated_at"] = _now_iso()
    _write_state("account_conversations.json", state)
    return {
        "ok": True,
        "imported_count": len(imported),
        "ignored_count": len(ignored),
        "total_conversations": len(state["items"]),
        "conversations": imported,
        "ignored": ignored[:10],
        "safety": {
            "cookies_or_passwords_stored": False,
            "messages_sent": 0,
            "snapshot_sanitized": True,
        },
    }


@mcp.tool()
def list_account_conversations(include_messages: bool = False, limit: int = 50) -> dict:
    """List sanitized Leboncoin conversations imported from the browser."""
    state = _read_state("account_conversations.json", {"items": []})
    conversations = [
        _conversation_summary(item, include_messages=include_messages)
        for item in [item for item in state.get("items", []) if _is_snapshot_active(item)][: max(1, min(limit, 100))]
    ]
    return {
        "count": len(conversations),
        "conversations": conversations,
        "safety": {
            "private_snapshot": True,
            "cookies_or_passwords_stored": False,
            "messages_sent": 0,
        },
    }


@mcp.tool()
def get_account_conversation(conversation_id: str) -> dict:
    """Read one sanitized imported conversation."""
    state = _read_state("account_conversations.json", {"items": []})
    for item in state.get("items", []):
        if str(item.get("conversation_id")) == str(conversation_id) and _is_snapshot_active(item):
            return {
                "ok": True,
                "conversation": _conversation_summary(item, include_messages=True),
                "analysis": _conversation_action(item),
            }
    return {"ok": False, "error": "conversation_not_found"}


@mcp.tool()
def analyze_account_conversations(limit: int = 50) -> dict:
    """Return what Ben should do next for each imported Leboncoin conversation."""
    state = _read_state("account_conversations.json", {"items": []})
    actions = [
        _conversation_action(item)
        for item in [item for item in state.get("items", []) if _is_snapshot_active(item)][: max(1, min(limit, 100))]
    ]
    counts = {}
    for action in actions:
        counts[action["decision"]] = counts.get(action["decision"], 0) + 1
    return {
        "count": len(actions),
        "decision_counts": counts,
        "actions": actions,
        "safety": {
            "messages_sent": 0,
            "requires_ben_go_before_send": True,
        },
    }


@mcp.tool()
def prepare_reply_draft(
    message: str,
    conversation_id: Optional[str] = None,
    ad_id: Optional[str] = None,
    ad_url: Optional[str] = None,
    seller_id: Optional[str] = None,
) -> dict:
    """Create a message draft that cannot be sent until Ben explicitly validates it."""
    clean_message = _redact_text(message, 1200)
    target = {
        "conversation_id": _redact_text(conversation_id, 240) if conversation_id else None,
        "ad_id": _redact_text(ad_id or _extract_ad_id(ad_url or ""), 80),
        "ad_url": _redact_text(ad_url, 1200) if ad_url else None,
        "seller_id": _redact_text(seller_id, 240) if seller_id else None,
    }
    dedupe_key = _message_dedupe_key(target, clean_message)
    journal = _read_message_journal()
    if _has_blocking_message_entry(journal, dedupe_key):
        return {
            "ok": False,
            "status": "blocked_duplicate",
            "reason": "message_identique_deja_prepare_ou_traite",
            "messages_sent": 0,
        }
    message_hash = _message_hash(clean_message)
    draft_id = "draft-" + hashlib.sha256(f"{dedupe_key}|{_now_iso()}".encode("utf-8")).hexdigest()[:16]
    entry = _append_message_journal({
        "draft_id": draft_id,
        "status": "awaiting_approval",
        "target": target,
        "message_hash": message_hash,
        "dedupe_key": dedupe_key,
        "message_preview": clean_message,
        "validator": "Ben",
        "messages_sent": 0,
    })
    return {
        "ok": True,
        "draft_id": draft_id,
        "status": "awaiting_approval",
        "message": clean_message,
        "message_hash": message_hash,
        "target": target,
        "journal_entry": entry,
        "requires_exact_ben_go": True,
        "approval_hint": f"go {draft_id} {message_hash}",
        "messages_sent": 0,
    }


@mcp.tool()
def send_message(
    draft_id: str,
    approval_text: str,
    dry_run: bool = True,
) -> dict:
    """Approve a prepared message after explicit Ben validation. Real send is disabled in V1."""
    journal = _read_message_journal()
    draft = next((
        entry for entry in reversed(journal)
        if entry.get("draft_id") == draft_id and entry.get("status") == "awaiting_approval"
    ), None)
    if not draft:
        return {"ok": False, "status": "blocked", "reason": "draft_not_found", "messages_sent": 0}
    if any(
        entry.get("draft_id") == draft_id
        and entry.get("status") in {"approved_dry_run", "manual_required", "sent", "manual_sent"}
        for entry in journal
    ):
        return {
            "ok": False,
            "status": "blocked_duplicate",
            "reason": "draft_deja_valide_ou_traite",
            "messages_sent": 0,
        }
    if not _contains_explicit_go(approval_text):
        _append_message_journal({
            "draft_id": draft_id,
            "status": "blocked",
            "reason": "explicit_ben_go_required",
            "dedupe_key": draft.get("dedupe_key"),
            "messages_sent": 0,
        })
        return {
            "ok": False,
            "status": "blocked",
            "reason": "explicit_ben_go_required",
            "accepted_go_phrases": sorted(GO_PHRASES),
            "messages_sent": 0,
        }
    if not _approval_confirms_draft(approval_text, draft):
        return {
            "ok": False,
            "status": "blocked",
            "reason": "explicit_draft_confirmation_required",
            "required_confirmation": f"go {draft_id} {draft.get('message_hash')}",
            "messages_sent": 0,
        }
    if _has_blocking_message_entry(journal, draft.get("dedupe_key"), draft_id=draft_id):
        return {"ok": False, "status": "blocked_duplicate", "messages_sent": 0}
    if dry_run:
        entry = _append_message_journal({
            "draft_id": draft_id,
            "status": "approved_dry_run",
            "dedupe_key": draft.get("dedupe_key"),
            "approval_evidence": _redact_text(approval_text, 120),
            "messages_sent": 0,
        })
        return {
            "ok": True,
            "status": "approved_dry_run",
            "journal_entry": entry,
            "messages_sent": 0,
            "note": "Validation acceptée, mais aucun message réel envoyé en dry_run.",
        }
    entry = _append_message_journal({
        "draft_id": draft_id,
        "status": "manual_required",
        "reason": "real_leboncoin_sender_not_configured",
        "dedupe_key": draft.get("dedupe_key"),
        "approval_evidence": _redact_text(approval_text, 120),
        "messages_sent": 0,
    })
    return {
        "ok": False,
        "status": "manual_required",
        "reason": "real_leboncoin_sender_not_configured",
        "journal_entry": entry,
        "messages_sent": 0,
        "next_step": "Utiliser le navigateur connecté pour envoyer manuellement ou brancher une passerelle navigateur dédiée.",
    }


@mcp.tool()
def approve_reply_draft(
    draft_id: str,
    approval_text: str,
    dry_run: bool = True,
) -> dict:
    """Approve one prepared Leboncoin draft; this never sends a real message in V1."""
    return send_message(draft_id=draft_id, approval_text=approval_text, dry_run=dry_run)


@mcp.tool()
def get_ad(ad_id: str) -> dict:
    """Get detailed information about a specific Leboncoin ad.

    Args:
        ad_id: The Leboncoin ad ID (numeric string from the ad URL).
    """
    ad = _client.get_ad(ad_id)
    result = _ad_to_dict(ad)
    result["favorites"] = ad.favorites
    return result


@mcp.tool()
def get_user(user_id: str) -> dict:
    """Get information about a Leboncoin user/seller.

    Args:
        user_id: The Leboncoin user ID (UUID format).
    """
    user = _client.get_user(user_id)
    return _user_to_dict(user)


@mcp.tool()
def get_seller(user_id: str) -> dict:
    """Get information about a Leboncoin seller. Alias of get_user with clearer naming."""
    return get_user(user_id=user_id)


@mcp.tool()
def list_categories() -> dict:
    """List all available Leboncoin categories and their names."""
    return {item.name: item.value for item in lbc.Category}


@mcp.tool()
def list_regions() -> list[str]:
    """List all available French regions for location filtering."""
    return [item.name for item in lbc.Region]


@mcp.tool()
def list_departments() -> list[str]:
    """List all available French departments for location filtering."""
    return [item.name for item in lbc.Department]


@mcp.tool()
def fetch_autoviza_report(autoviza_url: str) -> dict:
    """FIX #3: Fetch a public Autoviza vehicle-history report and surface key facts.

    Only URLs on https://autoviza.fr/ are accepted (origin allowlist). Cookies and
    auth tokens are not stored. If parsing fails, returns the raw HTML truncated
    to 5000 chars with manual_review_needed=True so a human can validate.
    """
    safe = _redact_text(autoviza_url, 1200)
    parsed = urlparse(safe)
    if parsed.scheme != "https" or parsed.netloc != "autoviza.fr":
        return {"ok": False, "error": "invalid_autoviza_origin"}
    try:
        import httpx  # local import keeps optional dep contained
    except ImportError:
        return {"ok": False, "error": "httpx_not_available"}
    try:
        resp = httpx.get(safe, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return {
            "ok": False,
            "error": "autoviza_fetch_failed",
            "detail": _redact_text(f"{type(exc).__name__}: {exc}", 300),
        }
    html = resp.text or ""
    truncated = html[:5000]

    def _find_first(patterns: list[str]) -> Optional[str]:
        for pat in patterns:
            match = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return None

    declared_km = _find_first([
        r"kilom[ée]trage[^<\d]{0,40}(\d[\d\s.,]{2,})",
        r"mileage[^<\d]{0,40}(\d[\d\s.,]{2,})",
    ])
    owners = _find_first([
        r"propri[ée]taires?[^<\d]{0,40}(\d+)",
        r"owners?[^<\d]{0,40}(\d+)",
    ])
    sinistres = _find_first([
        r"sinistres?[^<\d]{0,40}(\d+|aucun|non)",
        r"accidents?[^<\d]{0,40}(\d+|none|no)",
    ])
    admin = _find_first([
        r"situation\s+admin(?:istrative)?[^<]{0,80}(libre|non\s*gag[ée]e?|gag[ée]e?|opposition|vol[ée]?)",
    ])

    structured = {
        "declared_km": declared_km,
        "owners_count_text": owners,
        "owners_count": int(re.search(r"\d+", owners).group(0)) if owners and re.search(r"\d+", owners) else None,
        "sinistres": sinistres,
        "admin_status": admin,
    }
    has_any = any(v is not None for v in structured.values())
    return {
        "ok": True,
        "url": safe,
        "parsed": structured,
        "manual_review_needed": not has_any,
        "raw_html_truncated": truncated if not has_any else None,
        "safety": {
            "cookies_or_passwords_stored": False,
            "origin_allowlisted": True,
        },
    }


if __name__ == "__main__":
    import sys

    if "--sse" in sys.argv:
        port = 3001
        for arg in sys.argv:
            if arg.startswith("--port="):
                port = int(arg.split("=", 1)[1])
        mcp.run(transport="sse", host="127.0.0.1", port=port)
    else:
        mcp.run()
