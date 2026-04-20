#!/usr/bin/env python3
"""
Book a restaurant table on Chope via the booking widget.

Uses the direct booking.chope.co widget URL (no login required — guest checkout).
Requires Playwright MCP server for form submission.

Usage:
  # Book with defaults from preferences.json
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2

  # Book with special request
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 \
    --request "Window seat if possible"

  # Dry run — show what would be booked without submitting
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 --dry-run

  # Output booking URL only (for manual completion)
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 --url-only
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

SKILL_DIR = Path(__file__).resolve().parent.parent
PREFS_PATH = SKILL_DIR / "references" / "preferences.json"
VENUES_PATH = SKILL_DIR / "references" / "venues.json"

# Import the availability checker for rid resolution
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from check_availability import resolve_rid, load_prefs, get_available_times, format_date_for_chope


def load_guest_info() -> dict:
    """Load guest contact info from preferences."""
    prefs_path = SKILL_DIR / "references" / "guest.json"
    if prefs_path.exists():
        return json.loads(prefs_path.read_text())
    return {}


def build_booking_url(rid: str, name: str, date: str, time: str,
                      adults: int, children: int = 0) -> str:
    """
    Build the direct booking.chope.co widget URL.

    Args:
        rid: Restaurant ID (e.g. 'nobu2212sg')
        name: Restaurant display name
        date: Date in 'DD Mon YYYY' format (e.g. '23 Apr 2026')
        time: Time slot (e.g. '7:00 pm')
        adults: Number of adults
        children: Number of children
    """
    params = {
        "date": date,
        "name": name,
        "GTM_RestaurantUID": rid,
        "GTM_RestaurantName": name,
        "time": time,
        "adults": str(adults),
        "children": str(children),
        "rid": rid,
        "source": "chope.com.sg",
        "redirect": "1",
        "reservation_charge": "0",
        "lang": "en_US",
        "country_code": "SG",
    }
    return f"https://booking.chope.co/widget/#/booking_check?{urlencode(params)}"


def format_date_for_booking(date_str: str) -> str:
    """Convert YYYY-MM-DD to 'DD Mon YYYY' for the booking widget."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%-d %b %Y")


def generate_playwright_script(url: str, guest: dict, request: str = None,
                               dry_run: bool = False) -> str:
    """
    Generate a Playwright automation script for the booking form.

    Returns the script as a string that can be executed via Playwright MCP.
    """
    first_name = guest.get("first_name", "")
    last_name = guest.get("last_name", "")
    email = guest.get("email", "")
    phone = guest.get("phone", "")
    country_code = guest.get("country_code", "+65")

    steps = [
        f'// Navigate to booking widget',
        f'await page.goto("{url}");',
        f'await page.waitForTimeout(2000);',
        f'',
        f'// Fill contact details',
        f'await page.getByRole("textbox", {{ name: "First Name" }}).fill("{first_name}");',
        f'await page.getByRole("textbox", {{ name: "Last Name" }}).fill("{last_name}");',
        f'await page.getByRole("textbox", {{ name: "Email address" }}).fill("{email}");',
        f'await page.getByRole("textbox", {{ name: "Mobile number" }}).fill("{phone}");',
    ]

    if request:
        steps.extend([
            f'',
            f'// Add special request',
            f'await page.locator("text=Special requests").locator("..").locator("text=Add").click();',
            f'await page.waitForTimeout(500);',
            f'await page.getByRole("textbox", {{ name: "Enter your response" }}).fill("{request}");',
        ])

    if not dry_run:
        steps.extend([
            f'',
            f'// Accept restaurant policy and submit',
            f'await page.locator("input[type=checkbox]").last().check();',
            f'await page.waitForTimeout(500);',
            f'await page.getByRole("button", {{ name: "Book table" }}).click();',
            f'await page.waitForTimeout(3000);',
        ])
    else:
        steps.extend([
            f'',
            f'// DRY RUN — not submitting',
        ])

    return "\n".join(steps)


