"""
Rental Search Configuration

Using Rentcast API as the initial automated source.
"""
import os

# Search criteria
SEARCH_CONFIG = {
    "city": "Richmond",
    "state": "VA",
    "min_beds": 0,
    "min_price": 0,
    "max_price": 10000,
    # Rentcast property types: Single Family, Townhouse, Condo, Apartment, Multi Family
    "property_types": ["Apartment", "Condo", "Townhouse", "Single Family"],

    # Locations to search. Rentcast city search is broad, so we also keep a few
    # Richmond ZIP codes to improve coverage in neighborhoods you might actually want.
    "locations": [
        {
            "name": "Richmond City",
            "city": "Richmond",
            "state": "VA",
        },
        {
            "name": "The Fan / Museum District",
            "zip": "23220",
        },
        {
            "name": "Scott's Addition / Near West",
            "zip": "23230",
        },
        {
            "name": "Downtown / Shockoe",
            "zip": "23219",
        },
        {
            "name": "Church Hill / East End",
            "zip": "23223",
        },
    ],

    # Preferences for scoring and manual review
    "ideal_price": 6500,
    "target_min_price": 4000,
    "target_max_price": 8000,
    "available_after": "2026-07-20",
    "available_before": "2026-08-10",
    "ideal_move_in": "2026-08-01",
    "ideal_lease_months": 6,
}

# File paths
STATE_FILE = "state.json"
SEEN_LISTINGS_FILE = "seen_listings.json"
DB_FILE = os.getenv("DB_FILE", "rental_search.db")

# Notification settings
NOTIFY_CHANNEL = "telegram"
NOTIFY_TARGET = os.getenv("NOTIFY_TARGET", "8526033276")  # Victor's Telegram

# Shared app access
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "8765")))
APP_PASSWORD = os.getenv("APP_PASSWORD", "richmond-rentals")
