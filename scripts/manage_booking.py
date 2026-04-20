#!/usr/bin/env python3
"""
Manage Chope reservations — find, modify, or cancel existing bookings.

Searches Gmail for Chope confirmation emails and extracts manage/cancel links.
Designed to be called by OpenClaw (Morty) or Claude Cowork.

Usage:
  # List recent Chope bookings
  python3 manage_booking.py --list

  # Find a specific booking
  python3 manage_booking.py --restaurant nobu

  # Find bookings for a specific date
  python3 manage_booking.py --date 2026-04-23

  # Get cancel link for a booking
  python3 manage_booking.py --restaurant nobu --cancel

  # Get modify link for a booking
  python3 manage_booking.py --restaurant nobu --modify

Note: This script outputs structured info. The actual Gmail search must be
performed by the calling agent (Morty/Cowork) via Gmail MCP, since this script
cannot authenticate to Gmail directly. See SKILL.md for the full flow.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
BOOKINGS_PATH = SKILL_DIR / "references" / "bookings.json"


def load_bookings() -> list[dict]:
    """Load cached bookings from local store."""
    if BOOKINGS_PATH.exists():
        data = json.loads(BOOKINGS_PATH.read_text())
        if isinstance(data, list):
            return data
    return []


def save_bookings(bookings: list[dict]):
    """Save bookings to local store."""
    BOOKINGS_PATH.write_text(json.dumps(bookings, indent=2, ensure_ascii=False))


def add_booking(restaurant: str, date: str, time: str, pax: int,
                confirmation_id: str = None, manage_url: str = None,
                cancel_url: str = None, modify_url: str = None,
                email_subject: str = None) -> dict:
    """Add or update a booking in the local store."""
    bookings = load_bookings()

    booking = {
        "restaurant": restaurant,
        "date": date,
        "time": time,
        "pax": pax,
        "confirmation_id": confirmation_id,
        "manage_url": manage_url,
        "cancel_url": cancel_url,
        "modify_url": modify_url,
        "email_subject": email_subject,
        "status": "confirmed",
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # Update existing if same restaurant + date + time
    for i, b in enumerate(bookings):
        if (b.get("restaurant", "").lower() == restaurant.lower()
                and b.get("date") == date
                and b.get("time") == time):
            bookings[i] = booking
            save_bookings(bookings)
            return booking

    bookings.append(booking)
    save_bookings(bookings)
    return booking


def find_bookings(restaurant: str = None, date: str = None,
                  status: str = "confirmed") -> list[dict]:
    """Find bookings matching criteria."""
    bookings = load_bookings()
    results = []

    for b in bookings:
        if status and b.get("status") != status:
            continue
        if restaurant and restaurant.lower() not in b.get("restaurant", "").lower():
            continue
        if date and b.get("date") != date:
            continue
        results.append(b)

    # Sort by date
    results.sort(key=lambda b: b.get("date", ""))
    return results


def cancel_booking(restaurant: str, date: str = None) -> dict | None:
    """Mark a booking as cancelled. Returns the booking if found."""
    bookings = load_bookings()

    for b in bookings:
        if restaurant.lower() not in b.get("restaurant", "").lower():
            continue
        if date and b.get("date") != date:
            continue
        if b.get("status") == "confirmed":
            b["status"] = "cancelled"
            b["cancelled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            save_bookings(bookings)
            return b

    return None


def parse_chope_email(email_body: str) -> dict:
    """
    Parse a Chope confirmation email body to extract booking details and links.

    Chope confirmation emails typically contain:
    - Restaurant name
    - Date and time
    - Party size
    - Confirmation/reference number
    - Manage/modify/cancel links (URLs to booking.chope.co)

    Returns dict with extracted fields.
    """
    result = {}

    # Extract confirmation/reference number
    conf_match = re.search(r'(?:confirmation|reference|booking)\s*(?:#|number|id|:)\s*([A-Z0-9-]+)',
                           email_body, re.IGNORECASE)
    if conf_match:
        result["confirmation_id"] = conf_match.group(1)

    # Extract manage/cancel/modify URLs
    # Chope uses booking.chope.co for management links
    urls = re.findall(r'https?://booking\.chope\.co/[^\s<>"\']+', email_body)
    for url in urls:
        url_lower = url.lower()
        if "cancel" in url_lower:
            result["cancel_url"] = url
        elif "modify" in url_lower or "edit" in url_lower or "change" in url_lower:
            result["modify_url"] = url
        elif "manage" in url_lower or "view" in url_lower:
            result["manage_url"] = url

    # Also check for chope.co links (non-booking subdomain)
    chope_urls = re.findall(r'https?://(?:www\.)?chope\.co/[^\s<>"\']+', email_body)
    for url in chope_urls:
        url_lower = url.lower()
        if "cancel" in url_lower:
            result.setdefault("cancel_url", url)
        elif "modify" in url_lower or "manage" in url_lower:
            result.setdefault("manage_url", url)

    # Generic URL fallback — any link with reservation management keywords
    all_urls = re.findall(r'https?://[^\s<>"\']+', email_body)
    for url in all_urls:
        url_lower = url.lower()
        if "chope" in url_lower:
            if "cancel" in url_lower:
                result.setdefault("cancel_url", url)
            elif "modify" in url_lower or "edit" in url_lower:
                result.setdefault("modify_url", url)
            elif "manage" in url_lower or "reservation" in url_lower:
                result.setdefault("manage_url", url)

    # Extract restaurant name from email
    rest_match = re.search(r'(?:reservation at|booking at|table at)\s+(.+?)(?:\s+on|\s+for|\.|$)',
                           email_body, re.IGNORECASE)
    if rest_match:
        result["restaurant"] = rest_match.group(1).strip()

    # Extract date
    date_match = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4})',
                           email_body, re.IGNORECASE)
    if date_match:
        result["date_text"] = date_match.group(1)

    # Extract pax
    pax_match = re.search(r'(\d+)\s*(?:guest|pax|person|people|adult)', email_body, re.IGNORECASE)
    if pax_match:
        result["pax"] = int(pax_match.group(1))

    return result


def format_booking(b: dict) -> str:
    """Format a booking for display."""
    lines = []
    rest = b.get("restaurant", "Unknown")
    date = b.get("date", "?")
    time = b.get("time", "?")
    pax = b.get("pax", "?")
    status = b.get("status", "?")

    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        day_name = dt.strftime("%A")
        date_display = f"{date} ({day_name})"
    except (ValueError, TypeError):
        date_display = date

    lines.append(f"  {rest}")
    lines.append(f"    Date:   {date_display}")
    lines.append(f"    Time:   {time}")
    lines.append(f"    Pax:    {pax}")
    lines.append(f"    Status: {status}")

    if b.get("confirmation_id"):
        lines.append(f"    Ref:    {b['confirmation_id']}")
    if b.get("manage_url"):
        lines.append(f"    Manage: {b['manage_url']}")
    if b.get("cancel_url"):
        lines.append(f"    Cancel: {b['cancel_url']}")
    if b.get("modify_url"):
        lines.append(f"    Modify: {b['modify_url']}")

    return "\n".join(lines)


def gmail_search_instructions(restaurant: str = None, date: str = None) -> str:
    """
    Generate Gmail search query for finding Chope confirmation emails.
    To be used by the calling agent with Gmail MCP.
    """
    query_parts = ["from:chope", "(confirmation OR booking OR reservation)"]
    if restaurant:
        query_parts.append(f'"{restaurant}"')
    if date:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            query_parts.append(f'after:{dt.strftime("%Y/%m/%d")}')
        except ValueError:
            pass

    return " ".join(query_parts)


def main():
    parser = argparse.ArgumentParser(description="Manage Chope bookings")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List all confirmed bookings")
    parser.add_argument("--restaurant", "-r", help="Filter by restaurant name")
    parser.add_argument("--date", "-d", help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--cancel", action="store_true",
                        help="Show cancel link for matching booking")
    parser.add_argument("--modify", action="store_true",
                        help="Show modify link for matching booking")
    parser.add_argument("--gmail-query", action="store_true",
                        help="Output Gmail search query (for agent use)")
    parser.add_argument("--add", action="store_true",
                        help="Add a booking (requires --restaurant, --date, --time, --pax)")
    parser.add_argument("--time", "-t", help="Booking time")
    parser.add_argument("--pax", "-p", type=int, help="Party size")
    parser.add_argument("--confirmation-id", help="Confirmation/reference number")
    parser.add_argument("--manage-url", help="Manage booking URL")
    parser.add_argument("--cancel-url", help="Cancel booking URL")
    parser.add_argument("--modify-url", help="Modify booking URL")
    parser.add_argument("--mark-cancelled", action="store_true",
                        help="Mark a booking as cancelled locally")
    parser.add_argument("--parse-email", help="Parse email body from file (- for stdin)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Gmail query mode — output search query for agent
    if args.gmail_query:
        query = gmail_search_instructions(args.restaurant, args.date)
        print(query)
        return

    # Parse email mode
    if args.parse_email:
        if args.parse_email == "-":
            body = sys.stdin.read()
        else:
            body = Path(args.parse_email).read_text()
        result = parse_chope_email(body)
        print(json.dumps(result, indent=2))
        return

    # Add booking
    if args.add:
        if not all([args.restaurant, args.date, args.time]):
            print("--add requires --restaurant, --date, --time")
            sys.exit(1)
        booking = add_booking(
            restaurant=args.restaurant,
            date=args.date,
            time=args.time,
            pax=args.pax or 2,
            confirmation_id=args.confirmation_id,
            manage_url=args.manage_url,
            cancel_url=args.cancel_url,
            modify_url=args.modify_url,
        )
        print(f"Booking saved:")
        print(format_booking(booking))
        return

    # Mark cancelled
    if args.mark_cancelled:
        if not args.restaurant:
            print("--mark-cancelled requires --restaurant")
            sys.exit(1)
        b = cancel_booking(args.restaurant, args.date)
        if b:
            print(f"Marked as cancelled:")
            print(format_booking(b))
        else:
            print(f"No confirmed booking found for '{args.restaurant}'")
            sys.exit(1)
        return

    # List / search bookings
    bookings = find_bookings(
        restaurant=args.restaurant,
        date=args.date,
        status="confirmed" if not args.list else None,
    )

    if not bookings:
        print("No bookings found.")
        if not BOOKINGS_PATH.exists():
            print("\nNo bookings cached yet. To find bookings from email, the agent should:")
            print(f"  1. Search Gmail: {gmail_search_instructions(args.restaurant, args.date)}")
            print("  2. Read the confirmation email")
            print("  3. Save with: python3 manage_booking.py --add --restaurant NAME --date DATE --time TIME --cancel-url URL")
        sys.exit(0)

    if args.json:
        print(json.dumps(bookings, indent=2))
        return

    if args.cancel:
        for b in bookings:
            if b.get("cancel_url"):
                print(f"{b['restaurant']} — {b['date']} {b['time']}")
                print(f"Cancel link: {b['cancel_url']}")
            else:
                print(f"{b['restaurant']} — {b['date']} {b['time']}")
                print("No cancel link stored. Agent should search Gmail:")
                print(f"  {gmail_search_instructions(b.get('restaurant'), b.get('date'))}")
        return

    if args.modify:
        for b in bookings:
            if b.get("modify_url") or b.get("manage_url"):
                url = b.get("modify_url") or b.get("manage_url")
                print(f"{b['restaurant']} — {b['date']} {b['time']}")
                print(f"Modify link: {url}")
            else:
                print(f"{b['restaurant']} — {b['date']} {b['time']}")
                print("No modify link stored. Agent should search Gmail:")
                print(f"  {gmail_search_instructions(b.get('restaurant'), b.get('date'))}")
        return

    # Default: list
    print(f"\n{'Confirmed' if not args.list else 'All'} Bookings ({len(bookings)}):\n")
    for b in bookings:
        print(format_booking(b))
        print()


if __name__ == "__main__":
    main()
