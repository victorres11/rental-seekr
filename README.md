# Richmond Rental Finder

Private Richmond rental dashboard with:
- Rentcast sync as the initial automated feed
- manual listing intake for Zillow / Furnished Finder / Airbnb / Craigslist / Facebook links
- shared notes and statuses for two people
- Richmond-specific scoring for furnished-ish August 1 / 6-month-fit rentals

## Setup

1. Get a free Rentcast API key: https://app.rentcast.io/app/api
2. Save your API key:
   ```bash
   echo "YOUR_API_KEY" > ~/.clawdbot/credentials/rentcast_api_key
   chmod 600 ~/.clawdbot/credentials/rentcast_api_key
   ```

3. Test the search:
   ```bash
   cd ~/clawd/rental-search
   ~/clawd/venv/bin/python search.py --dry-run
   ```

## Configuration

Edit `config.py` to adjust:
- Richmond locations / ZIP codes
- price range
- move-in window
- ideal lease timing
- host/port

## Usage

### CLI search
```bash
~/clawd/venv/bin/python search.py           # Run search, update seen listings
~/clawd/venv/bin/python search.py --dry-run # Run search without updating state
~/clawd/venv/bin/python search.py --json    # Output raw JSON
```

### Web app
```bash
~/clawd/venv/bin/python app.py
```

Then open `http://127.0.0.1:8765`.

### Hosted deploy
This app is prepared for Fly.io because it needs persistent SQLite storage for notes/statuses.

Key runtime env vars:
- `DB_FILE` (defaults to `/data/rental_search.db` in Fly)
- `RENTCAST_API_KEY`
- `PORT`

Deploy shape:
- `Dockerfile` builds the app
- `fly.toml` mounts a persistent volume at `/data`
- `requirements.txt` installs Python dependencies

### Daily digest
```bash
~/clawd/venv/bin/python daily_digest.py
```

## Cron Setup

The old digest flow can still run on a cron if you want a quiet automated feed:
```
0 8 1-31/2 * * cd ~/clawd/rental-search && ~/clawd/venv/bin/python daily_digest.py >> /tmp/rental-search.log 2>&1
```

## API Usage

- Free tier: 50 calls/month
- Current config uses 4 Richmond ZIPs plus a city-wide search
- Each sync = 1 API call per location

## Files

- `config.py` - Search configuration
- `search.py` - Main search orchestrator
- `app.py` - Private shared web dashboard
- `daily_digest.py` - Cron job entry point
- `Dockerfile` - Container image for hosted deploys
- `fly.toml` - Fly.io app and volume config
- `requirements.txt` - Python dependencies
- `scrapers/rentcast.py` - Rentcast API integration
- `seen_listings.json` - Tracks listings we've already seen
- `state.json` - Run state and history
- `rental_search.db` - SQLite database for listings, notes, and statuses
