#!/usr/bin/env python3
"""
Check restaurant availability on Chope.

Uses Chope's internal API to fetch available time slots without a browser.

Usage:
  # Check specific restaurant
  python3 check_availability.py --restaurant nobu --date 2026-04-22 --pax 2

  # Check with preferred time (shows nearest slots)
  python3 check_availability.py --restaurant nobu --date 2026-04-22 --pax 2 --time 1900

  # Search by name (scrapes restaurant page to get rid)
  python3 check_availability.py --search "nobu" --date 2026-04-22 --pax 2

  # Check multiple dates
  python3 check_availability.py --restaurant nobu --date 2026-04-22 --days 3 --pax 2

  # Output as JSON
  python3 check_availability.py --restaurant nobu --date 2026-04-22 --pax 2 --json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent
PREFS_PATH = SKILL_DIR / "references" / "preferences.json"
VENUES_PATH = SKILL_DIR / "references" / "venues.json"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.chope.co/singapore-restaurants",
})


def load_prefs() -> dict:
    if PREFS_PATH.exists():
        return json.loads(PREFS_PATH.read_text())
    return {"default_pax": 2, "default_children": 0, "default_time": "1900",
            "country": "singapore", "base_url": "https://www.chope.co/singapore-restaurants"}


def load_venues() -> dict:
    if VENUES_PATH.exists():
        return json.loads(VENUES_PATH.read_text())
    return {}


def save_venues(venues: dict):
    VENUES_PATH.write_text(json.dumps(venues, indent=2, ensure_ascii=False))


class OptionParser(HTMLParser):
    """Parse <option> tags from Chope's get_times response."""
    def __init__(self):
        super().__init__()
        self.slots = []
        self._in_option = False
        self._current = {}

    def handle_starttag(self, tag, attrs):
        if tag == "option":
            self._in_option = True
            self._current = dict(attrs)

    def handle_data(self, data):
        if self._in_option:
            text = data.strip()
            if text and text != "Not Available":
                self._current["text"] = text
                self.slots.append(self._current)
            self._in_option = False

    def handle_endtag(self, tag):
        if tag == "option":
            self._in_option = False


def get_rid_from_page(slug: str, base_url: str) -> str | None:
    """Fetch restaurant page and extract the rid (restaurant ID)."""
    url = f"{base_url}/restaurant/{slug}"
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
        match = re.search(r'id="rid"\s+value="([^"]+)"', resp.text)
        if match:
            return match.group(1)
        # Also try data attribute
        match = re.search(r'data-rid="([^"]+)"', resp.text)
        if match:
            return match.group(1)
    except requests.exceptions.HTTPError:
        pass  # 404 = restaurant not found, expected during slug search
    except Exception as e:
        print(f"Error fetching page for {slug}: {e}", file=sys.stderr)
    return None


def search_restaurant(query: str, base_url: str) -> list[dict]:
    """
    Search for restaurants by trying the query as a slug, then directory search.
    """
    # Check venue cache first
    venues = load_venues()
    query_lower = query.lower()
    for key, v in venues.items():
        if not isinstance(v, dict):
            continue
        name = v.get("name", "").lower()
        if query_lower in name or query_lower in key:
            if v.get("rid"):
                return [{"name": v["name"], "slug": v.get("slug", key)}]

    # Normalise query to slug
    slug = re.sub(r'[^a-z0-9]+', '-', query_lower).strip('-')

    # Try the slug directly
    rid = get_rid_from_page(slug, base_url)
    if rid:
        return [{"name": query.title(), "slug": slug}]

    # Try slug prefix variations (e.g. "wang dae bak" -> "wang-dae-bak-*")
    parts = slug.split("-")
    if len(parts) > 1:
        for i in range(len(parts), 0, -1):
            partial = "-".join(parts[:i])
            rid = get_rid_from_page(partial, base_url)
            if rid:
                return [{"name": query.title(), "slug": partial}]

    # Fallback: search the directory listing page for matching slugs
    try:
        resp = SESSION.get(f"{base_url}/list_of_restaurants", timeout=15)
        resp.raise_for_status()
        matches = []
        for m in re.finditer(r'/restaurant/([^"/?#]+)', resp.text):
            s = m.group(1)
            if all(p in s for p in parts):
                matches.append(s)
        seen = set()
        for s in matches:
            if s not in seen:
                seen.add(s)
                rid = get_rid_from_page(s, base_url)
                if rid:
                    return [{"name": s.replace("-", " ").title(), "slug": s}]
    except Exception:
        pass

    return []


def resolve_rid(restaurant: str, base_url: str) -> tuple[str | None, str]:
    """Resolve restaurant name/slug to rid. Returns (rid, slug)."""
    venues = load_venues()

    # Check venue cache first
    key = restaurant.lower().replace(" ", "-")
    if key in venues and venues[key].get("rid"):
        return venues[key]["rid"], venues[key].get("slug", key)

    # Try as slug directly
    slug = key
    rid = get_rid_from_page(slug, base_url)
    if rid:
        # Cache it
        venues[key] = venues.get(key, {})
        venues[key]["rid"] = rid
        venues[key]["slug"] = slug
        venues[key]["platform"] = "chope"
        save_venues(venues)
        return rid, slug

    # Try search
    results = search_restaurant(restaurant, base_url)
    if results:
        slug = results[0]["slug"]
        rid = get_rid_from_page(slug, base_url)
        if rid:
            venues[key] = {
                "rid": rid,
                "name": results[0]["name"],
                "slug": slug,
                "platform": "chope",
            }
            save_venues(venues)
            return rid, slug

    return None, key


