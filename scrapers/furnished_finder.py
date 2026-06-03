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


def _clean_block_text(block: str) -> str:
    text = block.replace("\\\\\n\\\\\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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

    return listings


if __name__ == "__main__":
    results = search_furnished_finder()
    print(f"Found {len(results)} Furnished Finder listings")
    for item in results[:10]:
        print(f"- {item['title']} :: ${item['price']}/mo :: {item['beds']}bd/{item['baths']}ba")
