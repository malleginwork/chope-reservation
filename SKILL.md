---
name: restaurant-reservations
description: Search availability, book, modify, and cancel restaurant reservations on Chope (Singapore). Supports availability checks, multi-date search, booking link generation, and reservation management via Gmail.
metadata: {"openclaw":{"emoji":"","primaryEnv":"","requires":{"bins":["python3"],"env":[]}}}
---

# Restaurant Reservations

Search for available tables and book restaurants via Chope (Singapore).

All scripts run from: `~/.openclaw/workspace/skills/restaurant-reservations/`

## Trigger Patterns

- "book [restaurant] for [N] on [date]"
- "find a table at [restaurant]"
- "check availability at [restaurant]"
- "any tables at [restaurant] this week"
- "cancel my [restaurant] booking"
- "modify my reservation at [restaurant]"
- "what reservations do I have"
- "restaurant reservation"
- "/reserve", "/chope", "/book-restaurant"

## How to Handle Requests

### 1. Availability Check

User asks: "Is Nobu free on Wednesday for 2?"

```bash
cd ~/.openclaw/workspace/skills/restaurant-reservations
python3 scripts/check_availability.py --restaurant nobu --date 2026-04-22 --pax 2
```

Reply with the available time slots. If none, suggest checking adjacent dates with `--days 3`.

### 2. Multi-Date Search

User asks: "Any tables at Nobu this week?"

```bash
python3 scripts/check_availability.py --restaurant nobu --date 2026-04-20 --pax 2 --days 7
```

### 3. Restaurant Search

User asks: "Find me a Japanese restaurant"

```bash
python3 scripts/check_availability.py --search "japanese" --date 2026-04-22 --pax 2
```

### 4. Booking — Send Link

User asks: "Book Nobu for 2, April 23, 7pm"

**Step 1:** Verify availability first:
```bash
python3 scripts/check_availability.py --restaurant nobu --date 2026-04-23 --pax 2
```

**Step 2:** If the requested time is available, generate booking link:
```bash
python3 scripts/book_chope.py --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 --url-only
```

**Step 3:** Send the URL to Nigel on Telegram with a message like:
"Here's the Chope booking link for Nobu, Apr 23, 7pm, 2 pax. Tap to complete:"

The link opens the form pre-filled with date/time/pax. Nigel fills name, email, phone and submits.

### 5. Complete Booking — Auto-Submit

**IMPORTANT:** If the user says "book it for me", "complete the booking", "submit it", or "I want you to book it" — they want YOU to complete the form, not just send a link.

**You CAN do this.** Run `submit_booking.py` — it uses a headless browser to fill and submit the form automatically. No MCP, no permissions, no CAPTCHA. Works in any session mode including isolated/background.

**Run this command directly (no delegation to Cowork needed):**
```bash
cd ~/.openclaw/workspace/skills/restaurant-reservations
~/.openclaw/workspace/.venv/bin/python scripts/submit_booking.py \
  --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2
```

**Or with a slug (if you already know it):**
```bash
~/.openclaw/workspace/.venv/bin/python scripts/submit_booking.py \
  --slug the-coconut-club-siglap --date 2026-04-25 --time "12:30 pm" --pax 2
```

**With special request:**
```bash
~/.openclaw/workspace/.venv/bin/python scripts/submit_booking.py \
  --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 \
  --request "Window seat please"
```

**Dry run (test without submitting):**
```bash
~/.openclaw/workspace/.venv/bin/python scripts/submit_booking.py \
  --restaurant nobu --date 2026-04-23 --time "7:00 pm" --pax 2 --dry-run
```

Exit codes: 0 = confirmed, 1 = error, 2 = dry run OK.
Screenshots saved to `/tmp/chope_booking_*.png`.
Guest details loaded from `references/guest.json` automatically.

**NEVER say "I can't complete the booking" or mention CAPTCHA or permissions.** This script handles everything.

### 6. Cancel a Booking

User asks: "Cancel my Nobu booking"

**Step 1:** Check local cache first:
```bash
cd ~/.openclaw/workspace/skills/restaurant-reservations
python3 scripts/manage_booking.py --restaurant nobu --cancel
```

