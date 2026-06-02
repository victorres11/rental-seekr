#!/usr/bin/env python3
"""
Rental Search Daily Digest

Run by cron every other day to check for new listings
and send a Telegram digest.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from search import run_search, load_state, save_state
from config import NOTIFY_CHANNEL, NOTIFY_TARGET


def send_telegram_message(message: str) -> bool:
    """Send message via Clawdbot CLI."""
    try:
        result = subprocess.run(
            [
                "clawdbot", "message", "send",
                "--channel", NOTIFY_CHANNEL,
                "--target", NOTIFY_TARGET,
                "--message", message,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            print(f"Error sending message: {result.stderr}")
            return False
        
        print("Message sent successfully")
        return True
        
    except subprocess.TimeoutExpired:
        print("Timeout sending message")
        return False
    except Exception as e:
        print(f"Error sending message: {e}")
        return False


def main():
    """Main entry point for the daily digest."""
    print(f"=== Rental Search Digest: {datetime.now().isoformat()} ===\n")
    
    # Run the search
    digest = run_search(dry_run=False)
    
    if digest:
        print("\nNew listings found! Sending digest...")
        success = send_telegram_message(digest)
        
        if success:
            print("Digest sent successfully!")
        else:
            print("Failed to send digest - check logs")
            # Save the digest locally as backup
            backup_path = Path(__file__).parent / "last_digest.txt"
            backup_path.write_text(digest)
            print(f"Digest saved to {backup_path}")
    else:
        print("\nNo new listings found.")
        # Optionally send a "no new listings" message on certain days
        # For now, stay quiet to avoid spam
    
    # Update state
    state = load_state()
    state["last_digest_run"] = datetime.now().isoformat()
    state["last_digest_sent"] = bool(digest)
    save_state(state)
    
    print("\nDone!")


if __name__ == "__main__":
    main()
