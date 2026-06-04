#!/usr/bin/env python3
"""
Rental Search - Main orchestrator

Runs scrapers, tracks seen listings, and formats results.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import SEARCH_CONFIG, STATE_FILE, SEEN_LISTINGS_FILE
from scrapers.furnished_finder import search_furnished_finder
from scrapers.rentcast import search_rentals


def load_seen_listings() -> set:
    """Load previously seen listing IDs."""
    path = Path(__file__).parent / SEEN_LISTINGS_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return set(data.get("seen_ids", []))
        except json.JSONDecodeError:
            return set()
    return set()


def save_seen_listings(seen_ids: set):
    """Save seen listing IDs."""
    path = Path(__file__).parent / SEEN_LISTINGS_FILE
    data = {
        "seen_ids": list(seen_ids),
        "last_updated": datetime.now().isoformat(),
    }
    path.write_text(json.dumps(data, indent=2))


def load_state() -> dict:
    """Load search state."""
    path = Path(__file__).parent / STATE_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict):
    """Save search state."""
    path = Path(__file__).parent / STATE_FILE
    path.write_text(json.dumps(state, indent=2))


def search_all_locations() -> list[dict]:
    """Search all configured locations and return combined results."""
    all_listings = []
    
    config = SEARCH_CONFIG
    
    for location in config["locations"]:
        print(f"Searching {location['name']}...")
        
        # Search Rentcast API
        try:
            # Use zip code if available, otherwise city
            if "zip" in location:
                results = search_rentals(
                    zip_code=location["zip"],
                    state=location.get("state", config.get("state", "VA")),
                    min_beds=config.get("min_beds", 3),
                    min_price=config.get("min_price", 3000),
                    max_price=config.get("max_price", 6000),
                    property_types=config.get("property_types", ["Single Family", "Townhouse"]),
                )
            else:
                results = search_rentals(
                    city=location.get("city"),
                    state=location.get("state", "AZ"),
                    min_beds=config.get("min_beds", 3),
                    min_price=config.get("min_price", 3000),
                    max_price=config.get("max_price", 6000),
                    property_types=config.get("property_types", ["Single Family", "Townhouse"]),
                )
            print(f"  Found {len(results)} listings from Rentcast")
            all_listings.extend(results)
        except Exception as e:
            print(f"  Error searching Rentcast: {e}")

    try:
        print("Searching Furnished Finder...")
        furnished_results = search_furnished_finder(
            min_beds=config.get("min_beds", 0),
            min_price=config.get("min_price", 0),
            max_price=config.get("max_price", 10000),
        )
        print(f"  Found {len(furnished_results)} listings from Furnished Finder")
        all_listings.extend(furnished_results)
    except Exception as e:
        print(f"  Error searching Furnished Finder: {e}")
    
    # Deduplicate by listing ID across sources.
    seen_ids = set()
    unique_listings = []
    for listing in all_listings:
        if listing["id"] not in seen_ids:
            seen_ids.add(listing["id"])
            unique_listings.append(listing)
    
    return unique_listings


def filter_new_listings(listings: list[dict], seen_ids: set) -> list[dict]:
    """Filter out listings we've already seen."""
    return [l for l in listings if l["id"] not in seen_ids]


def format_listing(listing: dict) -> str:
    """Format a single listing for display."""
    lines = []
    
    # Address as header
    lines.append(f"📍 **{listing['address']}**")
    
    # Details line
    details = []
    if listing.get("beds"):
        details.append(f"{listing['beds']} bed")
    if listing.get("baths"):
        details.append(f"{listing['baths']} bath")
    if listing.get("sqft"):
        details.append(f"{listing['sqft']:,} sqft")
    
    price_str = f"${listing.get('price', 0):,}/mo"
    lines.append(f"   {' | '.join(details)} | {price_str}")
    
    # Property type if available
    if listing.get("property_type"):
        lines.append(f"   Type: {listing['property_type'].title()}")
    
    # Link
    lines.append(f"   🔗 {listing['url']}")
    
    return "\n".join(lines)


def format_digest(listings: list[dict]) -> str:
    """Format all listings into a digest message."""
    if not listings:
        return None
    
    today = datetime.now().strftime("%b %d")
    
    lines = [f"🏠 **New Rentals Found** ({today})\n"]
    
    # Sort by price
    sorted_listings = sorted(listings, key=lambda x: x.get("price", 0))
    
    for i, listing in enumerate(sorted_listings, 1):
        lines.append(f"\n{i}. {format_listing(listing)}")
    
    lines.append(f"\n---\nFound {len(listings)} new listing(s) matching your criteria.")
    
    return "\n".join(lines)


def run_search(dry_run: bool = False) -> Optional[str]:
    """
    Run the rental search.
    
    Args:
        dry_run: If True, don't update seen listings
    
    Returns:
        Formatted digest message if new listings found, None otherwise
    """
    print(f"Starting rental search at {datetime.now().isoformat()}")
    
    # Load seen listings
    seen_ids = load_seen_listings()
    print(f"Tracking {len(seen_ids)} previously seen listings")
    
    # Search all locations
    all_listings = search_all_locations()
    print(f"\nTotal listings found: {len(all_listings)}")
    
    # Filter to new listings only
    new_listings = filter_new_listings(all_listings, seen_ids)
    print(f"New listings: {len(new_listings)}")
    
    if not new_listings:
        print("No new listings to report")
        return None
    
    # Format digest
    digest = format_digest(new_listings)
    
    # Update seen listings (unless dry run)
    if not dry_run:
        for listing in new_listings:
            seen_ids.add(listing["id"])
        save_seen_listings(seen_ids)
        print(f"Updated seen listings (now tracking {len(seen_ids)})")
    
    # Update state
    state = load_state()
    state["last_run"] = datetime.now().isoformat()
    state["last_new_count"] = len(new_listings)
    save_state(state)
    
    return digest


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Search for rental listings")
    parser.add_argument("--dry-run", action="store_true", help="Don't update seen listings")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of formatted")
    args = parser.parse_args()
    
    if args.json:
        listings = search_all_locations()
        print(json.dumps(listings, indent=2))
    else:
        digest = run_search(dry_run=args.dry_run)
        if digest:
            print("\n" + "="*50)
            print("DIGEST:")
            print("="*50)
            print(digest)
        else:
            print("\nNo new listings to report.")
