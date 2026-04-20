#!/usr/bin/env python3
"""
Submit a Chope booking using headless Playwright (no MCP needed).

Navigates to the restaurant page on chope.co, clicks through the booking
modal, fills the contact form from guest.json, and submits. Runs as a
regular shell command — no interactive permissions required.

Usage:
  # Submit a booking
  python3 submit_booking.py --slug the-coconut-club-siglap --date 2026-04-25 \
    --time "12:30 pm" --pax 2

  # With special request
  python3 submit_booking.py --slug nobu --date 2026-04-23 --time "7:00 pm" \
    --pax 2 --request "Window seat please"

  # Dry run — fill form but don't click Confirm Booking
  python3 submit_booking.py --slug nobu --date 2026-04-23 --time "7:00 pm" \
    --pax 2 --dry-run

  # With restaurant name lookup (resolves slug automatically)
  python3 submit_booking.py --restaurant nobu --date 2026-04-23 \
    --time "7:00 pm" --pax 2

Exit codes:
  0 = booking submitted successfully
  1 = error
  2 = dry run completed
"""

import argparse
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

SKILL_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(SKILL_DIR / "scripts"))
from check_availability import resolve_rid, load_prefs, load_venues


def load_guest_info() -> dict:
    prefs_path = SKILL_DIR / "references" / "guest.json"
    if prefs_path.exists():
        return json.loads(prefs_path.read_text())
    return {}


def resolve_slug(restaurant: str) -> str | None:
    """Resolve restaurant name to Chope slug."""
    venues = load_venues()
    key = restaurant.lower().replace(" ", "-")

    # Direct match
    if key in venues and isinstance(venues[key], dict):
        return venues[key].get("slug", key)

    # Search by name
    for k, v in venues.items():
        if not isinstance(v, dict):
            continue
        if restaurant.lower() in v.get("name", "").lower() or restaurant.lower() in k:
            return v.get("slug", k)

    # Fall back to slug-ified name
    return key


def format_date_for_url(date_str: str) -> str:
    """Convert YYYY-MM-DD to DD-MM-YYYY for Chope restaurant page URL."""
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d-%m-%Y")


