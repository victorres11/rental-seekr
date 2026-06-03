#!/usr/bin/env python3
"""
Private Richmond rental dashboard MVP.

Features:
- Rentcast sync into SQLite
- manual listing intake for links from Zillow/Furnished Finder/Airbnb/etc.
- inbox, shortlist, hidden views
- per-listing notes and status
"""

import html
import json
import sqlite3
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from config import APP_HOST, APP_PORT, DB_FILE, SEARCH_CONFIG
from search import search_all_locations


BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / DB_FILE
VALID_STATUSES = ["new", "maybe", "good", "contacted", "toured", "pass"]
VALID_TRI_STATE = ["unknown", "yes", "maybe", "no"]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT,
            url TEXT NOT NULL,
            title TEXT,
            address TEXT,
            neighborhood TEXT,
            rent INTEGER,
            beds REAL,
            baths REAL,
            sqft INTEGER,
            property_type TEXT,
            image_url TEXT,
            available_date_raw TEXT,
            lease_term_raw TEXT,
            furnished_status TEXT NOT NULL DEFAULT 'unknown',
            utilities_status TEXT NOT NULL DEFAULT 'unknown',
            parking TEXT,
            laundry TEXT,
            score INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            notes TEXT NOT NULL DEFAULT '',
            hidden INTEGER NOT NULL DEFAULT 0,
            manual INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT,
            listed_date TEXT,
            days_on_market INTEGER,
            scraped_at TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_source_external_id "
        "ON listings(source, external_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_source_url "
        "ON listings(source, url)"
    )
    conn.commit()
    conn.close()


def compute_score(record: dict) -> int:
    furnished = (record.get("furnished_status") or "unknown").lower()
    lease = (record.get("lease_term_raw") or "").lower()
    available = (record.get("available_date_raw") or "").lower()
    rent = record.get("rent") or 0
    sqft = record.get("sqft") or 0
    prop = (record.get("property_type") or "").lower()

    score = 0

    furnished_points = {
        "yes": 30,
        "maybe": 18,
        "unknown": 8,
        "no": 0,
    }
    score += furnished_points.get(furnished, 0)

    if available:
        if "aug" in available or "08/" in available or "2026-08" in available:
            score += 25
        elif "jul" in available or "07/" in available or "2026-07" in available:
            score += 18
        else:
            score += 8
    elif record.get("days_on_market") is not None:
        score += 10 if (record.get("days_on_market") or 0) <= 30 else 6
    else:
        score += 6

    if lease:
        if "6" in lease and "month" in lease:
            score += 20
        elif "3" in lease and "month" in lease:
            score += 16
        elif "month-to-month" in lease or "month to month" in lease:
            score += 18
        elif "flex" in lease or "short-term" in lease or "short term" in lease:
            score += 15
        elif "12" in lease and "month" in lease:
            score += 4
        else:
            score += 7
    else:
        score += 7

    quality_points = 0
    if sqft >= 900:
        quality_points += 7
    elif sqft >= 650:
        quality_points += 5
    elif sqft > 0:
        quality_points += 3

    if any(token in prop for token in ["condo", "apartment", "townhouse", "single family"]):
        quality_points += 4
    if record.get("parking"):
        quality_points += 2
    if record.get("laundry"):
        quality_points += 2
    score += min(15, quality_points)

    ideal_price = SEARCH_CONFIG.get("ideal_price", 6500)
    min_price = SEARCH_CONFIG.get("target_min_price", SEARCH_CONFIG.get("min_price", 4000))
    max_price = SEARCH_CONFIG.get("target_max_price", SEARCH_CONFIG.get("max_price", 8000))
    if rent:
        if min_price <= rent <= ideal_price:
            score += 10
        elif ideal_price < rent <= max_price:
            score += 7
        elif rent < min_price:
            score += 5
        else:
            score += 1

    return min(100, score)


