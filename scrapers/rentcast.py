#!/usr/bin/env python3
"""
Rentcast API Integration

Uses the official Rentcast API to search for rental listings.
Docs: https://developers.rentcast.io/reference/rental-listings-long-term
"""

import json
import os
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

# API endpoint
RENTCAST_API_URL = "https://api.rentcast.io/v1/listings/rental/long-term"

def get_api_key() -> str:
    """Load Rentcast API key from credentials file."""
    key_path = Path(os.path.expanduser("~/.clawdbot/credentials/rentcast_api_key"))
    if not key_path.exists():
        raise ValueError(
            f"Rentcast API key not found at {key_path}\n"
            "Sign up at https://app.rentcast.io/app/api and save your key there."
        )
    return key_path.read_text().strip()


def search_rentals(
    city: str = None,
    state: str = "AZ",
    zip_code: str = None,
    min_beds: int = 3,
    min_price: int = 3000,
    max_price: int = 6000,
    property_types: list = None,
    status: str = "Active",
    limit: int = 50,
) -> list[dict]:
    """
    Search for rental listings via Rentcast API.
    
    Args:
        city: City name (e.g., "Scottsdale")
        state: State code (e.g., "AZ")
        zip_code: ZIP code (e.g., "85254")
        min_beds: Minimum bedrooms
        min_price: Minimum monthly rent
        max_price: Maximum monthly rent
        property_types: List of types (Single Family, Townhouse, Condo, etc.)
        status: Listing status (Active, Inactive, etc.)
        limit: Max results to return
    
    Returns:
        List of normalized listing dicts
    """
    api_key = get_api_key()
    
    # Build query params
    params = {
        "state": state,
        "status": status,
        "limit": limit,
    }
    
    if zip_code:
        params["zipCode"] = zip_code
    elif city:
        params["city"] = city
    
    if min_beds:
        params["bedrooms"] = min_beds  # Exact match, or we filter after
    
    # Note: Rentcast uses "bathrooms", "bedrooms" as filters
    # Price filtering may need to be done client-side
    
    headers = {
        "Accept": "application/json",
        "X-Api-Key": api_key,
    }
    
    try:
        response = requests.get(RENTCAST_API_URL, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        # Normalize and filter results
        listings = []
        for item in data:
            listing = _normalize_listing(item)
            if listing and _matches_criteria(listing, min_beds, min_price, max_price, property_types):
                listings.append(listing)
        
        return listings
        
    except requests.RequestException as e:
        print(f"Rentcast API error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text[:500]}")
        return []


def _normalize_listing(raw: dict) -> Optional[dict]:
    """Convert Rentcast listing to our standard format."""
    try:
        listing_id = raw.get("id") or raw.get("addressLine1", "").replace(" ", "_")
        
        # Build address string
        address_parts = [
            raw.get("addressLine1", ""),
            raw.get("city", ""),
            raw.get("state", ""),
            raw.get("zipCode", ""),
        ]
        address = ", ".join(p for p in address_parts if p)
        
        # Price
        price = raw.get("price", 0)
        
        # Beds/baths
        beds = raw.get("bedrooms", 0)
        baths = raw.get("bathrooms", 0)
        
        # Square footage
        sqft = raw.get("squareFootage", 0)
        
        # Property type
        prop_type = raw.get("propertyType", "").lower()
        
        # Listing URL (Rentcast may not provide direct URL)
        # We'll construct one or use their provided URL
        listing_url = raw.get("listingUrl") or raw.get("url")
        if not listing_url:
            # Try to construct a Zillow/Redfin search URL as fallback
            addr_slug = raw.get("addressLine1", "").replace(" ", "-").lower()
            city_slug = raw.get("city", "").replace(" ", "-").lower()
            listing_url = f"https://www.zillow.com/homes/{addr_slug}-{city_slug}-{raw.get('state', 'AZ')}-{raw.get('zipCode', '')}"
        
        # Photos
        photos = raw.get("photos", [])
        image_url = photos[0] if photos else None
        
        # Listing date
        listed_date = raw.get("listedDate") or raw.get("createdDate")
        
        # Days on market
        days_on_market = raw.get("daysOnMarket", 0)
        
        return {
            "id": f"rentcast_{listing_id}",
            "source": "rentcast",
            "address": address,
            "price": price,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "property_type": prop_type,
            "url": listing_url,
            "image_url": image_url,
            "listed_date": listed_date,
            "days_on_market": days_on_market,
            "scraped_at": datetime.now().isoformat(),
            "raw": raw,  # Keep raw data for debugging
        }
        
    except Exception as e:
        print(f"Error normalizing Rentcast listing: {e}")
        return None


def _matches_criteria(
    listing: dict,
    min_beds: int,
    min_price: int,
    max_price: int,
    property_types: list = None,
) -> bool:
    """Check if listing matches search criteria."""
    # Check beds (>= min)
    if listing.get("beds", 0) < min_beds:
        return False
    
    # Check price range
    price = listing.get("price", 0)
    if price < min_price or price > max_price:
        return False
    
    # Check property type if specified
    if property_types:
        listing_type = listing.get("property_type", "").lower()
        # Map common variations
        type_map = {
            "single family": ["single family", "house", "sfr"],
            "townhouse": ["townhouse", "townhome"],
            "condo": ["condo", "condominium"],
        }
        
        matches = False
        for wanted in property_types:
            wanted_lower = wanted.lower()
            if wanted_lower in listing_type:
                matches = True
                break
            # Check aliases
            for canonical, aliases in type_map.items():
                if wanted_lower in aliases and any(a in listing_type for a in aliases):
                    matches = True
                    break
        
        if not matches and listing_type:  # Only filter if we have type info
            return False
    
    return True


if __name__ == "__main__":
    import sys
    
    # Test search
    zip_code = sys.argv[1] if len(sys.argv) > 1 else "85254"
    
    print(f"Searching Rentcast for rentals in {zip_code}...")
    results = search_rentals(
        zip_code=zip_code,
        min_beds=3,
        min_price=3000,
        max_price=6000,
    )
    
    print(f"\nFound {len(results)} listings:")
    for r in results[:10]:
        print(f"  - {r['address']}: ${r['price']}/mo, {r['beds']}bd/{r['baths']}ba")
        if r.get('days_on_market'):
            print(f"    Listed {r['days_on_market']} days ago")
        print(f"    {r['url']}")