def get_available_times(rid: str, date: str, adults: int, children: int,
                        base_url: str, preferred_time: str = None) -> list[dict]:
    """
    Fetch available time slots from Chope's internal API.

    Args:
        rid: Restaurant ID (e.g. 'nobu2212sg')
        date: Date in DD-M-YYYY format (Chope's format)
        adults: Number of adults
        children: Number of children
        base_url: Chope base URL
        preferred_time: Optional preferred time (e.g. '1900')

    Returns:
        List of dicts with 'text' (display time) and optional attributes
    """
    url = f"{base_url}/categories/get_times"
    data = {
        "rid": rid,
        "date": date,
        "adults": adults,
        "children": children,
        "reservation_id": 0,
    }
    if preferred_time:
        data["selected_time"] = preferred_time

    try:
        resp = SESSION.post(url, data=data, timeout=15)
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") == 200:
            parser = OptionParser()
            parser.feed(result["data"])
            return parser.slots
        else:
            return []
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        return []


def format_date_for_chope(date_str: str) -> str:
    """Convert YYYY-MM-DD to DD-M-YYYY (Chope's internal format)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day}-{dt.month}-{dt.year}"


def format_output(restaurant: str, date: str, pax: int, slots: list[dict],
                   preferred_time: str = None) -> str:
    """Format availability results for display."""
    dt = datetime.strptime(date, "%Y-%m-%d")
    day_name = dt.strftime("%A")

    lines = [f"\n{restaurant} — {day_name} {date} — {pax} pax"]
    if not slots:
        lines.append("  No availability")
        return "\n".join(lines)

    lines.append(f"  {len(slots)} slots available:")
    for s in slots:
        marker = ""
        if preferred_time:
            # Highlight slots near preferred time
            slot_text = s["text"].lower().replace(".", "")
            # Simple proximity check
            marker = ""
        lines.append(f"    {s['text']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Check Chope restaurant availability")
    parser.add_argument("--restaurant", "-r", help="Restaurant name or slug")
    parser.add_argument("--search", "-s", help="Search query (finds restaurants)")
    parser.add_argument("--date", "-d", help="Date (YYYY-MM-DD), default: tomorrow")
    parser.add_argument("--pax", "-p", type=int, help="Number of guests")
    parser.add_argument("--children", type=int, default=0, help="Number of children")
    parser.add_argument("--time", "-t", help="Preferred time (e.g. 1900, '7:00 pm')")
    parser.add_argument("--days", type=int, default=1, help="Check N consecutive days")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    prefs = load_prefs()
    base_url = prefs["base_url"]

    # Defaults
    pax = args.pax or prefs["default_pax"]
    children = args.children or prefs["default_children"]
    preferred_time = args.time or prefs.get("default_time")

    if not args.date:
        tomorrow = datetime.now() + timedelta(days=1)
        date_str = tomorrow.strftime("%Y-%m-%d")
    else:
        date_str = args.date

    # Search mode
    if args.search:
        results = search_restaurant(args.search, base_url)
        if not results:
            print(f"No restaurants found for '{args.search}'")
            sys.exit(1)

        print(f"\nSearch: '{args.search}' — {len(results)} results\n")
        for i, r in enumerate(results, 1):
            print(f"  {i}. {r['name']} (slug: {r['slug']})")

        # Check availability for first result
        if results:
            print(f"\nChecking availability for: {results[0]['name']}...")
            rid = get_rid_from_page(results[0]["slug"], base_url)
            if rid:
                chope_date = format_date_for_chope(date_str)
                slots = get_available_times(rid, chope_date, pax, children,
                                            base_url, preferred_time)
                print(format_output(results[0]["name"], date_str, pax, slots, preferred_time))
            else:
                print("  Restaurant not accepting online reservations")
        sys.exit(0)

    # Direct restaurant lookup
    if not args.restaurant:
        parser.print_help()
        sys.exit(1)

    rid, slug = resolve_rid(args.restaurant, base_url)
    if not rid:
        print(f"Could not find restaurant '{args.restaurant}' on Chope")
        print("Try: --search \"restaurant name\" to find it")
        sys.exit(1)

    all_results = []
    for day_offset in range(args.days):
        dt = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=day_offset)
        check_date = dt.strftime("%Y-%m-%d")
        chope_date = format_date_for_chope(check_date)

        slots = get_available_times(rid, chope_date, pax, children,
                                    base_url, preferred_time)

        if args.json:
            all_results.append({
                "date": check_date,
                "day": dt.strftime("%A"),
                "pax": pax,
                "slots": [s["text"] for s in slots],
                "count": len(slots),
            })
        else:
            venues = load_venues()
            name = args.restaurant
            for v in venues.values():
                if not isinstance(v, dict):
                    continue
                if v.get("slug") == slug or v.get("rid") == rid:
                    name = v.get("name", args.restaurant)
                    break
            print(format_output(name, check_date, pax, slots, preferred_time))

    if args.json:
        print(json.dumps({
            "restaurant": args.restaurant,
            "rid": rid,
            "results": all_results,
        }, indent=2))


if __name__ == "__main__":
    main()
