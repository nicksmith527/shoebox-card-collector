import os
import io
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from market_values import estimate_card_values, test_market_access

# External sports-card catalog key.
# Streamlit Community Cloud users can set THE_CARD_API_KEY in app Secrets.
try:
    if not os.getenv("THE_CARD_API_KEY") and "THE_CARD_API_KEY" in st.secrets:
        os.environ["THE_CARD_API_KEY"] = str(st.secrets["THE_CARD_API_KEY"])
except Exception:
    pass
from external_catalog import (
    parse_card_query,
    search_external_card_catalog,
    provider_status,
    test_provider_connection,
    find_external_base_set,
    fetch_external_set_page,
    diagnose_catalog_access,
)

from database import (
    get_card_values_for_set,
    save_card_value_estimates,
    upsert_master_cards_from_external,
    delete_collection_copy,
    delete_all_copies_for_master_card,
    create_master_card_from_external,
    add_copy,
    add_manual_master_card,
    get_card_values,
    get_collection_copies,
    get_collection_export_rows,
    get_copy_counts,
    get_master_cards,
    get_master_sets,
    search_master_cards,
    remove_one_copy,
    set_copy_quantity,
    upload_copy_image,
)

st.set_page_config(page_title="Shoebox Card Collector", page_icon="🃏", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 1.4rem; max-width: 1500px;}
[data-testid="stMetricValue"] {font-size: 1.6rem;}
.card-ref {color:#A63A2B;font-weight:800;font-size:.85rem;}
.small-muted {color:#69737D;font-size:.82rem;}
</style>
""", unsafe_allow_html=True)


def set_label(s):
    return f"{s['year']} {s['manufacturer']} {s['set_name']} ({s['total_cards'] or '—'} cards)"


def valuation_map(rows):
    out = {}
    for r in rows:
        mid = int(r["master_card_id"])
        key = (r.get("condition_label") or (f"{r.get('grading_company')} {r.get('grade')}" if r.get("grading_company") else r.get("value_basis")) or "Value")
        out.setdefault(mid, {})[str(key)] = r.get("estimated_value")
    return out


nav = st.sidebar.radio("Shoebox Card Collector", ["Set Builder", "My Collection", "Add / Scan Card", "Master Export"])

sets = get_master_sets()
if not sets:
    st.error("No master sets are loaded yet.")
    st.stop()

if nav == "Set Builder":
    st.title("Set Builder")
    st.caption("Browse a master checklist, quickly mark what you own, and track unlimited duplicate copies.")

    selected_label = st.selectbox("Choose set", [set_label(s) for s in sets])
    selected_set = sets[[set_label(s) for s in sets].index(selected_label)]
    # Load cached valuation state before valuation UI renders.
    try:
        set_value_rows = get_card_values_for_set(selected_set["id"])
    except Exception:
        set_value_rows = []
    values_by_card = {}
    for _v in set_value_rows:
        values_by_card.setdefault(int(_v["master_card_id"]), []).append(_v)
    master_cards = get_master_cards(selected_set["id"])
    counts = get_copy_counts(selected_set["id"])
    owned_unique = sum(1 for c in master_cards if counts.get(int(c["id"]), 0) > 0)
    total_copies = sum(counts.values())
    set_size = int(selected_set.get("total_cards") or len(master_cards) or 0)

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Set size", set_size)
    m2.metric("Catalog loaded", len(master_cards))
    m3.metric("Owned unique", owned_unique)
    m4.metric("Total copies", total_copies)
    if set_size:
        st.progress(min(owned_unique / set_size, 1.0), text=f"{owned_unique}/{set_size} complete ({owned_unique/set_size:.1%})")

    if len(master_cards) < set_size:
        st.info(
            f"This set currently has {len(master_cards):,} enriched catalog records "
            f"of {set_size:,}. Bulk actions affect only the {len(master_cards):,} "
            "catalog records currently loaded."
        )



    with st.expander("💰 Estimated card values", expanded=False):
        st.caption(
            "Values are cached estimates from recent eBay sold listings. "
            "Modern cards use Raw / PSA 9 / PSA 10. Pre-1980 cards use "
            "Good / VG / EX / NM plus PSA 8 / 9 / 10."
        )

        value_search = st.text_input(
            "Find card to value",
            placeholder="e.g. O.J. Simpson or 90",
            key=f"value_search_{selected_set['id']}",
        ).strip().lower()

        candidates_for_value = master_cards
        if value_search:
            candidates_for_value = [
                c for c in master_cards
                if value_search in str(c.get("player_name") or "").lower()
                or value_search in str(c.get("card_number") or "").lower()
            ]

        if candidates_for_value:
            value_options = {
                f"#{c.get('card_number')} — {c.get('player_name') or c.get('card_title') or 'Unknown'}": c
                for c in candidates_for_value[:200]
            }
            selected_value_label = st.selectbox(
                "Card",
                options=list(value_options.keys()),
                key=f"value_card_select_{selected_set['id']}",
            )
            selected_value_card = value_options[selected_value_label]

            existing_values = values_by_card.get(int(selected_value_card["id"]), [])
            if existing_values:
                cols = st.columns(min(4, len(existing_values)))
                for i, val in enumerate(existing_values):
                    label = (
                        f"{val.get('grading_company')} {val.get('grade'):g}"
                        if val.get("grading_company") and val.get("grade") is not None
                        else val.get("condition_label") or "Estimate"
                    )
                    cols[i % len(cols)].metric(
                        label,
                        f"${float(val.get('estimated_value') or 0):,.2f}",
                        help=(
                            f"{val.get('comp_count') or 0} comps • "
                            f"as of {val.get('as_of_date') or '—'}"
                        ),
                    )

            confirm_value = st.checkbox(
                "Use market API calls to refresh this card's estimates",
                key=f"confirm_value_{selected_value_card['id']}",
            )
            if st.button(
                "Refresh estimates",
                key=f"refresh_value_{selected_value_card['id']}",
                width="stretch",
            ):
                if not confirm_value:
                    st.warning("Check the confirmation box first.")
                else:
                    enriched_card = {
                        **selected_value_card,
                        "year": selected_set.get("year"),
                        "manufacturer": selected_set.get("manufacturer"),
                        "set_name": selected_set.get("set_name"),
                    }
                    try:
                        with st.spinner("Searching recent sold comps…"):
                            estimates = estimate_card_values(enriched_card)
                            saved = save_card_value_estimates(
                                selected_value_card["id"], estimates
                            )
                        if saved:
                            st.success(f"Saved {saved} value estimates.")
                            st.rerun()
                        else:
                            st.warning(
                                "No clean comps were found in the current market lookback."
                            )
                    except Exception as error:
                        st.error(f"Value refresh failed: {error}")
        else:
            st.info("No matching catalog card found.")



    with st.expander("⚡ Batch value refresh", expanded=False):
        st.caption(
            "Refresh several cards at once while protecting the market-API allowance. "
            "Shoebox skips cards that already have fresh cached values unless you choose otherwise."
        )

        fresh_days = st.number_input(
            "Treat cached values as fresh for",
            min_value=1,
            max_value=30,
            value=7,
            step=1,
            help="Cards valued within this many days will be skipped by default.",
            key=f"fresh_days_{selected_set['id']}",
        )

        refresh_scope = st.radio(
            "Cards to refresh",
            ["Owned cards in this set", "Visible/search results", "Selected cards"],
            horizontal=True,
            key=f"value_scope_{selected_set['id']}",
        )

        force_refresh = st.checkbox(
            "Refresh even if cached values are still fresh",
            key=f"force_value_refresh_{selected_set['id']}",
        )

        # Build candidates according to selected scope.
        if refresh_scope == "Owned cards in this set":
            batch_candidates = [
                c for c in master_cards if counts.get(int(c["id"]), 0) > 0
            ]
        elif refresh_scope == "Visible/search results":
            batch_candidates = display_cards
        else:
            selected_ids = set()
            try:
                if "Select" in edited.columns:
                    selected_ids = {
                        int(v) for v in edited.loc[edited["Select"] == True, "master_card_id"].tolist()
                    }
            except Exception:
                selected_ids = set()
            batch_candidates = [
                c for c in master_cards if int(c["id"]) in selected_ids
            ]

        stale_candidates = []
        skipped_fresh = 0
        today = date.today()

        # Use the valuation rows already loaded for this set.
        # This avoids one Supabase request per card.
        latest_value_date_by_card = {}
        for _row in set_value_rows:
            try:
                _mid = int(_row.get("master_card_id"))
            except (TypeError, ValueError):
                continue
            _as_of = _row.get("as_of_date")
            if not _as_of:
                continue
            try:
                _date = date.fromisoformat(str(_as_of))
            except Exception:
                continue
            current = latest_value_date_by_card.get(_mid)
            if current is None or _date > current:
                latest_value_date_by_card[_mid] = _date

        for c in batch_candidates:
            latest_date = latest_value_date_by_card.get(int(c["id"]))
            is_fresh = (
                latest_date is not None
                and (today - latest_date).days < int(fresh_days)
            )

            if is_fresh and not force_refresh:
                skipped_fresh += 1
            else:
                stale_candidates.append(c)

        st.write(
            f"**{len(batch_candidates)}** candidate cards • "
            f"**{len(stale_candidates)}** will query the market API • "
            f"**{skipped_fresh}** skipped as fresh"
        )

        # Rough request-budget estimate.
        # Modern card: 3 searches; vintage: 7 searches.
        estimated_searches = 0
        for c in stale_candidates:
            estimated_searches += 7 if int(selected_set.get("year") or 9999) < 1980 else 3

        st.caption(
            f"Estimated market searches: ~{estimated_searches:,}. "
            "Actual returned-sales usage depends on how many comps each search returns."
        )

        max_batch = st.number_input(
            "Maximum cards this run",
            min_value=1,
            max_value=100,
            value=min(10, max(1, len(stale_candidates) or 1)),
            step=1,
            key=f"max_value_batch_{selected_set['id']}",
        )

        confirm_batch_values = st.checkbox(
            f"Yes, refresh up to {int(max_batch)} card values now",
            key=f"confirm_batch_values_{selected_set['id']}",
        )

        if st.button(
            "Run batch value refresh",
            key=f"run_batch_values_{selected_set['id']}",
            width="stretch",
        ):
            if not stale_candidates:
                st.info("Nothing needs refreshing.")
            elif not confirm_batch_values:
                st.warning("Check the confirmation box first.")
            else:
                queue = stale_candidates[: int(max_batch)]
                progress = st.progress(0)
                status = st.empty()
                completed = 0
                failed = 0

                for idx, c in enumerate(queue, start=1):
                    player = c.get("player_name") or c.get("card_title") or "Unknown"
                    status.write(
                        f"Valuing #{c.get('card_number')} {player} "
                        f"({idx}/{len(queue)})"
                    )

                    enriched = {
                        **c,
                        "year": selected_set.get("year"),
                        "manufacturer": selected_set.get("manufacturer"),
                        "set_name": selected_set.get("set_name"),
                    }

                    try:
                        estimates = estimate_card_values(enriched)
                        save_card_value_estimates(c["id"], estimates)
                        completed += 1
                    except Exception as error:
                        failed += 1
                        st.warning(
                            f"Skipped #{c.get('card_number')} {player}: {error}"
                        )

                    progress.progress(idx / len(queue))

                status.empty()
                st.success(
                    f"Batch complete: {completed} refreshed, {failed} failed/skipped."
                )
                st.rerun()


    with st.expander("📚 Catalog enrichment / reference photos", expanded=False):
        st.caption(
            "Shoebox's paid Catalog API integration is disabled by default. "
            "Your current Card API key can still be used for free market-pricing calls."
        )

        paid_catalog_enabled = (
            os.getenv("ENABLE_PAID_CARD_CATALOG", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if not paid_catalog_enabled:
            st.info(
                "Catalog hydration is OFF to prevent repeated 403 errors. "
                "Known sets will be loaded into Shoebox's own master catalog instead."
            )
            st.caption(
                "If you later upgrade The Card API and want to re-enable this feature, "
                'add ENABLE_PAID_CARD_CATALOG = "true" to Streamlit Secrets.'
            )
        else:
            provider = provider_status()
            if not provider.get("configured"):
                st.warning("Add THE_CARD_API_KEY to Streamlit Secrets first.")
            else:
                page_key = f"hydrate_page_{selected_set['id']}"
                if page_key not in st.session_state:
                    st.session_state[page_key] = 1

                h1, h2, h3 = st.columns([1, 1, 2])
                load_page = h1.number_input(
                    "Catalog page",
                    min_value=1,
                    value=int(st.session_state[page_key]),
                    step=1,
                    key=f"hydrate_page_input_{selected_set['id']}",
                )

                if h2.button(
                    "Load 100 cards",
                    key=f"hydrate_100_{selected_set['id']}",
                    width="stretch",
                ):
                    try:
                        external_set = find_external_base_set(
                            selected_set["year"],
                            selected_set["manufacturer"],
                            selected_set["sport"],
                        )
                        if not external_set or not external_set.get("external_set_id"):
                            st.error("Could not confidently locate this base set.")
                        else:
                            with st.spinner(
                                f"Loading page {int(load_page)} of {external_set['set_name']}…"
                            ):
                                page_data = fetch_external_set_page(
                                    external_set["external_set_id"],
                                    page=int(load_page),
                                    limit=100,
                                )
                                affected = upsert_master_cards_from_external(
                                    selected_set["id"],
                                    page_data["cards"],
                                )
                            st.session_state[page_key] = int(load_page) + 1
                            st.success(
                                f"Cached {affected} cards. "
                                f"Provider page {page_data['page']} of {page_data['pages']}."
                            )
                            st.rerun()
                    except Exception as error:
                        st.error(f"Catalog load failed: {error}")

                missing_images = sum(
                    1 for c in master_cards if not c.get("reference_image_url")
                )
                h3.metric("Reference photos still missing", missing_images)

    search = st.text_input("Search player or card number")
    rookie_only = st.checkbox("Rookies only")

    display_cards = master_cards
    if search:
        q = search.casefold().strip()
        display_cards = [c for c in display_cards if q in str(c.get("player_name") or "").casefold() or q in str(c.get("card_number") or "").casefold()]
    if rookie_only:
        display_cards = [c for c in display_cards if c.get("rookie")]

    tab_visual, tab_bulk = st.tabs(["Visual", "Bulk Entry"])

    with tab_visual:
        values = valuation_map(get_card_values([c["id"] for c in display_cards]))
        cols = st.columns(4)
        for pos, c in enumerate(display_cards):
            mid = int(c["id"])
            qty = counts.get(mid, 0)
            with cols[pos % 4]:
                with st.container(border=True):
                    if c.get("reference_image_url"):
                        st.image(c["reference_image_url"], width="stretch")
                    else:
                        st.caption("📷 Reference image pending")
                    st.markdown(f"<div class='card-ref'>#{c.get('card_number')}</div>", unsafe_allow_html=True)
                    st.markdown(f"**{c.get('player_name') or c.get('card_title') or 'Unknown card'}**")
                    bits = []
                    if c.get("rookie"): bits.append("RC")
                    if c.get("variation"): bits.append(c["variation"])
                    if bits: st.caption(" • ".join(bits))

                    q1,q2,q3 = st.columns([1,1.3,1])
                    if q1.button("−", key=f"minus_{mid}", disabled=qty <= 0, width="stretch"):
                        remove_one_copy(mid); st.rerun()
                    q2.markdown(f"<div style='text-align:center;font-size:20px;font-weight:800;padding-top:4px'>{qty}</div>", unsafe_allow_html=True)
                    if q3.button("+", key=f"plus_{mid}", width="stretch"):
                        add_copy(mid); st.rerun()

                    vm = values.get(mid, {})
                    if vm:
                        preferred = ["GOOD","VG","EX","NM","RAW","PSA 8","PSA 9","PSA 10"]
                        items = []
                        for k in preferred:
                            for actual,val in vm.items():
                                if actual.upper() == k and val is not None:
                                    items.append(f"{actual}: ${float(val):,.0f}")
                        if not items:
                            items = [f"{k}: ${float(v):,.0f}" for k,v in list(vm.items())[:3] if v is not None]
                        if items: st.caption(" | ".join(items[:4]))

    with tab_bulk:
        df = pd.DataFrame([{
            "Select": False,
            "Card #": c.get("card_number"),
            "Player": c.get("player_name") or c.get("card_title"),
            "RC": bool(c.get("rookie")),
            "Variation": c.get("variation") or "",
            "Current Qty": counts.get(int(c["id"]), 0),
            "Target Qty": counts.get(int(c["id"]), 0),
            "master_card_id": int(c["id"]),
        } for c in display_cards])
        edited = st.data_editor(
            df,
            hide_index=True,
            width="stretch",
            disabled=["Card #","Player","RC","Variation","Current Qty","master_card_id"],
            column_config={"master_card_id": None},
            num_rows="fixed",
        )
        st.caption("Bulk shortcuts")
        b1,b2,b3,b4,b5 = st.columns(5)

        mass_confirm = st.checkbox(
            f"Confirm bulk changes for {selected_label}",
            key=f"confirm_bulk_{selected_set['id']}",
            help="Required before actions that change the entire set."
        )

        if b1.button("Mark entire set owned", width="stretch"):
            if not mass_confirm:
                st.warning("Check the confirmation box before changing the entire set.")
            else:
                with st.spinner(f"Adding {len(master_cards):,} cards to your collection…"):
                    for c in master_cards:
                        mid = int(c["id"])
                        set_copy_quantity(mid, max(1, counts.get(mid, 0)))
                st.success(f"{len(master_cards):,} catalog cards marked owned.")
                st.rerun()

        if b2.button("Mark visible owned", width="stretch"):
            if len(display_cards) > 25 and not mass_confirm:
                st.warning("Check the confirmation box before changing more than 25 cards at once.")
            else:
                with st.spinner(f"Adding {len(display_cards):,} visible cards…"):
                    for c in display_cards:
                        mid = int(c["id"])
                        set_copy_quantity(mid, max(1, counts.get(mid, 0)))
                st.rerun()

        if b3.button("Mark selected owned", width="stretch"):
            chosen = edited[edited["Select"] == True]
            if chosen.empty:
                st.warning("Check one or more rows in the Select column first.")
            else:
                for _,r in chosen.iterrows():
                    set_copy_quantity(
                        int(r["master_card_id"]),
                        max(1, int(r["Current Qty"]))
                    )
                st.rerun()

        if b4.button("Set selected target qty", width="stretch"):
            chosen = edited[edited["Select"] == True]
            if chosen.empty:
                st.warning("Check one or more rows in the Select column first.")
            else:
                for _,r in chosen.iterrows():
                    set_copy_quantity(
                        int(r["master_card_id"]),
                        int(r["Target Qty"])
                    )
                st.rerun()

        if b5.button("Mark selected need", width="stretch"):
            chosen = edited[edited["Select"] == True]
            if chosen.empty:
                st.warning("Check one or more rows in the Select column first.")
            elif not mass_confirm:
                st.warning("Check the confirmation box before removing selected cards from your collection.")
            else:
                for _,r in chosen.iterrows():
                    set_copy_quantity(int(r["master_card_id"]), 0)
                st.rerun()

elif nav == "My Collection":
    st.title("My Collection")
    rows = get_collection_export_rows()
    if not rows:
        st.info("No copy-level collection rows yet. Use Set Builder or Add / Scan Card to start adding cards.")
    else:
        df = pd.DataFrame(rows)
        st.metric("Physical copies", len(df))
        st.dataframe(df, hide_index=True, width="stretch")

elif nav == "Add / Scan Card":
    st.title("Smart Add")
    st.caption("Photo first. Search the catalog second. Create a new catalog record only when the card is not already known.")

    if "recent_set_ids" not in st.session_state:
        st.session_state["recent_set_ids"] = []

    photo_tab, search_tab, manual_tab = st.tabs(["📷 Take / Upload Photo", "🔎 Search Card", "✍️ Manual Fallback"])

    with photo_tab:
        st.subheader("1. Add the card image")
        c1, c2 = st.columns(2)
        with c1:
            camera = st.camera_input("Take a photo", key="smart_camera")
        with c2:
            upload = st.file_uploader("Upload a photo", type=["jpg","jpeg","png","webp"], key="smart_upload")
        image = camera or upload
        if image is not None:
            st.success("Photo captured. Now identify the card below; the image will attach to the copy you add.")
            st.image(image, width=320)
        else:
            st.info("You can still search and add without a photo. Photo recognition can be layered onto this same workflow later.")

        st.subheader("2. Identify the card")
        recent_sets = [s for s in sets if int(s["id"]) in st.session_state["recent_set_ids"]]
        if recent_sets:
            st.caption("Recent sets")
            rcols = st.columns(min(4, len(recent_sets)))
            for i,rs in enumerate(recent_sets[:4]):
                if rcols[i].button(f"{rs['year']} {rs['manufacturer']} {rs['set_name']}", key=f"recent_{rs['id']}", width="stretch"):
                    st.session_state["smart_set_id"] = int(rs["id"])

        q1,q2,q3 = st.columns([1.2,1,1.6])
        sport_options = ["All"] + sorted({str(x.get("sport") or "") for x in sets if x.get("sport")})
        year_options = ["All"] + [str(y) for y in sorted({int(x["year"]) for x in sets if x.get("year") is not None}, reverse=True)]
        sport = q1.selectbox("Sport", sport_options, key="smart_sport")
        year = q2.selectbox("Year", year_options, key="smart_year")
        query = q3.text_input("Player, card #, or keywords", placeholder="e.g. Clemens 181, Mattingly, #83")

        filtered_sets = [s for s in sets if (sport == "All" or s.get("sport") == sport) and (year == "All" or int(s.get("year")) == int(year))]
        set_choices = ["Any loaded set"] + [set_label(s) for s in filtered_sets]
        default_index = 0
        remembered = st.session_state.get("smart_set_id")
        if remembered:
            for idx,sr in enumerate(filtered_sets, start=1):
                if int(sr["id"]) == int(remembered):
                    default_index = idx
                    break
        chosen_set_label = st.selectbox("Set (optional but recommended)", set_choices, index=default_index, key="smart_set")
        chosen_set = None if chosen_set_label == "Any loaded set" else filtered_sets[set_choices.index(chosen_set_label)-1]

        matches = []
        if query.strip() or chosen_set is not None:
            matches = search_master_cards(
                query=query.strip() or None,
                sport=None if sport == "All" else sport,
                year=None if year == "All" else int(year),
                manufacturer=chosen_set.get("manufacturer") if chosen_set else None,
                set_name=chosen_set.get("set_name") if chosen_set else None,
                limit=50,
            )

        if matches:
            labels=[]
            for m in matches:
                ms=m.get("master_sets") or {{}}
                who=m.get("player_name") or m.get("card_title") or "Unknown"
                flags=[]
                if m.get("rookie"): flags.append("RC")
                if m.get("variation"): flags.append(str(m.get("variation")))
                suffix=f" • {' • '.join(flags)}" if flags else ""
                labels.append(f"{ms.get('year')} {ms.get('manufacturer')} {ms.get('set_name')} #{m.get('card_number')} — {who}{suffix}")
            selection = st.selectbox("Best catalog match", labels, key="smart_match")
            match = matches[labels.index(selection)]
            ms=match.get("master_sets") or {{}}
            if match.get("reference_image_url"):
                st.image(match["reference_image_url"], width=220)
            st.caption(f"Catalog ID {match['id']} • {ms.get('sport')} • {ms.get('year')} {ms.get('manufacturer')} {ms.get('set_name')}")

            d1,d2,d3 = st.columns(3)
            condition = d1.selectbox("Condition", ["", "GOOD", "VG", "EX", "EX-MT", "NM", "NM-MT"], key="smart_condition")
            grading_company = d2.selectbox("Grading", ["RAW", "PSA", "SGC", "CGC", "BGS"], key="smart_grader")
            grade = d3.number_input("Grade", min_value=0.0, max_value=10.0, step=0.5, value=0.0, disabled=grading_company == "RAW", key="smart_grade")

            if st.button("Add matched card", type="primary", width="stretch", key="smart_add_match"):
                result = add_copy(int(match["id"]), condition_label=condition or None, grading_company=grading_company, grade=(grade if grading_company != "RAW" and grade > 0 else None))
                copy_id = int(result[0]["id"])
                if image is not None:
                    try:
                        upload_copy_image(copy_id, image, "front")
                    except Exception as exc:
                        st.warning(f"Card saved, but image upload failed: {exc}")
                sid=int(match["master_set_id"])
                st.session_state["recent_set_ids"] = [sid] + [x for x in st.session_state["recent_set_ids"] if x != sid]
                st.session_state["recent_set_ids"] = st.session_state["recent_set_ids"][:4]
                st.success("Card added.")
                st.rerun()
        elif query.strip() or chosen_set is not None:
            st.warning("No catalog match found. Use the Manual Fallback tab to create the card once, then future copies can use normal search.")

    with search_tab:
        st.subheader("Fast catalog search")
        search_text = st.text_input("Search across loaded catalog", placeholder="1985 Topps Clemens, Sandberg 83, O.J. Simpson 90", key="global_card_search")
        sport_filter = st.selectbox("Sport filter", ["All"] + sorted({str(x.get("sport") or "") for x in sets if x.get("sport")}), key="global_sport")
        results = search_master_cards(query=search_text or None, sport=None if sport_filter == "All" else sport_filter, limit=100) if search_text.strip() else []
        if search_text.strip() and not results:
            st.info("No exact catalog records found yet.")
        for r in results[:25]:
            ms=r.get("master_sets") or {{}}
            with st.container(border=True):
                a,b,c = st.columns([1,4,1])
                if r.get("reference_image_url"):
                    a.image(r["reference_image_url"], width=110)
                else:
                    a.caption("📷")
                b.markdown(f"**{r.get('player_name') or r.get('card_title') or 'Unknown'}**")
                b.caption(f"{ms.get('year')} {ms.get('manufacturer')} {ms.get('set_name')} • #{r.get('card_number')}{' • RC' if r.get('rookie') else ''}")
                if c.button("+ Add", key=f"search_add_{r['id']}", width="stretch"):
                    add_copy(int(r["id"]))
                    sid=int(r["master_set_id"])
                    st.session_state["recent_set_ids"] = [sid] + [x for x in st.session_state["recent_set_ids"] if x != sid]
                    st.session_state["recent_set_ids"] = st.session_state["recent_set_ids"][:4]
                    st.success("Added.")
                    st.rerun()

    with manual_tab:
        st.subheader("Create a missing catalog card")
        st.caption("Use this only when Shoebox cannot find the card in the catalog. Once created, it becomes searchable for future copies.")
        selected_label = st.selectbox("Set", [set_label(s) for s in sets], key="manual_set")
        selected_set = sets[[set_label(s) for s in sets].index(selected_label)]
        m1,m2 = st.columns(2)
        card_number = m1.text_input("Card number", key="manual_num")
        player_name = m2.text_input("Player / card title", key="manual_player")
        variation = st.text_input("Variation / parallel (optional)", key="manual_var")
        manual_image = st.file_uploader("Optional front image", type=["jpg","jpeg","png","webp"], key="manual_image")
        if st.button("Create catalog card + add copy", type="primary", key="manual_create"):
            if not card_number.strip() or not player_name.strip():
                st.error("Card number and player/title are required.")
            else:
                created = add_manual_master_card(selected_set["id"], card_number, player_name, variation=variation)
                mid = int(created[0]["id"])
                result = add_copy(mid)
                copy_id = int(result[0]["id"])
                if manual_image is not None:
                    try:
                        upload_copy_image(copy_id, manual_image, "front")
                    except Exception as exc:
                        st.warning(f"Card saved, but image upload failed: {exc}")
                sid=int(selected_set["id"])
                st.session_state["recent_set_ids"] = [sid] + [x for x in st.session_state["recent_set_ids"] if x != sid]
                st.session_state["recent_set_ids"] = st.session_state["recent_set_ids"][:4]
                st.success("Catalog card created and first copy added.")
                st.rerun()

elif nav == "Master Export":
    st.title("Master Export")
    rows = get_collection_export_rows()
    if not rows:
        st.info("No copy-level rows yet. Your original Shoebox spreadsheet remains available separately during the transition.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True, width="stretch")
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", csv, file_name=f"shoebox_master_collection_{date.today().isoformat()}.csv", mime="text/csv")
