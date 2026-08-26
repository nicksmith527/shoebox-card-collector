from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


THE_CARD_API_BASE = "https://www.thecardapi.com/api/v1/catalog"
THE_CARD_API_SETS = "https://www.thecardapi.com/api/v1/catalog/sets"


def _normalize(text):
    return re.sub(r"\s+", " ", str(text or "").strip())


def _api_key():
    # Supports the new standalone name plus likely legacy names from Shoebox BI.
    return (
        os.getenv("THE_CARD_API_KEY", "").strip()
        or os.getenv("CARD_API_KEY", "").strip()
        or os.getenv("MARKET_API_KEY", "").strip()
    )


def parse_card_query(query: str) -> dict:
    """
    Extract useful hints from natural searches such as:
      1993 Topps Derek Jeter 98
      1985 Topps Clemens 181
      2018 Topps Chrome Ohtani 150
    """
    text = _normalize(query)
    year = None
    card_number = None

    m = re.search(r"\b(?:19|20)\d{2}\b", text)
    if m:
        year = int(m.group(0))

    tokens = text.split()
    for token in reversed(tokens):
        cleaned = token.lstrip("#").strip(",")
        if str(year) == cleaned:
            continue
        if re.fullmatch(r"[A-Za-z]{0,6}\d{1,5}[A-Za-z0-9-]*", cleaned):
            card_number = cleaned
            break

    return {"raw_query": text, "year": year, "card_number": card_number}


