#!/usr/bin/env python3
"""
Furnished Finder integration via Firecrawl.

The Furnished Finder site blocks plain requests from commodity hosts, so we use
Firecrawl to fetch the Richmond search page and parse listing cards from the
returned markdown.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests


DEFAULT_FIRECRAWL_API_URL = "https://api.firecrawl.dev"
DEFAULT_SEARCH_URL = "https://www.furnishedfinder.com/housing/us--va--richmond"
STREET_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z0-9][A-Za-z0-9.'-]*(?:\s+[A-Z0-9][A-Za-z0-9.'-]*){0,6}\s"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Place|Pl|Way)\b"
)


def get_firecrawl_credentials() -> tuple[str, str]:
    """Load Firecrawl credentials from env vars or the local CLI config."""
    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    api_url = os.getenv("FIRECRAWL_API_URL", DEFAULT_FIRECRAWL_API_URL).strip() or DEFAULT_FIRECRAWL_API_URL
    if api_key:
        return api_key, api_url.rstrip("/")

    credentials_path = Path.home() / "Library/Application Support/firecrawl-cli/credentials.json"
    if not credentials_path.exists():
        raise ValueError(
            "Firecrawl credentials not found. Set FIRECRAWL_API_KEY or run `firecrawl login` first."
        )

    data = json.loads(credentials_path.read_text())
    api_key = str(data.get("apiKey", "")).strip()
    api_url = str(data.get("apiUrl", DEFAULT_FIRECRAWL_API_URL)).strip() or DEFAULT_FIRECRAWL_API_URL
    if not api_key:
        raise ValueError(
            "Firecrawl credentials file exists but does not contain an API key."
        )
    return api_key, api_url.rstrip("/")


def scrape_search_page(search_url: str = DEFAULT_SEARCH_URL, max_age_ms: int = 6 * 60 * 60 * 1000) -> dict:
    """Fetch Furnished Finder search results as markdown through Firecrawl."""
    api_key, api_url = get_firecrawl_credentials()
    response = requests.post(
        f"{api_url}/v1/scrape",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "url": search_url,
            "formats": ["markdown"],
            "maxAge": max_age_ms,
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise ValueError(f"Firecrawl scrape failed: {payload.get('error') or 'unknown error'}")
    return payload.get("data", {})


def scrape_detail_page(detail_url: str, max_age_ms: int = 24 * 60 * 60 * 1000) -> dict:
    """Fetch a Furnished Finder property detail page as markdown."""
    api_key, api_url = get_firecrawl_credentials()
    response = requests.post(
        f"{api_url}/v1/scrape",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "url": detail_url,
            "formats": ["markdown"],
            "maxAge": max_age_ms,
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise ValueError(f"Firecrawl scrape failed: {payload.get('error') or 'unknown error'}")
    return payload.get("data", {})


def _clean_block_text(block: str) -> str:
    text = block.replace("\\\\\n\\\\\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first_sentence(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0].strip()
    return sentence[:160]


def _extract_neighborhood_name(text: str) -> str:
    patterns = [
        (r"\b(Fan district)\b", "Fan District"),
        (r"\b(?:the )?Fan\b", "Fan District"),
        (r"\b(Scott's Addition)\b", "Scott's Addition"),
        (r"\b(Museum District)\b", "Museum District"),
        (r"\b(Jackson Ward)\b", "Jackson Ward"),
        (r"\b(Church Hill)\b", "Church Hill"),
        (r"\b(Arts District)\b", "Arts District"),
        (r"\b(Monroe Ward)\b", "Monroe Ward"),
        (r"\b(Shockoe Bottom)\b", "Shockoe Bottom"),
        (r"\b(Shockoe Slip)\b", "Shockoe Slip"),
        (r"\b(Carytown)\b", "Carytown"),
        (r"\b(Manchester)\b", "Manchester"),
        (r"\b(West End)\b", "West End"),
        (r"\b(Lakeside)\b", "Lakeside"),
        (r"\b(Near VCU|VCU(?: MCV)? Campus|VCU health)\b", "Near VCU"),
        (r"\b(Downtown Richmond)\b", "Downtown Richmond"),
    ]
    for pattern, label in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return label
    generic_match = re.search(
        r"\b([A-Z][A-Za-z'&.-]+(?:\s+[A-Z][A-Za-z'&.-]+){0,2})\s+neighborhood\b",
        text,
    )
    if generic_match:
        candidate = generic_match.group(1).strip()
        if candidate.lower() not in {"this", "our", "north"}:
            return candidate
    return ""


def enrich_listing_details(listing: dict) -> dict:
    """Enrich a search-card listing with detail-page data when available."""
    detail = scrape_detail_page(listing["url"])
    markdown = detail.get("markdown") or ""
    if not markdown:
        return listing

    neighborhood_overview_match = re.search(
        r"Neighborhood overview\s+(.*?)(?:\n## |\nClosest facilities|\n## Rooms & beds)",
        markdown,
        re.DOTALL | re.IGNORECASE,
    )
    neighborhood_overview = _clean_block_text(neighborhood_overview_match.group(1)) if neighborhood_overview_match else ""

    space_match = re.search(
        r"\nSpace\s+(.*?)(?:\nRead more|\nNeighborhood overview|\n## Rooms & beds)",
        markdown,
        re.DOTALL | re.IGNORECASE,
    )
    space_text = _clean_block_text(space_match.group(1)) if space_match else ""

    description_text = " ".join(part for part in [neighborhood_overview, space_text, listing.get("title", "")] if part).strip()
    neighborhood = _extract_neighborhood_name(description_text)

    min_stay_match = re.search(r"Minimum stay:\s*([^\n]+)", markdown, re.IGNORECASE)
    sqft_match = re.search(r"(\d[\d,]*)\s*Sq\.\s*Ft", markdown, re.IGNORECASE)
    address_match = STREET_RE.search(description_text)

    enriched = dict(listing)
    if neighborhood:
        enriched["neighborhood"] = neighborhood
    if min_stay_match:
        enriched["lease_term_raw"] = min_stay_match.group(1).strip()
    if sqft_match:
        enriched["sqft"] = int(sqft_match.group(1).replace(",", ""))
    if address_match:
        street = address_match.group(0).strip()
        city = listing.get("address", "").strip()
        enriched["address"] = f"{street}, {city}" if city and city not in street else street
    enriched["raw"] = {
        **(listing.get("raw") or {}),
        "detail_markdown_excerpt": markdown[:4000],
    }
    return enriched


def _parse_listing_block(block: str) -> Optional[dict]:
    url_match = re.search(r"\]\((https://www\.furnishedfinder\.com/property/[^)]+)\)", block)
    if not url_match:
        return None
    image_match = re.search(r"\[!\[property_photo\]\((https://[^)]+)\)", block)
    title_match = re.search(r"\*\*(.+?)\*\*", block)
    price_match = re.search(r"\$([\d,]+)/month", block)
    beds_match = re.search(r"(\d+(?:\.\d+)?) Bedrooms?", block)
    url = url_match.group(1)
    property_slug = re.search(r"/property/([^?]+)", url)
    external_id = property_slug.group(1) if property_slug else url

    cleaned = _clean_block_text(block)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    baths_match = re.search(r"(\d+(?:\.\d+)?) (?:shared |private )?(?:bathroom|bathrooms|ba)\b", cleaned, re.IGNORECASE)

    property_label = ""
    address = ""
    for line in lines:
        line_match = re.match(r"(.+?)in ([A-Za-z .'-]+,\s*[A-Z]{2})$", line)
        if line_match:
            property_label = line_match.group(1).strip()
            address = line_match.group(2).strip()
            break

    title = title_match.group(1).strip() if title_match else ""
    if not title:
        for idx, line in enumerate(lines):
            if address and line == address and idx >= 1:
                candidate = lines[idx - 1]
                if candidate != property_label:
                    title = candidate
                    break
    if not title:
        title = property_label or "Furnished Finder listing"

    property_type = property_label.replace("House icon", "").strip().lower()
    if property_type.lower().startswith("room - "):
        property_type = property_type[7:].strip().lower()

    utilities = "yes" if "Utilities Included" in block else "unknown"
    parking = "Available" if "Parking" in block else ""
    laundry = "Washer and dryer" if "Washer and dryer" in block else ""
    available_date = "Available" if "Available" in block else ""

    return {
        "id": f"furnished_finder_{external_id}",
        "source": "furnished_finder",
        "external_id": external_id,
        "title": title,
        "address": address,
        "price": int(price_match.group(1).replace(",", "")) if price_match else 0,
        "beds": float(beds_match.group(1)) if beds_match else None,
        "baths": float(baths_match.group(1)) if baths_match else None,
        "sqft": None,
        "property_type": property_type,
        "url": urljoin("https://www.furnishedfinder.com", url),
        "image_url": image_match.group(1) if image_match else None,
        "available_date_raw": available_date,
        "lease_term_raw": "",
        "furnished_status": "yes",
        "utilities_status": utilities,
        "parking": parking,
        "laundry": laundry,
        "listed_date": None,
        "days_on_market": None,
        "scraped_at": datetime.now().isoformat(),
        "raw": {
            "block": cleaned,
            "url": url,
        },
    }


def search_furnished_finder(
    search_url: str = DEFAULT_SEARCH_URL,
    min_beds: int = 0,
    min_price: int = 0,
    max_price: int = 10000,
    enrich_limit: int = 20,
) -> list[dict]:
    """Return normalized Furnished Finder listings from the Richmond search page."""
    data = scrape_search_page(search_url=search_url)
    markdown = data.get("markdown") or ""
    if not markdown:
        return []

    match = re.search(
        r"(# \d+ furnished monthly rentals near Richmond, VA.*?)(?:\n\[1\]\(|\nFurnished apartments, homes, condos, rooms, and more\.)",
        markdown,
        re.DOTALL,
    )
    listing_section = match.group(1) if match else markdown
    blocks = re.split(r"\n(?=\[!\[property_photo\])", listing_section)

    listings: list[dict] = []
    for block in blocks:
        if "[![property_photo]" not in block:
            continue
        listing = _parse_listing_block(block)
        if not listing:
            continue
        beds = listing.get("beds") or 0
        price = listing.get("price") or 0
        if beds < min_beds:
            continue
        if price and (price < min_price or price > max_price):
            continue
        listings.append(listing)

    for idx, listing in enumerate(listings):
        if idx >= enrich_limit:
            break
        try:
            listings[idx] = enrich_listing_details(listing)
        except Exception:
            continue

    return listings


if __name__ == "__main__":
    results = search_furnished_finder()
    print(f"Found {len(results)} Furnished Finder listings")
    for item in results[:10]:
        print(f"- {item['title']} :: ${item['price']}/mo :: {item['beds']}bd/{item['baths']}ba")
