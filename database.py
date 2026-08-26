import os
import time
import httpx
from datetime import datetime

import streamlit as st
from supabase import create_client


@st.cache_resource
def get_supabase():
    url = (
        st.secrets.get("SUPABASE_URL")
        or st.secrets.get("supabase_url")
        or os.getenv("SUPABASE_URL")
    )
    key = (
        st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
        or st.secrets.get("SUPABASE_KEY")
        or st.secrets.get("supabase_key")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
    )
    if not url or not key:
        raise RuntimeError("Supabase credentials are unavailable.")
    return create_client(url, key)



def _execute_with_retry(builder, attempts=4, base_delay=0.35):
    """Retry only transient network/transport failures."""
    last_error = None
    for attempt in range(attempts):
        try:
            return builder.execute()
        except (
            httpx.ReadError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteError,
            httpx.RemoteProtocolError,
        ) as error:
            last_error = error
            if attempt == attempts - 1:
                break
            time.sleep(base_delay * (2 ** attempt))
    raise last_error



def get_master_sets():
    sb = get_supabase()
    q = (
        sb.table("master_sets")
        .select("id,sport,year,manufacturer,set_name,subset_name,total_cards,notes")
        .order("year")
        .order("set_name")
    )
    response = _execute_with_retry(q)
    return response.data or []


def get_master_cards(master_set_id):
    sb = get_supabase()
    q = (
        sb.table("master_cards")
        .select("id,master_set_id,card_number,player_name,team_name,card_title,rookie,variation,is_checklist,reference_image_url,reference_back_image_url")
        .eq("master_set_id", int(master_set_id))
        .order("id")
    )
    response = _execute_with_retry(q)
    return response.data or []


def get_collection_copies(master_set_id=None):
    sb = get_supabase()
    q = sb.table("collection_copies").select("*")
    if master_set_id is not None:
        ids = [r["id"] for r in get_master_cards(master_set_id)]
        if not ids:
            return []
        q = q.in_("master_card_id", ids)
    q = q.order("master_card_id").order("copy_number")
    response = _execute_with_retry(q)
    return response.data or []

def get_copy_counts(master_set_id):
    cards = get_master_cards(master_set_id)
    card_ids = {int(c["id"]) for c in cards}
    rows = get_collection_copies(master_set_id)
    counts = {card_id: 0 for card_id in card_ids}
    for row in rows:
        mid = int(row["master_card_id"])
        counts[mid] = counts.get(mid, 0) + 1
    return counts