def main():
    parser = argparse.ArgumentParser(description="Book a table on Chope")
    parser.add_argument("--restaurant", "-r", required=True, help="Restaurant name or slug")
    parser.add_argument("--date", "-d", required=True, help="Date (YYYY-MM-DD)")
    parser.add_argument("--time", "-t", required=True, help="Time slot (e.g. '7:00 pm')")
    parser.add_argument("--pax", "-p", type=int, help="Number of guests")
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--request", help="Special request (e.g. 'Window seat')")
    parser.add_argument("--dry-run", action="store_true", help="Fill form but don't submit")
    parser.add_argument("--url-only", action="store_true", help="Print booking URL and exit")
    parser.add_argument("--script", action="store_true", help="Output Playwright script")
    args = parser.parse_args()

    prefs = load_prefs()
    base_url = prefs["base_url"]
    pax = args.pax or prefs["default_pax"]

    # Resolve restaurant
    rid, slug = resolve_rid(args.restaurant, base_url)
    if not rid:
        print(f"Could not find restaurant '{args.restaurant}' on Chope")
        sys.exit(1)

    # Get restaurant name from venue cache
    venues_data = json.loads(VENUES_PATH.read_text()) if VENUES_PATH.exists() else {}
    restaurant_name = args.restaurant.replace("-", " ").title()
    for v in venues_data.values():
        if isinstance(v, dict) and v.get("rid") == rid:
            restaurant_name = v.get("name", restaurant_name)
            break

    # Verify the time slot is available
    chope_date = format_date_for_chope(args.date)
    slots = get_available_times(rid, chope_date, pax, args.children, base_url)
    slot_texts = [s["text"] for s in slots]

    if not slots:
        print(f"No availability at {restaurant_name} on {args.date} for {pax} pax")
        sys.exit(1)

    if args.time not in slot_texts:
        print(f"'{args.time}' is not available at {restaurant_name} on {args.date}")
        print(f"Available slots: {', '.join(slot_texts)}")
        sys.exit(1)

    # Build booking URL
    booking_date = format_date_for_booking(args.date)
    url = build_booking_url(rid, restaurant_name, booking_date, args.time, pax, args.children)

    if args.url_only:
        print(url)
        sys.exit(0)

    # Load guest info
    guest = load_guest_info()
    if not guest.get("first_name"):
        print("Guest info not configured. Create references/guest.json with:")
        print(json.dumps({
            "first_name": "Nigel",
            "last_name": "Lam",
            "email": "morty.pepper.potts@gmail.com",
            "phone": "91234567",
            "country_code": "+65",
        }, indent=2))
        print(f"\nBooking URL (complete manually): {url}")
        sys.exit(1)

    # Summary
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Booking Summary")
    print(f"{'=' * 50}")
    print(f"Restaurant: {restaurant_name}")
    print(f"Date:       {args.date} ({datetime.strptime(args.date, '%Y-%m-%d').strftime('%A')})")
    print(f"Time:       {args.time}")
    print(f"Guests:     {pax} adults{f', {args.children} children' if args.children else ''}")
    print(f"Name:       {guest['first_name']} {guest['last_name']}")
    print(f"Email:      {guest['email']}")
    print(f"Phone:      {guest.get('country_code', '+65')} {guest['phone']}")
    if args.request:
        print(f"Request:    {args.request}")
    print(f"{'=' * 50}")
    print(f"\nBooking URL: {url}")

    # Generate Playwright script
    script = generate_playwright_script(url, guest, args.request, args.dry_run)

    if args.script:
        print(f"\n// Playwright script:")
        print(script)
    else:
        # Write script to temp file for Playwright MCP execution
        script_path = Path("/tmp/chope_book.js")
        # Wrap in async function for Playwright MCP
        wrapped = f"async (page) => {{\n{script}\n  return await page.title();\n}}"
        script_path.write_text(wrapped)
        print(f"\nPlaywright script saved to: {script_path}")
        print(f"Execute via Playwright MCP browser_run_code or manually open the URL above.")


if __name__ == "__main__":
    main()