def submit_booking(slug: str, date: str, time: str, pax: int,
                   guest: dict, request: str = None,
                   children: int = 0, dry_run: bool = False) -> dict:
    """
    Full booking flow:
    1. Open restaurant page with date/time/pax params
    2. Click "Book Now" -> opens policy modal
    3. Click "Book Now" in modal -> opens booking form popup
    4. Fill contact details from guest.json
    5. Check T&C, click "Confirm Booking"
    """
    result = {"status": "error", "restaurant": slug}
    url_date = format_date_for_url(date)
    restaurant_url = (
        f"https://www.chope.co/singapore-restaurants/restaurant/{slug}"
        f"?adults={pax}&children={children}&date={url_date}&time={time}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        try:
            # Step 1: Load restaurant page
            print(f"Opening restaurant page...")
            page.goto(restaurant_url, timeout=30000)
            page.wait_for_timeout(4000)
            title = page.title()
            print(f"  {title}")
            result["restaurant_name"] = title.replace(" - Book and Save On Chope", "")

            # Step 2: Click Book Now on page
            print(f"Clicking Book Now...")
            book_now = page.locator("text=Book Now").first
            book_now.wait_for(state="visible", timeout=10000)
            book_now.click()
            page.wait_for_timeout(3000)

            # Step 3: Click Book Now in modal -> opens popup
            print(f"Confirming in policy modal...")
            with ctx.expect_page(timeout=15000) as popup_info:
                page.locator('button:has-text("Book Now")').last.click()

            bp = popup_info.value
            bp.wait_for_load_state()
            bp.wait_for_timeout(5000)
            print(f"  Booking form loaded: {bp.title()}")

            # Step 4: Detect form type and fill
            # Two Chope form variants:
            #   book.chope.co (older): input[name=forename/surname/email/mobile]
            #   booking.chope.co (newer widget): input[placeholder="First Name"/etc]
            print(f"Filling contact details...")

            is_old_form = bp.locator("input[name=forename]").count() > 0

            if is_old_form:
                print(f"  Form type: book.chope.co (classic)")
                bp.locator("input[name=forename]").fill(guest["first_name"])
                bp.locator("input[name=surname]").fill(guest["last_name"])
                bp.locator("input[name=email]").fill(guest["email"])
                bp.locator("input[name=mobile]").fill(guest["phone"])
            else:
                print(f"  Form type: booking.chope.co (widget)")
                bp.locator('input[placeholder="First Name"]').fill(guest["first_name"])
                bp.locator('input[placeholder="Last Name"]').fill(guest["last_name"])
                bp.locator('input[placeholder="Email address"]').fill(guest["email"])
                bp.locator('input[placeholder="Mobile number"]').fill(guest["phone"])

            print(f"  {guest['first_name']} {guest['last_name']}")
            print(f"  {guest['email']}")
            print(f"  {guest.get('country_code', '+65')} {guest['phone']}")

            # Special request
            if request:
                print(f"  Request: {request}")
                if is_old_form:
                    msg_field = bp.locator('textarea, input[placeholder*="Message"]').first
                    if msg_field.is_visible():
                        msg_field.fill(request)
                else:
                    # Widget form: click "Add" under Special requests, fill textbox
                    sr_add = bp.locator("text=Special requests").locator("..").locator("text=Add")
                    if sr_add.count() > 0 and sr_add.is_visible():
                        sr_add.click()
                        bp.wait_for_timeout(500)
                    resp_field = bp.locator('input[placeholder="Enter your response"], textarea')
                    if resp_field.count() > 0:
                        resp_field.first.fill(request)
                    else:
                        print(f"  Warning: could not find request field")

            # Step 5: Check T&C / policy checkbox
            if is_old_form:
                tc = bp.locator("input[name=agreee_terms_conditions]")
                if tc.count() > 0:
                    tc.check()
                else:
                    bp.locator("input[type=checkbox]:visible").last.check()
            else:
                # Widget: last checkbox is restaurant policy
                bp.locator("input[type=checkbox]:visible").last.check()
            print(f"  T&C accepted")

            bp.wait_for_timeout(500)

            if dry_run:
                bp.screenshot(path="/tmp/chope_booking_dryrun.png")
                print(f"\nDRY RUN — form filled, not submitting.")
                print(f"Screenshot: /tmp/chope_booking_dryrun.png")
                result["status"] = "dry_run"
                result["screenshot"] = "/tmp/chope_booking_dryrun.png"
                return result

            # Step 6: Submit
            print(f"Submitting booking...")
            if is_old_form:
                confirm_btn = bp.locator('button:has-text("Confirm Booking")')
            else:
                confirm_btn = bp.locator('button:has-text("Book table")')
            confirm_btn.wait_for(state="visible", timeout=5000)
            confirm_btn.click()
            bp.wait_for_timeout(8000)

            # Check result
            bp.screenshot(path="/tmp/chope_booking_result.png")
            page_text = bp.inner_text("body").lower()

            if any(kw in page_text for kw in ["confirmed", "confirmation", "thank you", "success"]):
                print(f"\nBOOKING CONFIRMED!")
                result["status"] = "confirmed"

                # Extract confirmation number
                import re
                conf_match = re.search(
                    r'(?:confirmation|reference|booking)\s*(?:#|number|:)?\s*([A-Z0-9-]+)',
                    bp.inner_text("body"), re.IGNORECASE
                )
                if conf_match:
                    result["confirmation_id"] = conf_match.group(1)
                    print(f"  Ref: {conf_match.group(1)}")
            else:
                result["status"] = "submitted"
                print(f"\nBooking submitted — check screenshot for confirmation.")

            result["screenshot"] = "/tmp/chope_booking_result.png"
            print(f"Screenshot: /tmp/chope_booking_result.png")

        except PwTimeout as e:
            result["error"] = f"Timeout: {e}"
            print(f"\nError: Timeout — {e}")
            try:
                page.screenshot(path="/tmp/chope_booking_error.png")
                result["screenshot"] = "/tmp/chope_booking_error.png"
            except Exception:
                pass
        except Exception as e:
            result["error"] = str(e)
            print(f"\nError: {e}")
            try:
                page.screenshot(path="/tmp/chope_booking_error.png")
                result["screenshot"] = "/tmp/chope_booking_error.png"
            except Exception:
                pass
        finally:
            browser.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="Submit a Chope booking (headless)")
    parser.add_argument("--slug", help="Restaurant slug on Chope (e.g. 'nobu', 'the-coconut-club-siglap')")
    parser.add_argument("--restaurant", "-r", help="Restaurant name (auto-resolves to slug)")
    parser.add_argument("--date", "-d", required=True, help="Date (YYYY-MM-DD)")
    parser.add_argument("--time", "-t", required=True, help="Time slot (e.g. '7:00 pm', '12:30 pm')")
    parser.add_argument("--pax", "-p", type=int, help="Number of guests")
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--request", help="Special request / message")
    parser.add_argument("--dry-run", action="store_true", help="Fill form but don't submit")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    # Load guest info
    guest = load_guest_info()
    if not guest.get("first_name"):
        print("Error: references/guest.json not configured")
        sys.exit(1)

    # Resolve slug
    if args.slug:
        slug = args.slug
    elif args.restaurant:
        slug = resolve_slug(args.restaurant)
        print(f"Resolved '{args.restaurant}' → slug: {slug}")
    else:
        parser.error("Either --slug or --restaurant is required")

    prefs = load_prefs()
    pax = args.pax or prefs["default_pax"]

    # Print summary
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Chope Booking")
    print(f"{'=' * 50}")
    print(f"Restaurant: {slug}")
    print(f"Date:       {args.date}")
    print(f"Time:       {args.time}")
    print(f"Guests:     {pax}")
    print(f"Guest:      {guest['first_name']} {guest['last_name']}")
    print(f"{'=' * 50}\n")

    # Submit
    result = submit_booking(slug, args.date, args.time, pax, guest,
                            args.request, args.children, args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))

    if result["status"] in ("confirmed", "submitted"):
        sys.exit(0)
    elif result["status"] == "dry_run":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