def upsert_listing(record: dict) -> None:
    def clean_text(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    normalized = {
        "source": record.get("source", "manual"),
        "external_id": record.get("external_id"),
        "url": clean_text(record.get("url")),
        "title": record.get("title") or record.get("address") or "Untitled listing",
        "address": clean_text(record.get("address")),
        "neighborhood": clean_text(record.get("neighborhood")),
        "rent": int(record["rent"]) if record.get("rent") else None,
        "beds": float(record["beds"]) if record.get("beds") not in (None, "") else None,
        "baths": float(record["baths"]) if record.get("baths") not in (None, "") else None,
        "sqft": int(record["sqft"]) if record.get("sqft") else None,
        "property_type": clean_text(record.get("property_type")),
        "image_url": clean_text(record.get("image_url")),
        "available_date_raw": clean_text(record.get("available_date_raw")),
        "lease_term_raw": clean_text(record.get("lease_term_raw")),
        "furnished_status": (record.get("furnished_status") or "unknown").lower(),
        "utilities_status": (record.get("utilities_status") or "unknown").lower(),
        "parking": clean_text(record.get("parking")),
        "laundry": clean_text(record.get("laundry")),
        "status": (record.get("status") or "new").lower(),
        "notes": clean_text(record.get("notes")),
        "hidden": 1 if record.get("hidden") else 0,
        "manual": 1 if record.get("manual") else 0,
        "last_seen_at": record.get("last_seen_at") or now_iso(),
        "listed_date": record.get("listed_date"),
        "days_on_market": int(record["days_on_market"]) if record.get("days_on_market") not in (None, "") else None,
        "scraped_at": record.get("scraped_at"),
        "raw_json": json.dumps(record.get("raw")) if record.get("raw") is not None else None,
    }
    normalized["score"] = compute_score(normalized)
    normalized["updated_at"] = now_iso()
    normalized["created_at"] = record.get("created_at") or normalized["updated_at"]

    conn = get_db()
    existing = None
    if normalized["external_id"]:
        existing = conn.execute(
            "SELECT id, notes, status, hidden, furnished_status, utilities_status, available_date_raw, lease_term_raw, parking, laundry "
            "FROM listings WHERE source = ? AND external_id = ?",
            (normalized["source"], normalized["external_id"]),
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id, notes, status, hidden, furnished_status, utilities_status, available_date_raw, lease_term_raw, parking, laundry "
            "FROM listings WHERE source = ? AND url = ?",
            (normalized["source"], normalized["url"]),
        ).fetchone()

    if existing:
        # Preserve manual review fields when automated sync runs again.
        for field in [
            "notes",
            "status",
            "hidden",
            "furnished_status",
            "utilities_status",
            "available_date_raw",
            "lease_term_raw",
            "parking",
            "laundry",
        ]:
            if existing[field] not in (None, "", 0):
                normalized[field] = existing[field]
        normalized["score"] = compute_score(normalized)
        conn.execute(
            """
            UPDATE listings SET
                url = :url,
                title = :title,
                address = :address,
                neighborhood = :neighborhood,
                rent = :rent,
                beds = :beds,
                baths = :baths,
                sqft = :sqft,
                property_type = :property_type,
                image_url = :image_url,
                available_date_raw = :available_date_raw,
                lease_term_raw = :lease_term_raw,
                furnished_status = :furnished_status,
                utilities_status = :utilities_status,
                parking = :parking,
                laundry = :laundry,
                score = :score,
                status = :status,
                notes = :notes,
                hidden = :hidden,
                manual = :manual,
                last_seen_at = :last_seen_at,
                listed_date = :listed_date,
                days_on_market = :days_on_market,
                scraped_at = :scraped_at,
                raw_json = :raw_json,
                updated_at = :updated_at
            WHERE id = :id
            """,
            {**normalized, "id": existing["id"]},
        )
    else:
        conn.execute(
            """
            INSERT INTO listings (
                source, external_id, url, title, address, neighborhood, rent, beds, baths, sqft,
                property_type, image_url, available_date_raw, lease_term_raw, furnished_status,
                utilities_status, parking, laundry, score, status, notes, hidden, manual,
                last_seen_at, listed_date, days_on_market, scraped_at, raw_json, created_at, updated_at
            ) VALUES (
                :source, :external_id, :url, :title, :address, :neighborhood, :rent, :beds, :baths, :sqft,
                :property_type, :image_url, :available_date_raw, :lease_term_raw, :furnished_status,
                :utilities_status, :parking, :laundry, :score, :status, :notes, :hidden, :manual,
                :last_seen_at, :listed_date, :days_on_market, :scraped_at, :raw_json, :created_at, :updated_at
            )
            """,
            normalized,
        )
    conn.commit()
    conn.close()


def sync_all_sources() -> dict[str, int]:
    counts: dict[str, int] = {}
    for listing in search_all_locations():
        source = listing.get("source") or "unknown"
        upsert_listing(
            {
                "source": source,
                "external_id": listing.get("external_id") or listing.get("id"),
                "url": listing.get("url"),
                "title": listing.get("title") or listing.get("address"),
                "address": listing.get("address"),
                "rent": listing.get("price"),
                "beds": listing.get("beds"),
                "baths": listing.get("baths"),
                "sqft": listing.get("sqft"),
                "property_type": listing.get("property_type"),
                "image_url": listing.get("image_url"),
                "available_date_raw": listing.get("available_date_raw", ""),
                "lease_term_raw": listing.get("lease_term_raw", ""),
                "furnished_status": listing.get("furnished_status", "unknown"),
                "utilities_status": listing.get("utilities_status", "unknown"),
                "parking": listing.get("parking", ""),
                "laundry": listing.get("laundry", ""),
                "listed_date": listing.get("listed_date"),
                "days_on_market": listing.get("days_on_market"),
                "scraped_at": listing.get("scraped_at"),
                "raw": listing.get("raw"),
                "manual": False,
            }
        )
        counts[source] = counts.get(source, 0) + 1
    return counts


def fetch_listings(view: str) -> list[sqlite3.Row]:
    conn = get_db()
    if view == "shortlist":
        rows = conn.execute(
            "SELECT * FROM listings WHERE hidden = 0 AND status IN ('good', 'contacted', 'toured') "
            "ORDER BY score DESC, updated_at DESC"
        ).fetchall()
    elif view == "hidden":
        rows = conn.execute(
            "SELECT * FROM listings WHERE hidden = 1 OR status = 'pass' ORDER BY updated_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM listings WHERE hidden = 0 AND status NOT IN ('good', 'contacted', 'toured', 'pass') "
            "ORDER BY score DESC, updated_at DESC"
        ).fetchall()
    conn.close()
    return rows


def fetch_listing(listing_id: int) -> Optional[sqlite3.Row]:
    conn = get_db()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    conn.close()
    return row


def update_listing(listing_id: int, form: dict[str, str]) -> None:
    row = fetch_listing(listing_id)
    if not row:
        return
    updated = dict(row)
    updated["status"] = form.get("status", updated["status"]).lower()
    updated["notes"] = form.get("notes", updated["notes"])
    updated["furnished_status"] = form.get("furnished_status", updated["furnished_status"]).lower()
    updated["utilities_status"] = form.get("utilities_status", updated["utilities_status"]).lower()
    updated["available_date_raw"] = form.get("available_date_raw", updated["available_date_raw"])
    updated["lease_term_raw"] = form.get("lease_term_raw", updated["lease_term_raw"])
    updated["parking"] = form.get("parking", updated["parking"])
    updated["laundry"] = form.get("laundry", updated["laundry"])
    updated["hidden"] = form.get("hidden") == "1"
    updated["score"] = compute_score(updated)
    updated["updated_at"] = now_iso()

    conn = get_db()
    conn.execute(
        """
        UPDATE listings
        SET status = ?, notes = ?, furnished_status = ?, utilities_status = ?,
            available_date_raw = ?, lease_term_raw = ?, parking = ?, laundry = ?,
            hidden = ?, score = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            updated["status"],
            updated["notes"],
            updated["furnished_status"],
            updated["utilities_status"],
            updated["available_date_raw"],
            updated["lease_term_raw"],
            updated["parking"],
            updated["laundry"],
            1 if updated["hidden"] else 0,
            updated["score"],
            updated["updated_at"],
            listing_id,
        ),
    )
    conn.commit()
    conn.close()


def render_layout(title: str, body: str, active: str = "inbox") -> bytes:
    def nav_item(name: str, label: str) -> str:
        cls = "nav-link active" if active == name else "nav-link"
        return f'<a class="{cls}" href="/{name}">{label}</a>'

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --ink: #1f2a24;
      --muted: #67766b;
      --card: #fffdf9;
      --line: #d8d0c2;
      --accent: #1f6b57;
      --accent-soft: #d9efe6;
      --warn: #7f4f24;
      --skip: #7a2e2e;
      --shadow: rgba(31, 42, 36, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #fff9ef 0, #f3efe7 45%),
        linear-gradient(180deg, #ede6d6 0%, #f7f3ec 100%);
    }}
    a {{ color: var(--accent); }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 48px; }}
    .hero {{ display:flex; gap:16px; align-items:end; justify-content:space-between; margin-bottom:20px; }}
    .hero h1 {{ margin:0; font-size:2.1rem; }}
    .hero p {{ margin:.35rem 0 0; color: var(--muted); }}
    .nav {{ display:flex; gap:10px; flex-wrap:wrap; margin: 18px 0 24px; }}
    .nav-link {{
      text-decoration:none; padding:10px 14px; border-radius:999px;
      background:#efe8da; color:var(--ink); border:1px solid var(--line);
    }}
    .nav-link.active {{ background:var(--accent); color:white; border-color:var(--accent); }}
    .grid {{ display:grid; grid-template-columns: 1.55fr .95fr; gap:20px; }}
    .stack {{ display:grid; gap:18px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 10px 26px var(--shadow);
    }}
    .listing {{ display:grid; gap:10px; }}
    .listing-head {{ display:flex; justify-content:space-between; gap:12px; align-items:start; }}
    .listing h3 {{ margin:0; font-size:1.15rem; }}
    .meta, .mini {{ color: var(--muted); font-size:.96rem; }}
    .chipbar {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }}
    .chip {{
      padding:6px 10px; border-radius:999px; background:var(--accent-soft);
      border:1px solid #b8d9ca; font-size:.9rem;
    }}
    .score {{ font-weight:bold; color:var(--accent); }}
    form {{ display:grid; gap:10px; }}
    input, select, textarea, button {{
      font: inherit; padding: 10px 12px; border-radius: 12px; border:1px solid var(--line);
      background:white; color:var(--ink);
    }}
    textarea {{ min-height: 110px; resize: vertical; }}
    button {{
      background: var(--accent); color:white; border-color: var(--accent); cursor:pointer;
    }}
    .row {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }}
    .row3 {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:10px; }}
    .note {{ color:var(--muted); font-size:.92rem; }}
    .empty {{ padding: 28px 0; color: var(--muted); text-align:center; }}
    .footer {{ margin-top:22px; color:var(--muted); font-size:.9rem; }}
    @media (max-width: 900px) {{
      .grid, .row, .row3 {{ grid-template-columns: 1fr; }}
      .hero {{ align-items:start; flex-direction:column; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <div>
        <h1>Richmond Rental Finder</h1>
        <p>Private shortlist for furnished-ish Richmond rentals around August 1 with 6-month fit scoring.</p>
      </div>
      <div class="note">Shared MVP • multi-source sync + manual URL intake</div>
    </div>
    <div class="nav">
      {nav_item('inbox', 'Inbox')}
      {nav_item('shortlist', 'Shortlist')}
      {nav_item('hidden', 'Hidden')}
    </div>
    {body}
    <div class="footer">Live sync uses Rentcast and Furnished Finder, plus manual listing intake for everything else.</div>
  </div>
</body>
</html>"""
    return html_doc.encode("utf-8")


def render_listing_card(row: sqlite3.Row) -> str:
    title = html.escape(row["title"] or row["address"] or "Untitled listing")
    address = html.escape(row["address"] or "")
    url = html.escape(row["url"] or "#")
    notes = html.escape((row["notes"] or "")[:180])
    rent = f"${int(row['rent']):,}/mo" if row["rent"] else "No rent yet"
    meta_bits = [rent]
    if row["beds"]:
        meta_bits.append(f"{row['beds']:g} bd")
    if row["baths"]:
        meta_bits.append(f"{row['baths']:g} ba")
    if row["sqft"]:
        meta_bits.append(f"{int(row['sqft']):,} sqft")
    meta = " · ".join(meta_bits)

    chips = [
        f"<span class='chip'>{html.escape(row['source'])}</span>",
        f"<span class='chip'>status: {html.escape(row['status'])}</span>",
        f"<span class='chip'>furnished: {html.escape(row['furnished_status'])}</span>",
    ]
    if row["available_date_raw"]:
        chips.append(f"<span class='chip'>move-in: {html.escape(row['available_date_raw'])}</span>")
    if row["lease_term_raw"]:
        chips.append(f"<span class='chip'>lease: {html.escape(row['lease_term_raw'])}</span>")

    return f"""
    <div class="card listing">
      <div class="listing-head">
        <div>
          <h3><a href="/listing?id={row['id']}">{title}</a></h3>
          <div class="meta">{address}</div>
          <div class="mini">{meta}</div>
        </div>
        <div class="score">{row['score']}/100</div>
      </div>
      <div class="chipbar">{''.join(chips)}</div>
      <div class="mini"><a href="{url}" target="_blank" rel="noreferrer">Open source listing</a></div>
      <div class="note">{notes or 'No notes yet.'}</div>
    </div>
    """


def render_dashboard(view: str, flash: str = "") -> bytes:
    rows = fetch_listings(view)
    cards = "".join(render_listing_card(row) for row in rows) or '<div class="card empty">No listings here yet.</div>'
    flash_html = f'<div class="card">{html.escape(flash)}</div>' if flash else ""
    body = f"""
    {flash_html}
    <div class="grid">
      <div class="stack">{cards}</div>
      <div class="stack">
        <div class="card">
          <h3 style="margin-top:0">Sync automated listings</h3>
          <p class="note">Pull fresh Richmond results from Rentcast and Furnished Finder, then score them into the dashboard.</p>
          <form method="post" action="/sync">
            <input type="hidden" name="view" value="{html.escape(view)}">
            <button type="submit">Run Automated Sync</button>
          </form>
        </div>
        <div class="card">
          <h3 style="margin-top:0">Add manual listing</h3>
          <p class="note">Paste a Zillow, Furnished Finder, Airbnb, Craigslist, or Facebook URL and track it here even before automation catches up.</p>
          <form method="post" action="/manual">
            <div class="row">
              <input name="title" placeholder="Title or building name" required>
              <input name="source" placeholder="Source (zillow, airbnb, furnished_finder)" required>
            </div>
            <input name="url" placeholder="https://..." required>
            <input name="address" placeholder="Address or neighborhood">
            <div class="row3">
              <input name="rent" placeholder="Monthly rent">
              <input name="beds" placeholder="Beds">
              <input name="baths" placeholder="Baths">
            </div>
            <div class="row3">
              <select name="furnished_status">
                <option value="unknown">Furnished unknown</option>
                <option value="yes">Furnished yes</option>
                <option value="maybe">Furnished maybe</option>
                <option value="no">Furnished no</option>
              </select>
              <input name="available_date_raw" placeholder="Available around Aug 1">
              <input name="lease_term_raw" placeholder="6 month / flexible / 12 month">
            </div>
            <textarea name="notes" placeholder="Why this one might be interesting"></textarea>
            <button type="submit">Add Listing</button>
          </form>
        </div>
      </div>
    </div>
    """
    return render_layout(f"Richmond Rental Finder - {view.title()}", body, active=view)


def render_listing_detail(row: sqlite3.Row, flash: str = "") -> bytes:
    chips = []
    for label, value in [
        ("source", row["source"]),
        ("score", f"{row['score']}/100"),
        ("furnished", row["furnished_status"]),
        ("utilities", row["utilities_status"]),
        ("status", row["status"]),
    ]:
        chips.append(f"<span class='chip'>{html.escape(label)}: {html.escape(str(value))}</span>")

    flash_html = f'<div class="card">{html.escape(flash)}</div>' if flash else ""
    body = f"""
    {flash_html}
    <div class="grid">
      <div class="stack">
        <div class="card">
          <h2 style="margin-top:0">{html.escape(row['title'] or row['address'] or 'Listing')}</h2>
          <div class="meta">{html.escape(row['address'] or '')}</div>
          <div class="chipbar">{''.join(chips)}</div>
          <p><a href="{html.escape(row['url'])}" target="_blank" rel="noreferrer">Open source listing</a></p>
          <div class="row3">
            <div><strong>Rent</strong><br>{'$' + format(int(row['rent']), ',') if row['rent'] else 'Unknown'}</div>
            <div><strong>Beds / Baths</strong><br>{(str(row['beds']) if row['beds'] is not None else '?')} / {(str(row['baths']) if row['baths'] is not None else '?')}</div>
            <div><strong>Sqft</strong><br>{format(int(row['sqft']), ',') if row['sqft'] else 'Unknown'}</div>
          </div>
          <div class="row" style="margin-top:14px">
            <div><strong>Move-in</strong><br>{html.escape(row['available_date_raw'] or 'Unknown')}</div>
            <div><strong>Lease</strong><br>{html.escape(row['lease_term_raw'] or 'Unknown')}</div>
          </div>
          <div class="row" style="margin-top:14px">
            <div><strong>Parking</strong><br>{html.escape(row['parking'] or 'Unknown')}</div>
            <div><strong>Laundry</strong><br>{html.escape(row['laundry'] or 'Unknown')}</div>
          </div>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <h3 style="margin-top:0">Review</h3>
          <form method="post" action="/listing/update">
            <input type="hidden" name="id" value="{row['id']}">
            <div class="row">
              <select name="status">
                {''.join(f"<option value='{s}' {'selected' if row['status']==s else ''}>{s}</option>" for s in VALID_STATUSES)}
              </select>
              <select name="furnished_status">
                {''.join(f"<option value='{s}' {'selected' if row['furnished_status']==s else ''}>{s}</option>" for s in VALID_TRI_STATE)}
              </select>
            </div>
            <div class="row">
              <select name="utilities_status">
                {''.join(f"<option value='{s}' {'selected' if row['utilities_status']==s else ''}>{s}</option>" for s in VALID_TRI_STATE)}
              </select>
              <select name="hidden">
                <option value="0" {'selected' if not row['hidden'] else ''}>Visible</option>
                <option value="1" {'selected' if row['hidden'] else ''}>Hidden</option>
              </select>
            </div>
            <input name="available_date_raw" value="{html.escape(row['available_date_raw'] or '')}" placeholder="Available date text">
            <input name="lease_term_raw" value="{html.escape(row['lease_term_raw'] or '')}" placeholder="Lease term text">
            <div class="row">
              <input name="parking" value="{html.escape(row['parking'] or '')}" placeholder="Parking">
              <input name="laundry" value="{html.escape(row['laundry'] or '')}" placeholder="Laundry">
            </div>
            <textarea name="notes" placeholder="Shared notes">{html.escape(row['notes'] or '')}</textarea>
            <button type="submit">Save</button>
          </form>
        </div>
      </div>
    </div>
    """
    return render_layout("Listing detail", body, active="inbox")


class RentalHandler(BaseHTTPRequestHandler):
    def parse_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw)
        return {k: v[-1] for k, v in parsed.items()}

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def send_html(self, content: bytes, status: int = 200, extra_headers: Optional[list[tuple[str, str]]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ["/", "/inbox"]:
            self.send_html(render_dashboard("inbox"))
            return
        if path == "/shortlist":
            self.send_html(render_dashboard("shortlist"))
            return
        if path == "/hidden":
            self.send_html(render_dashboard("hidden"))
            return
        if path == "/listing":
            listing_id = parse_qs(parsed.query).get("id", [None])[0]
            if not listing_id or not listing_id.isdigit():
                self.send_html(render_layout("Missing listing", '<div class="card">Missing listing id.</div>'))
                return
            row = fetch_listing(int(listing_id))
            if not row:
                self.send_html(render_layout("Not found", '<div class="card">Listing not found.</div>'), status=404)
                return
            self.send_html(render_listing_detail(row))
            return
        self.send_html(render_layout("Not found", '<div class="card">Not found.</div>'), status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        form = self.parse_form()

        if path == "/sync":
            counts = sync_all_sources()
            if counts:
                summary = ", ".join(
                    f"{count} {source.replace('_', ' ').title()} listings"
                    for source, count in sorted(counts.items())
                )
            else:
                summary = "0 listings"
            self.send_html(render_dashboard(form.get("view", "inbox"), flash=f"Synced {summary}."))
            return

        if path == "/manual":
            upsert_listing(
                {
                    "source": form.get("source", "manual"),
                    "external_id": None,
                    "url": form.get("url", ""),
                    "title": form.get("title", ""),
                    "address": form.get("address", ""),
                    "rent": form.get("rent"),
                    "beds": form.get("beds"),
                    "baths": form.get("baths"),
                    "furnished_status": form.get("furnished_status", "unknown"),
                    "available_date_raw": form.get("available_date_raw", ""),
                    "lease_term_raw": form.get("lease_term_raw", ""),
                    "notes": form.get("notes", ""),
                    "manual": True,
                }
            )
            self.send_html(render_dashboard("inbox", flash="Manual listing added."))
            return

        if path == "/listing/update":
            listing_id = form.get("id", "")
            if listing_id.isdigit():
                update_listing(int(listing_id), form)
                row = fetch_listing(int(listing_id))
                self.send_html(render_listing_detail(row, flash="Listing updated."))
                return
            self.send_html(render_layout("Error", '<div class="card">Bad listing id.</div>'), status=400)
            return

        self.send_html(render_layout("Not found", '<div class="card">Not found.</div>'), status=404)


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((APP_HOST, APP_PORT), RentalHandler)
    print(f"Richmond Rental Finder running at http://{APP_HOST}:{APP_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
