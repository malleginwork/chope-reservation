#!/usr/bin/env python3
"""
Book a restaurant table on Chope via the booking widget.

Uses the direct booking.chope.co widget URL (no login required — guest checkout).
Guest contact details are loaded from references/guest.json.
Playwright MCP is used for auto-fill and submission — NO CAPTCHA on guest checkout.

Usage:
  # Book with restaurant lookup (verifies availability first)
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2

  # Book using a pre-built booking URL (skips availability check)
  python3 book_chope.py --url "https://booking.chope.co/widget/#/booking_check?..."

  # Book with special request
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 \
    --request "Window seat if possible"

  # Dry run — fill form but don't submit
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 --dry-run

  # Output booking URL only (for manual completion via Telegram)
  python3 book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 --url-only

  # Output Playwright MCP instructions (for agent execution)
  python3 book_chope.py --url "https://booking.chope.co/..." --playwright-instructions
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, parse_qs

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


def parse_booking_url(url: str) -> dict:
    """Extract booking details from a Chope booking URL."""
    # Handle hash fragment URLs (booking.chope.co/widget/#/booking_check?...)
    if "#" in url:
        fragment = url.split("#", 1)[1]
        if "?" in fragment:
            query_string = fragment.split("?", 1)[1]
        else:
            query_string = ""
    else:
        query_string = urlparse(url).query

    params = parse_qs(query_string)
    return {
        "restaurant": params.get("name", params.get("GTM_RestaurantName", ["Unknown"]))[0],
        "rid": params.get("rid", [""])[0],
        "date": params.get("date", [""])[0],
        "time": params.get("time", [""])[0],
        "adults": int(params.get("adults", ["2"])[0]),
        "children": int(params.get("children", ["0"])[0]),
    }


def generate_playwright_instructions(url: str, guest: dict, request: str = None,
                                     dry_run: bool = False) -> str:
    """
    Generate step-by-step Playwright MCP instructions for the agent.

    These are human-readable instructions that an agent with Playwright MCP
    can follow using browser_navigate, browser_fill_form, browser_click, etc.
    """
    first_name = guest.get("first_name", "")
    last_name = guest.get("last_name", "")
    email = guest.get("email", "")
    phone = guest.get("phone", "")

    lines = [
        "PLAYWRIGHT MCP INSTRUCTIONS — Complete Chope Booking",
        "=" * 55,
        "",
        "Step 1: Navigate to the booking URL:",
        f"  browser_navigate → {url}",
        "",
        "Step 2: Wait for the form to load (2-3 seconds), then take a snapshot:",
        "  browser_snapshot",
        "",
        "Step 3: Fill the contact form using browser_fill_form:",
        f'  First Name → "{first_name}"',
        f'  Last Name → "{last_name}"',
        f'  Email address → "{email}"',
        f'  Mobile number → "{phone}"',
    ]

    if request:
        lines.extend([
            "",
            "Step 4: Click 'Add' under 'Special requests' section:",
            "  browser_click → the 'Add' text next to Special requests heading",
            "",
            "Step 5: Fill special request:",
            f'  Enter your response → "{request}"',
            "",
            "Step 6: Check the restaurant policy checkbox (last checkbox on page):",
            "  browser_click → 'I agree to the restaurant's reservations policy' checkbox",
        ])
        submit_step = 7
    else:
        lines.extend([
            "",
            "Step 4: Check the restaurant policy checkbox (last checkbox on page):",
            "  browser_click → 'I agree to the restaurant's reservations policy' checkbox",
        ])
        submit_step = 5

    if not dry_run:
        lines.extend([
            "",
            f"Step {submit_step}: Click 'Book table' button to submit:",
            "  browser_click → 'Book table' button",
            "",
            f"Step {submit_step + 1}: Wait 3 seconds, then snapshot to confirm success:",
            "  browser_snapshot → look for confirmation message",
            "",
            "IMPORTANT: There is NO CAPTCHA on guest checkout. The form submits directly.",
            "Guest details are pre-configured in references/guest.json.",
        ])
    else:
        lines.extend([
            "",
            f"Step {submit_step}: DRY RUN — do NOT click 'Book table'. Take a snapshot to verify form is filled correctly.",
        ])

    return "\n".join(lines)


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
    parser.add_argument("--restaurant", "-r", help="Restaurant name or slug")
    parser.add_argument("--date", "-d", help="Date (YYYY-MM-DD)")
    parser.add_argument("--time", "-t", help="Time slot (e.g. '7:00 pm')")
    parser.add_argument("--pax", "-p", type=int, help="Number of guests")
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--url", help="Pre-built booking URL (skips availability check)")
    parser.add_argument("--request", help="Special request (e.g. 'Window seat')")
    parser.add_argument("--dry-run", action="store_true", help="Fill form but don't submit")
    parser.add_argument("--url-only", action="store_true", help="Print booking URL and exit")
    parser.add_argument("--script", action="store_true", help="Output Playwright JS script")
    parser.add_argument("--playwright-instructions", action="store_true",
                        help="Output step-by-step Playwright MCP instructions for agent")
    args = parser.parse_args()

    prefs = load_prefs()
    guest = load_guest_info()

    # Mode 1: Pre-built URL — skip availability check, go straight to booking
    if args.url:
        info = parse_booking_url(args.url)
        url = args.url

        if not guest.get("first_name"):
            print("Guest info not configured. Create references/guest.json")
            print(f"\nBooking URL (complete manually): {url}")
            sys.exit(1)

        print(f"\nBooking Summary (from URL)")
        print(f"{'=' * 50}")
        print(f"Restaurant: {info['restaurant']}")
        print(f"Date:       {info['date']}")
        print(f"Time:       {info['time']}")
        children = info['children']
        kids_str = f", {children} children" if children else ""
        print(f"Guests:     {info['adults']} adults{kids_str}")
        print(f"Name:       {guest['first_name']} {guest['last_name']}")
        print(f"Email:      {guest['email']}")
        print(f"Phone:      {guest.get('country_code', '+65')} {guest['phone']}")
        if args.request:
            print(f"Request:    {args.request}")
        print(f"{'=' * 50}")
        print(f"\nBooking URL: {url}")

        if args.playwright_instructions:
            print(f"\n{generate_playwright_instructions(url, guest, args.request, args.dry_run)}")
        elif args.script:
            script = generate_playwright_script(url, guest, args.request, args.dry_run)
            print(f"\n// Playwright script:")
            print(script)
        else:
            script = generate_playwright_script(url, guest, args.request, args.dry_run)
            script_path = Path("/tmp/chope_book.js")
            wrapped = f"async (page) => {{\n{script}\n  return await page.title();\n}}"
            script_path.write_text(wrapped)
            print(f"\nPlaywright script saved to: {script_path}")
            print(f"Execute via Playwright MCP or manually open the URL above.")

        return

    # Mode 2: Restaurant lookup — verify availability, build URL
    if not args.restaurant:
        parser.error("Either --restaurant or --url is required")

    if not args.date or not args.time:
        parser.error("--date and --time are required when using --restaurant")

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

    # Generate output
    if args.playwright_instructions:
        print(f"\n{generate_playwright_instructions(url, guest, args.request, args.dry_run)}")
    elif args.script:
        print(f"\n// Playwright script:")
        print(generate_playwright_script(url, guest, args.request, args.dry_run))
    else:
        script = generate_playwright_script(url, guest, args.request, args.dry_run)
        script_path = Path("/tmp/chope_book.js")
        wrapped = f"async (page) => {{\n{script}\n  return await page.title();\n}}"
        script_path.write_text(wrapped)
        print(f"\nPlaywright script saved to: {script_path}")
        print(f"Execute via Playwright MCP or manually open the URL above.")


if __name__ == "__main__":
    main()