def add_copy(master_card_id, condition_label=None, grading_company="RAW", grade=None, notes=None):
    sb = get_supabase()
    existing = (
        sb.table("collection_copies")
        .select("copy_number")
        .eq("master_card_id", int(master_card_id))
        .order("copy_number", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    copy_number = int(existing[0]["copy_number"]) + 1 if existing else 1
    payload = {
        "master_card_id": int(master_card_id),
        "copy_number": copy_number,
        "condition_label": condition_label,
        "grading_company": grading_company or "RAW",
        "grade": grade,
        "notes": notes,
        "updated_at": datetime.utcnow().isoformat(),
    }
    return sb.table("collection_copies").insert(payload).execute().data


def remove_one_copy(master_card_id):
    sb = get_supabase()
    rows = (
        sb.table("collection_copies")
        .select("id,copy_number")
        .eq("master_card_id", int(master_card_id))
        .order("copy_number", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return False
    sb.table("collection_copies").delete().eq("id", rows[0]["id"]).execute()
    return True


def set_copy_quantity(master_card_id, target_qty):
    target_qty = max(0, int(target_qty))
    sb = get_supabase()
    rows = (
        sb.table("collection_copies")
        .select("id,copy_number")
        .eq("master_card_id", int(master_card_id))
        .order("copy_number")
        .execute()
        .data
        or []
    )
    current = len(rows)
    if target_qty > current:
        for _ in range(target_qty - current):
            add_copy(master_card_id)
    elif target_qty < current:
        ids_to_delete = [r["id"] for r in rows[target_qty:]]
        if ids_to_delete:
            sb.table("collection_copies").delete().in_("id", ids_to_delete).execute()
    return target_qty



def get_card_values(master_card_id):
    sb = get_supabase()

    if isinstance(master_card_id, (list, tuple, set)):
        ids = []
        for value in master_card_id:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        if not ids:
            return []
        q = (
            sb.table("card_values")
            .select("*")
            .in_("master_card_id", ids)
            .order("master_card_id")
        )
        response = _execute_with_retry(q)
        return response.data or []

    try:
        card_id = int(master_card_id)
    except (TypeError, ValueError):
        return []

    q = (
        sb.table("card_values")
        .select("*")
        .eq("master_card_id", card_id)
        .order("estimated_value")
    )
    response = _execute_with_retry(q)
    return response.data or []

def add_manual_master_card(master_set_id, card_number, player_name, variation=None, image_url=None):
    sb = get_supabase()
    payload = {
        "master_set_id": int(master_set_id),
        "card_number": str(card_number).strip(),
        "player_name": str(player_name).strip(),
        "variation": variation or None,
        "reference_image_url": image_url or None,
    }
    return sb.table("master_cards").insert(payload).execute().data


def upload_copy_image(collection_copy_id, uploaded_file, image_type="front"):
    sb = get_supabase()
    raw = uploaded_file.getvalue()
    ext = os.path.splitext(getattr(uploaded_file, "name", "card.jpg"))[1] or ".jpg"
    path = f"collector/{collection_copy_id}/{image_type}{ext.lower()}"
    content_type = getattr(uploaded_file, "type", None) or "image/jpeg"
    sb.storage.from_("card-images").upload(path, raw, {"content-type": content_type, "upsert": "true"})
    public_url = sb.storage.from_("card-images").get_public_url(path)
    sb.table("card_images").insert({
        "collection_copy_id": int(collection_copy_id),
        "image_type": image_type,
        "image_url": public_url,
        "source": "user_upload",
        "is_primary": image_type == "front",
    }).execute()
    return public_url


def get_collection_export_rows():
    sb = get_supabase()
    try:
        return sb.table("collection_master_export").select("*").execute().data or []
    except Exception:
        return []


def search_master_cards(query=None, sport=None, year=None, manufacturer=None, set_name=None, limit=100):
    sb = get_supabase()
    q = sb.table("master_cards").select(
        "id,master_set_id,card_number,player_name,team_name,card_title,rookie,variation,is_checklist,reference_image_url,master_sets!inner(id,sport,year,manufacturer,set_name,subset_name,total_cards)"
    )
    if sport:
        q = q.eq("master_sets.sport", sport)
    if year:
        q = q.eq("master_sets.year", int(year))
    if manufacturer:
        q = q.eq("master_sets.manufacturer", manufacturer)
    if set_name:
        q = q.eq("master_sets.set_name", set_name)

    text = str(query or "").strip()
    if text:
        safe = text.replace(",", " ").strip()
        q = q.or_(
            f"player_name.ilike.%{safe}%,card_title.ilike.%{safe}%,card_number.ilike.%{safe}%"
        )

    rows = q.limit(int(limit)).execute().data or []
    return rows


def get_set_facets():
    sets = get_master_sets()
    return {
        "sports": sorted({str(s.get("sport") or "").strip() for s in sets if s.get("sport")}),
        "years": sorted({int(s["year"]) for s in sets if s.get("year") is not None}, reverse=True),
        "manufacturers": sorted({str(s.get("manufacturer") or "").strip() for s in sets if s.get("manufacturer")}),
    }



def find_master_set(sport, year, manufacturer, set_name):
    supabase = get_supabase()
    query = (
        supabase.table("master_sets")
        .select("*")
        .eq("year", int(year))
        .eq("set_name", str(set_name))
    )
    if sport:
        query = query.eq("sport", str(sport))
    if manufacturer:
        query = query.eq("manufacturer", str(manufacturer))
    response = query.limit(1).execute()
    return response.data[0] if response.data else None


def create_master_set_if_needed(sport, year, manufacturer, set_name):
    existing = find_master_set(sport, year, manufacturer, set_name)
    if existing:
        return existing

    payload = {
        "sport": sport or "Unknown",
        "year": int(year),
        "manufacturer": manufacturer or "Unknown",
        "set_name": set_name,
        "total_cards": None,
        "image_source": "external_discovery",
        "checklist_source": "external_discovery",
        "notes": "Created automatically from Smart Add external lookup.",
    }
    response = get_supabase().table("master_sets").insert(payload).execute()
    return response.data[0]


def create_master_card_from_external(candidate):
    master_set = create_master_set_if_needed(
        candidate.get("sport"),
        candidate.get("year"),
        candidate.get("manufacturer"),
        candidate.get("set_name"),
    )
    supabase = get_supabase()

    existing = (
        supabase.table("master_cards")
        .select("*")
        .eq("master_set_id", master_set["id"])
        .eq("card_number", str(candidate.get("card_number")))
        .eq("player_name", str(candidate.get("player_name")))
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    payload = {
        "master_set_id": master_set["id"],
        "card_number": str(candidate.get("card_number") or "").strip(),
        "player_name": str(candidate.get("player_name") or "").strip(),
        "rookie": bool(candidate.get("rookie", False)),
        "variation": candidate.get("variation"),
        "reference_image_url": candidate.get("image_url"),
        "source_url": candidate.get("source_url"),
    }
    response = supabase.table("master_cards").insert(payload).execute()
    return response.data[0]


def delete_collection_copy(copy_id):
    sb = get_supabase()
    return sb.table("collection_copies").delete().eq("id", int(copy_id)).execute()


def delete_all_copies_for_master_card(master_card_id):
    sb = get_supabase()
    return (
        sb.table("collection_copies")
        .delete()
        .eq("master_card_id", int(master_card_id))
        .execute()
    )


def upsert_master_cards_from_external(master_set_id, candidates):
    """
    Cache externally discovered card metadata into Shoebox.
    Existing cards are updated rather than duplicated.
    """
    sb = get_supabase()
    master_set_id = int(master_set_id)
    affected = 0

    for c in candidates:
        card_number = str(c.get("card_number") or "").strip()
        if not card_number:
            continue

        payload = {
            "master_set_id": master_set_id,
            "card_number": card_number,
            "player_name": c.get("player_name") or None,
            "rookie": bool(c.get("rookie", False)),
            "variation": c.get("variation") or None,
            "reference_image_url": c.get("image_url") or None,
            "source_url": c.get("source_url") or None,
        }

        existing = (
            sb.table("master_cards")
            .select("id,reference_image_url,player_name,rookie,variation")
            .eq("master_set_id", master_set_id)
            .eq("card_number", card_number)
            .limit(1)
            .execute()
            .data
            or []
        )

        if existing:
            update = {
                "player_name": payload["player_name"] or existing[0].get("player_name"),
                "rookie": payload["rookie"] or bool(existing[0].get("rookie")),
                "variation": payload["variation"] or existing[0].get("variation"),
                "reference_image_url": (
                    payload["reference_image_url"]
                    or existing[0].get("reference_image_url")
                ),
                "source_url": payload["source_url"],
                "updated_at": datetime.utcnow().isoformat(),
            }
            sb.table("master_cards").update(update).eq("id", existing[0]["id"]).execute()
        else:
            payload["updated_at"] = datetime.utcnow().isoformat()
            sb.table("master_cards").insert(payload).execute()
        affected += 1

    return affected


def save_card_value_estimates(master_card_id, estimates):
    sb = get_supabase()
    master_card_id = int(master_card_id)

    # Replace only our generated market estimates; preserve manual/future sources.
    sb.table("card_values").delete().eq(
        "master_card_id", master_card_id
    ).eq("source", "The Card API / eBay sold").execute()

    rows = []
    for item in estimates:
        rows.append({
            "master_card_id": master_card_id,
            "value_basis": item.get("value_basis"),
            "condition_label": item.get("condition_label"),
            "grading_company": (
                item.get("grading_company")
                or ("RAW" if item.get("condition_label") else "RAW")
            ),
            "grade": item.get("grade"),
            "estimated_value": item.get("estimated_value"),
            "low_value": item.get("low_value"),
            "high_value": item.get("high_value"),
            "comp_count": item.get("comp_count"),
            "source": item.get("source"),
            "as_of_date": item.get("as_of_date"),
        })

    if rows:
        sb.table("card_values").insert(rows).execute()
    return len(rows)



def get_card_values_for_set(master_set_id):
    cards = get_master_cards(master_set_id)
    ids = [int(c["id"]) for c in cards]
    if not ids:
        return []
    sb = get_supabase()
    q = (
        sb.table("card_values")
        .select("*")
        .in_("master_card_id", ids)
    )
    response = _execute_with_retry(q)
    return response.data or []

def get_card_values(master_card_id):
    """
    Return cached valuation rows for either one master card ID
    or a list/tuple/set of master card IDs.
    """
    sb = get_supabase()

    if isinstance(master_card_id, (list, tuple, set)):
        ids = []
        for value in master_card_id:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue

        if not ids:
            return []

        return (
            sb.table("card_values")
            .select("*")
            .in_("master_card_id", ids)
            .order("master_card_id")
            .execute()
            .data
            or []
        )

    try:
        card_id = int(master_card_id)
    except (TypeError, ValueError):
        return []

    return (
        sb.table("card_values")
        .select("*")
        .eq("master_card_id", card_id)
        .order("estimated_value")
        .execute()
        .data
        or []
    )


def get_latest_value_date(master_card_id):
    rows = (
        get_supabase()
        .table("card_values")
        .select("as_of_date")
        .eq("master_card_id", int(master_card_id))
        .order("as_of_date", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0].get("as_of_date") if rows else None


def get_primary_user_images_for_set(master_set_id):
    """
    Return {master_card_id: image_url} using the user's uploaded front photo.
    If multiple copies/photos exist, the newest primary/front image wins.
    """
    sb = get_supabase()
    cards = get_master_cards(master_set_id)
    card_ids = [int(c["id"]) for c in cards]
    if not card_ids:
        return {}

    copies_q = (
        sb.table("collection_copies")
        .select("id,master_card_id")
        .in_("master_card_id", card_ids)
    )
    copies = _execute_with_retry(copies_q).data or []
    if not copies:
        return {}

    copy_to_card = {
        int(row["id"]): int(row["master_card_id"])
        for row in copies
    }
    copy_ids = list(copy_to_card.keys())

    images_q = (
        sb.table("card_images")
        .select("id,collection_copy_id,image_type,image_url,is_primary,created_at")
        .in_("collection_copy_id", copy_ids)
        .order("created_at", desc=True)
    )
    images = _execute_with_retry(images_q).data or []

    result = {}
    for img in images:
        copy_id = int(img["collection_copy_id"])
        master_card_id = copy_to_card.get(copy_id)
        if master_card_id is None or master_card_id in result:
            continue
        # Prefer front/primary user images.
        if img.get("image_type") == "front" or img.get("is_primary"):
            result[master_card_id] = img.get("image_url")

    return result