If a cancel link is found, send it to Nigel on Telegram. One tap to cancel.

**Step 2:** If no local cache, search Gmail for the confirmation email:
```bash
python3 scripts/manage_booking.py --gmail-query --restaurant nobu
```
This outputs a Gmail search query. Use Gmail MCP `gmail_search_messages` with that query, then read the email to find the cancel link.

**Step 3:** Once you have the cancel URL from the email, save it and send to Nigel:
```bash
python3 scripts/manage_booking.py --add --restaurant "Nobu Singapore" --date 2026-04-23 --time "7:00 pm" --pax 2 --cancel-url "URL_FROM_EMAIL"
```

**Step 4:** After Nigel confirms cancellation, mark it locally:
```bash
python3 scripts/manage_booking.py --mark-cancelled --restaurant nobu
```

### 7. Modify a Booking

User asks: "Change my Nobu reservation to 8pm"

Same flow as cancel but use `--modify` instead of `--cancel`:
```bash
python3 scripts/manage_booking.py --restaurant nobu --modify
```

### 8. List Bookings

User asks: "What reservations do I have?"

```bash
python3 scripts/manage_booking.py --list
```

If empty, search Gmail:
```bash
python3 scripts/manage_booking.py --gmail-query
```

### 9. Save a Booking After Completing It

After Nigel completes a booking (taps the URL, fills form, submits), save the details so cancel/modify works later. If a Chope confirmation email arrives, parse it:

```bash
python3 scripts/manage_booking.py --parse-email - <<< "EMAIL_BODY_TEXT"
```

Then save the extracted info:
```bash
python3 scripts/manage_booking.py --add --restaurant "Nobu Singapore" --date 2026-04-23 --time "7:00 pm" --pax 2 --confirmation-id "CHO-ABC123" --cancel-url "URL" --modify-url "URL"
```

## Defaults

If the user doesn't specify:
- **Pax:** 2 (from `references/preferences.json`)
- **Time:** 7:00 pm
- **Date:** must be specified (don't guess)

## Available Options

```
check_availability.py:
  --restaurant, -r   Restaurant name or slug
  --search, -s       Search by keyword
  --date, -d         Date (YYYY-MM-DD)
  --pax, -p          Number of guests
  --time, -t         Preferred time (e.g. 1900 or "7:00 pm")
  --days             Check N consecutive days (default: 1)
  --json             Output as JSON

book_chope.py:
  --restaurant, -r   Restaurant name or slug (requires --date and --time)
  --url              Pre-built booking URL (skips availability check)
  --date, -d         Date (YYYY-MM-DD)
  --time, -t         Time slot (e.g. "7:00 pm") — must match available slot
  --pax, -p          Number of guests
  --request          Special request text
  --url-only         Print booking URL and exit (for sending via Telegram)
  --dry-run          Fill form but don't submit (Playwright mode)
  --script           Output Playwright JS code
  --playwright-instructions  Output step-by-step Playwright MCP instructions for agent

manage_booking.py:
  --list, -l         List all bookings (confirmed + cancelled)
  --restaurant, -r   Filter by restaurant name
  --date, -d         Filter by date
  --cancel           Show cancel link for matching booking
  --modify           Show modify link for matching booking
  --add              Save a new booking (with --restaurant, --date, --time, --pax)
  --confirmation-id  Chope reference number
  --cancel-url       URL to cancel the booking
  --modify-url       URL to modify the booking
  --mark-cancelled   Mark a booking as cancelled locally
  --gmail-query      Output Gmail search query for agent to use
  --parse-email      Parse Chope confirmation email (- for stdin)
  --json             Output as JSON
```

## Known Venues (cached)

| Name | Slug | Platform |
|------|------|----------|
| Nobu Singapore | nobu | Chope |
| Dancing Crab (Vivocity) | dancing-crab-vivocity | Chope |
| Irodori Restaurant | irodori-restaurant | Chope |
| Burnt Ends | burnt-ends | Call directly (no Chope) |

New venues are auto-cached in `references/venues.json` on first search.

## Guest Info

Pre-configured in `references/guest.json` — used for Playwright auto-fill:
- Nigel Lam, morty.pepper.potts@gmail.com, +65 91234567
