from __future__ import annotations

import json
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta


MARKET_BASE = "https://thecardapi.com/api/v1/market/sales"


def _key():
    return (
        os.getenv("THE_CARD_API_KEY", "").strip()
        or os.getenv("CARD_API_KEY", "").strip()
        or os.getenv("MARKET_API_KEY", "").strip()
    )


def _fetch_sales(query: str, limit: int = 50) -> list[dict]:
    key = _key()
    if not key:
        raise RuntimeError("THE_CARD_API_KEY is not configured.")

    params = {
        "q": query,
        "limit": max(1, min(int(limit), 100)),
    }
    url = f"{MARKET_BASE}?{urllib.parse.urlencode(params)}"

    last_error = None
    for attempt in range(4):
        req = urllib.request.Request(
            url,
            headers={
                "x-market-api-key": key,
                "Accept": "application/json",
                "User-Agent": "Shoebox-Card-Collector/1.0",
            },
        )

        try:
            # Longer timeout than before; vintage searches can be slower.
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = payload.get("data") or []
            return [r for r in rows if isinstance(r, dict)]

        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace").strip()
            if error.code == 429:
                raise RuntimeError(
                    "Market API daily sales allowance reached."
                ) from error
            if error.code in (401, 403):
                raise RuntimeError(
                    f"Market API authorization failed (HTTP {error.code}). {body}"
                ) from error

            # Retry server-side transient failures.
            if 500 <= error.code < 600 and attempt < 3:
                last_error = error
                time.sleep(0.75 * (2 ** attempt))
                continue

            raise RuntimeError(
                f"Market API HTTP {error.code}. {body}"
            ) from error

        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
        ) as error:
            last_error = error
            if attempt < 3:
                time.sleep(0.75 * (2 ** attempt))
                continue

        except OSError as error:
            # Handles intermittent Windows socket errors.
            last_error = error
            if attempt < 3:
                time.sleep(0.75 * (2 ** attempt))
                continue

    raise RuntimeError(
        f"Market API read timed out after 4 attempts: {last_error}"
    )


def _price(row):
    for key in ("sale_price", "price", "sold_price", "total_price"):
        try:
            value = row.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _title(row):
    return str(row.get("title") or "").lower()


def _clean_prices(rows, *, exclude_terms=(), require_terms=()):
    prices = []
    for row in rows:
        title = _title(row)
        if any(term.lower() in title for term in exclude_terms):
            continue
        if require_terms and not all(term.lower() in title for term in require_terms):
            continue
        p = _price(row)
        if p and p > 0:
            prices.append(p)

    if len(prices) >= 4:
        prices.sort()
        # Trim one extreme at each end for a simple robust estimate.
        prices = prices[1:-1]
    return prices


def _estimate(prices):
    if not prices:
        return None
    return round(float(statistics.median(prices)), 2)


def build_identity_query(card: dict) -> str:
    year = card.get("year")
    manufacturer = card.get("manufacturer") or ""
    set_name = card.get("set_name") or ""
    player = card.get("player_name") or card.get("card_title") or ""
    number = str(card.get("card_number") or "").lstrip("#")

    # Quoted set name improves precision while retaining title flexibility.
    set_piece = f'"{set_name}"' if set_name else manufacturer
    return " ".join(
        part for part in [
            str(year or ""),
            set_piece,
            player,
            f"#{number}" if number else "",
            "-reprint",
            "-(lot,album,complete set)",
        ] if part
    ).strip()


def estimate_card_values(card: dict) -> list[dict]:
    """
    Return condition/grade estimates for one card.

    Modern (1980+):
      Raw, PSA 9, PSA 10

    Vintage (<1980):
      Good, VG, EX, NM, PSA 8, PSA 9, PSA 10

    Estimates are medians of sold-listing prices from title-filtered searches.
    They are intentionally labeled ESTIMATES, not appraisals.
    """
    year = int(card.get("year") or 9999)
    base = build_identity_query(card)
    results = []

    def add(label, query, *, grade=None, condition=None, exclude=(), require=()):
        rows = _fetch_sales(query, limit=50)
        prices = _clean_prices(rows, exclude_terms=exclude, require_terms=require)
        est = _estimate(prices)
        if est is None:
            return
        results.append({
            "value_basis": "market_sales_median",
            "condition_label": condition,
            "grading_company": "PSA" if grade is not None else "RAW",
            "grade": grade,
            "estimated_value": est,
            "low_value": round(min(prices), 2) if prices else None,
            "high_value": round(max(prices), 2) if prices else None,
            "comp_count": len(prices),
            "source": "The Card API / eBay sold",
            "as_of_date": date.today().isoformat(),
            "display_label": label,
        })

    graded_excludes = ("bgs", "sgc", "cgc", "raw", "ungraded")

    if year >= 1980:
        add(
            "Raw",
            base + " -(PSA,BGS,SGC,CGC,graded)",
            condition="RAW",
            exclude=("psa", "bgs", "sgc", "cgc", "graded"),
        )
        add("PSA 9", base + ' "PSA 9"', grade=9, require=("psa 9",))
        add("PSA 10", base + ' "PSA 10"', grade=10, require=("psa 10",))
    else:
        # Raw vintage title-condition estimates.
        vintage_conditions = [
            ("Good", " good ", "GOOD"),
            ("VG", " vg ", "VG"),
            ("EX", " ex ", "EX"),
            ("NM", " nm ", "NM"),
        ]
        for label, term, condition in vintage_conditions:
            add(
                label,
                base + term + " -(PSA,BGS,SGC,CGC,graded)",
                condition=condition,
                exclude=("psa", "bgs", "sgc", "cgc", "graded"),
            )
        add("PSA 8", base + ' "PSA 8"', grade=8, require=("psa 8",))
        add("PSA 9", base + ' "PSA 9"', grade=9, require=("psa 9",))
        add("PSA 10", base + ' "PSA 10"', grade=10, require=("psa 10",))

    return results


def test_market_access() -> dict:
    rows = _fetch_sales("Topps PSA 10", limit=1)
    return {"ok": True, "records_returned": len(rows)}