def _get_json(url: str) -> dict:
    key = _api_key()
    if not key:
        raise RuntimeError(
            "The Card API key is not configured. Add THE_CARD_API_KEY "
            "to your environment or Streamlit secrets."
        )

    request = urllib.request.Request(
        url,
        headers={
            "x-api-key": key,
            "Accept": "application/json",
            "User-Agent": "Shoebox-Card-Collector/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""

        # Try to turn provider JSON errors into a concise message.
        provider_message = body
        if body:
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    provider_message = (
                        parsed.get("message")
                        or parsed.get("error")
                        or parsed.get("detail")
                        or body
                    )
            except Exception:
                pass

        if error.code == 403:
            raise RuntimeError(
                "The Card API returned 403 Forbidden. "
                "Your API key may be valid, but Catalog API access is currently "
                "limited to eligible paid plans/add-ons. "
                f"Provider response: {provider_message or 'No response body returned.'}"
            ) from error

        if error.code == 401:
            raise RuntimeError(
                "The Card API returned 401 Unauthorized. "
                "Check that THE_CARD_API_KEY is current and entered correctly. "
                f"Provider response: {provider_message or 'No response body returned.'}"
            ) from error

        if error.code == 429:
            raise RuntimeError(
                "The Card API rate limit or daily catalog allowance has been reached. "
                f"Provider response: {provider_message or 'No response body returned.'}"
            ) from error

        raise RuntimeError(
            f"The Card API returned HTTP {error.code}. "
            f"Provider response: {provider_message or 'No response body returned.'}"
        ) from error

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not reach The Card API: {getattr(error, 'reason', error)}"
        ) from error


def _first(row: dict, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _normalize_card(row: dict) -> dict:
    # The Card API catalog has evolved; be tolerant to old/new response aliases.
    set_obj = row.get("set") if isinstance(row.get("set"), dict) else {}
    player_obj = row.get("player") if isinstance(row.get("player"), dict) else {}

    year = _first(row, "year", "set_year") or _first(set_obj, "year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None

    set_name = (
        _first(row, "set_name", "setName")
        or _first(set_obj, "set_name", "name", "setName")
    )

    player_name = (
        _first(row, "player_name", "subject_name", "name", "subject")
        or _first(player_obj, "name", "player_name", "full_name")
    )

    sport = (
        _first(row, "subcategory", "sport")
        or _first(set_obj, "subcategory", "sport")
    )
    if isinstance(sport, str):
        sport = sport.replace("_", " ").title()

    manufacturer = (
        _first(row, "manufacturer", "brand")
        or _first(set_obj, "manufacturer", "brand")
    )

    image_url = _first(
        row,
        "image_url",
        "image",
        "front_image_url",
        "imageUrl",
    )

    source_id = _first(row, "ucid", "id", "card_id")
    source_url = _first(row, "url", "source_url")
    if not source_url and source_id:
        source_url = f"https://www.thecardapi.com/catalog/{source_id}"

    rookie_value = _first(row, "is_rookie", "rookie")
    rookie = bool(rookie_value)

    return {
        "sport": sport,
        "year": year,
        "manufacturer": manufacturer,
        "set_name": set_name,
        "card_number": str(_first(row, "card_number", "number", "cardNumber") or "").lstrip("#"),
        "player_name": player_name,
        "rookie": rookie,
        "variation": _first(row, "variation", "parallel", "parallel_name"),
        "image_url": image_url,
        "source_url": source_url,
        "external_id": source_id,
        "external_set_id": _first(row, "set_id", "usid") or _first(set_obj, "usid", "id"),
    }


def search_external_card_catalog(query: str, limit: int = 8) -> list[dict]:
    """
    Live sports-card fallback using The Card API.

    Documentation:
      GET https://www.thecardapi.com/api/v1/catalog
      filters include q, year, card_number, set_name, sport, set_id, etc.

    We send q plus any useful parsed hints, then normalize the response to
    Shoebox's provider-neutral card candidate structure.
    """
    query = _normalize(query)
    if not query:
        return []

    hints = parse_card_query(query)
    params = {"q": query, "limit": max(1, min(int(limit), 25))}

    # Extra filters improve precision when confidently present.
    if hints.get("year"):
        params["year"] = hints["year"]
    if hints.get("card_number"):
        params["card_number"] = hints["card_number"]

    url = f"{THE_CARD_API_BASE}?{urllib.parse.urlencode(params)}"
    payload = _get_json(url)

    rows = payload.get("data") or payload.get("results") or []
    if not isinstance(rows, list):
        return []

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        card = _normalize_card(row)
        if card["player_name"] or card["set_name"]:
            candidates.append(card)

    return candidates[:limit]


def provider_status() -> dict:
    return {
        "provider": "The Card API",
        "configured": bool(_api_key()),
        "base_url": THE_CARD_API_BASE,
    }


def test_provider_connection() -> dict:
    """
    Verify the configured key against a tiny catalog query.
    Uses a known broad sports-card query and requests only one record.
    """
    params = urllib.parse.urlencode({
        "q": "Topps",
        "limit": 1,
    })
    url = f"{THE_CARD_API_BASE}?{params}"
    payload = _get_json(url)
    rows = payload.get("data") or []
    pagination = payload.get("pagination") or {}
    return {
        "ok": isinstance(rows, list),
        "records_returned": len(rows) if isinstance(rows, list) else 0,
        "catalog_total": pagination.get("total"),
    }


def find_external_base_set(year: int, manufacturer: str, sport: str = "Baseball") -> dict | None:
    """
    Find the most likely top-level base set for a local Shoebox master set.

    The Card API recommends /catalog/sets + main_only=true for product-level
    browsing. We query by year + manufacturer and rank exact/simple base sets
    ahead of update/traded/insert products.
    """
    year = int(year)
    manufacturer = _normalize(manufacturer)
    params = {
        "year": year,
        "q": manufacturer,
        "sport": sport,
        "main_only": "true",
        "limit": 100,
        "page": 1,
    }
    payload = _get_json(f"{THE_CARD_API_SETS}?{urllib.parse.urlencode(params)}")
    rows = payload.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None

    maker = manufacturer.casefold()

    def score(row):
        name = str(row.get("set_name") or row.get("name") or "").casefold()
        points = 0
        if maker and maker in name:
            points += 10
        if str(year) in name:
            points += 8
        # Prefer a plain annual base product.
        bad_words = ("update", "traded", "chrome", "heritage", "insert", "parallel",
                     "mini", "tiffany", "glossy", "sticker", "opening day")
        points -= sum(3 for word in bad_words if word in name)
        if name.strip() in {f"{year} {maker}", f"{year} {maker} baseball"}:
            points += 20
        return points

    best = max(rows, key=score)
    return {
        "external_set_id": best.get("usid") or best.get("id"),
        "set_name": best.get("set_name") or best.get("name"),
        "year": best.get("year") or year,
        "sport": best.get("sport") or sport,
        "category": best.get("category"),
        "subcategory": best.get("subcategory"),
        "raw": best,
    }


def fetch_external_set_page(set_id: str, page: int = 1, limit: int = 100) -> dict:
    """
    Fetch one page of cards for a set. Builder plans max at 100 records/request;
    higher tiers may allow more, but 100 is deliberately conservative.
    """
    params = {
        "set_id": set_id,
        "page": max(1, int(page)),
        "limit": max(1, min(int(limit), 100)),
    }
    payload = _get_json(f"{THE_CARD_API_BASE}?{urllib.parse.urlencode(params)}")
    rows = payload.get("data") or []
    cards = [_normalize_card(row) for row in rows if isinstance(row, dict)]
    pagination = payload.get("pagination") or {}
    return {
        "cards": cards,
        "page": int(pagination.get("page") or page),
        "pages": int(pagination.get("pages") or 1),
        "total": int(pagination.get("total") or len(cards)),
        "limit": int(pagination.get("limit") or limit),
    }


def diagnose_catalog_access() -> dict:
    """
    Test the two catalog endpoints Shoebox depends on and return friendly
    diagnostics rather than failing the whole app.
    """
    checks = []

    for label, url in [
        ("Catalog cards", f"{THE_CARD_API_BASE}?q=Topps&limit=1"),
        ("Catalog sets", f"{THE_CARD_API_SETS}?q=Topps&limit=1"),
    ]:
        try:
            payload = _get_json(url)
            rows = payload.get("data") or []
            checks.append({
                "label": label,
                "ok": True,
                "message": f"Connected ({len(rows)} record returned).",
            })
        except Exception as error:
            checks.append({
                "label": label,
                "ok": False,
                "message": str(error),
            })

    return {
        "provider": "The Card API",
        "configured": bool(_api_key()),
        "checks": checks,
        "all_ok": all(item["ok"] for item in checks),
    }
