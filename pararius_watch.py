#!/usr/bin/env python3
"""
Pararius watcher: checks a Pararius search page and pushes new listings to your phone.

Run modes
  python pararius_watch.py --dry-run       fetch + print what it sees, no notifications, no state saved
  python pararius_watch.py --test-notify   send a test push to your phone
  python pararius_watch.py --save-html     save the raw search page to last_page.html (for debugging)
  python pararius_watch.py                 one check (for cron / GitHub Actions)
  python pararius_watch.py --loop 120      check forever, every ~120 s (for a laptop / server)

Configuration (environment variables)
  SEARCH_URLS     comma-separated Pararius search URLs
                  default: https://www.pararius.com/apartments/amsterdam/0-1500
  MAX_PRICE       extra safety filter in EUR (default 1500, 0 = off)
  NTFY_TOPIC      your secret ntfy topic name (install the ntfy app, subscribe to the same name)
  NTFY_SERVER     default https://ntfy.sh
  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID   optional, if you prefer Telegram
  STATE_FILE      default seen.json (next to this script)
  PAGES           how many result pages to check (default 1)
  EXCLUDE_TYPES   listing types to skip, e.g. "room" (default: keep everything)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:  # makes the request look like real Chrome at the TLS/HTTP2 level (pip install curl_cffi)
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

BASE = "https://www.pararius.com"
HERE = Path(__file__).resolve().parent

SEARCH_URLS = [u.strip() for u in (os.getenv(
    "SEARCH_URLS") or f"{BASE}/apartments/amsterdam/0-1500").split(",") if u.strip()]
MAX_PRICE = int(os.getenv("MAX_PRICE") or "1500")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_SERVER = (os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STATE_FILE = Path(os.getenv("STATE_FILE") or HERE / "seen.json")
PAGES = int(os.getenv("PAGES") or "1")
# listing types to ignore, from the URL: apartment, studio, house, room (default: keep everything)
EXCLUDE_TYPES = {t.strip() for t in (os.getenv("EXCLUDE_TYPES") or "").split(",") if t.strip()}
KEEP_DAYS = 60  # forget listings after this many days

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
}

# Listing detail URLs look like /apartment-for-rent/amsterdam/1a2b3c4d/some-street
DETAIL_RE = re.compile(r"^/(?P<type>[a-z]+)-for-rent/(?P<city>[^/]+)/(?P<id>[^/]+)(?:/|$)")
BLOCK_MARKERS = ("just a moment", "cf-chl", "captcha", "access denied",
                 "are you a robot", "verify you are human", "attention required")


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- fetching / parsing
def make_session():
    if cffi_requests and os.getenv("IMPERSONATE", "1") != "0":
        print("using curl_cffi (Chrome impersonation)")
        s = cffi_requests.Session(impersonate="chrome")
        s.headers.update({"Accept-Language": HEADERS["Accept-Language"]})
        return s
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(session, url: str) -> str:
    r = session.get(url, timeout=30)
    text = r.text or ""
    low = text[:20000].lower()
    looks_challenged = any(m in low for m in BLOCK_MARKERS) and "-for-rent/" not in text
    if r.status_code in (403, 429, 503) or looks_challenged:
        (HERE / "debug_last_page.html").write_text(text, encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        server = r.headers.get("server", "?")
        raise Blocked(f"HTTP {r.status_code} from {url} (server: {server}, "
                      f"page title: {title.group(1).strip()[:80] if title else '-'})")
    r.raise_for_status()
    return text


def page_url(url: str, page: int) -> str:
    return url if page == 1 else url.rstrip("/") + f"/page-{page}"


def _num(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_listings(html: str) -> list[dict]:
    """Return listings found on a Pararius search page. Robust to small layout changes:
    it keys on listing links (/xxx-for-rent/<city>/<id>/...) and reads text around them."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}

    def listing_ids(el) -> set[str]:
        ids = set()
        for x in el.find_all("a", href=True):
            mm = DETAIL_RE.match(urlparse(urljoin(BASE, x["href"])).path)
            if mm:
                ids.add(mm.group("id"))
        return ids

    for a in soup.find_all("a", href=True):
        path = urlparse(urljoin(BASE, a["href"])).path
        m = DETAIL_RE.match(path)
        if not m:
            continue
        lid = m.group("id")
        if lid in found:
            continue
        # The card = the largest ancestor that contains links to THIS listing only.
        # (Stops before reaching a container that also holds other listings, so details
        # from a neighbouring card can never bleed in.)
        card = a
        while card.parent is not None and card.parent.name not in ("body", "html", "[document]") \
                and listing_ids(card.parent) <= {lid}:
            card = card.parent
        text = card.get_text(" ", strip=True)

        title_el = card.select_one(".listing-search-item__title, h2, h3") or a
        title = title_el.get_text(" ", strip=True) or a.get_text(" ", strip=True)
        loc_el = card.select_one(".listing-search-item__sub-title, [class*='sub-title'], [class*='location']")
        price_el = card.select_one(".listing-search-item__price, [class*='price']")
        price_txt = price_el.get_text(" ", strip=True) if price_el else \
            (re.search(r"€\s?[\d.,]+", text).group(0) if re.search(r"€\s?[\d.,]+", text) else "")
        size = re.search(r"(\d+)\s*m²", text)
        rooms = re.search(r"(\d+)\s*rooms?\b", text)
        low = text.lower()

        found[lid] = {
            "id": lid,
            "type": m.group("type"),
            "url": urljoin(BASE, path),
            "title": title,
            "location": loc_el.get_text(" ", strip=True) if loc_el else "",
            "price": _num(price_txt.split("pcm")[0]) if price_txt else None,
            "price_text": price_txt,
            "size_m2": int(size.group(1)) if size else None,
            "rooms": int(rooms.group(1)) if rooms else None,
            "highlighted": "highlighted" in low[:80],
            "new": low.startswith("new ") or " new " in low[:40],
            "under_option": "under option" in low or "rented under" in low,
        }
    return list(found.values())


# ---------------------------------------------------------------- state
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"seen": {}, "blocked_notified": None}


def save_state(state: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- notifications
def notify(title: str, message: str, click: str | None = None, priority: int = 4) -> None:
    sent = False
    if NTFY_TOPIC:
        payload = {"topic": NTFY_TOPIC, "title": title, "message": message,
                   "priority": priority, "tags": ["house"]}
        if click:
            payload["click"] = click
            payload["actions"] = [{"action": "view", "label": "Open listing", "url": click}]
        requests.post(NTFY_SERVER, json=payload, timeout=15).raise_for_status()
        sent = True
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        text = f"*{title}*\n{message}" + (f"\n{click}" if click else "")
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                      timeout=15).raise_for_status()
        sent = True
    if not sent:
        print(f"[no notifier configured] {title} | {message} | {click or ''}")


def describe(l: dict) -> tuple[str, str]:
    bits = [l["price_text"] or (f"€{l['price']}" if l["price"] else "price ?")]
    if l["size_m2"]:
        bits.append(f"{l['size_m2']} m²")
    if l["rooms"]:
        bits.append(f"{l['rooms']} rooms")
    return l["title"], " · ".join(bits) + (f"\n{l['location']}" if l["location"] else "")


# ---------------------------------------------------------------- one check
def check(dry_run: bool = False) -> int:
    state = load_state()
    first_run = not state["seen"]
    session = make_session()
    now = datetime.now(timezone.utc).isoformat()

    listings: dict[str, dict] = {}
    try:
        for url in SEARCH_URLS:
            for p in range(1, PAGES + 1):
                html = fetch(session, page_url(url, p))
                page = parse_listings(html)
                if not page and p == 1:
                    (HERE / "debug_last_page.html").write_text(html, encoding="utf-8")
                    print(f"WARNING: 0 listings parsed from {url} – layout may have changed "
                          f"(saved debug_last_page.html)")
                for l in page:
                    if l["type"] in EXCLUDE_TYPES:
                        continue
                    listings.setdefault(l["id"], l)
                if p < PAGES:
                    time.sleep(random.uniform(2, 5))
    except Blocked as e:
        print(f"BLOCKED: {e}")
        # tell the phone once per day max, so you know the watcher is not working
        last = state.get("blocked_notified")
        if not dry_run and (not last or last < (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()):
            notify("Pararius watcher is being blocked", str(e), priority=3)
            state["blocked_notified"] = now
            save_state(state)
        return 2

    if dry_run:
        print(f"Found {len(listings)} listings:")
        for l in listings.values():
            flag = ("NEW " if l["new"] else "") + ("AD " if l["highlighted"] else "") + \
                   ("OPTION " if l["under_option"] else "")
            print(f"  {flag:<11}{l['price_text']:<14}{str(l['size_m2'] or '?'):>4} m²  "
                  f"{l['title']}  -> {l['url']}")
        return 0

    new = [l for lid, l in listings.items() if lid not in state["seen"]]
    for l in listings.values():
        state["seen"].setdefault(l["id"], now)

    if first_run:
        print(f"First run: remembered {len(listings)} existing listings, no alerts sent.")
        if listings:
            notify("Pararius watcher is running",
                   f"Watching {len(SEARCH_URLS)} search(es); {len(listings)} current listings ignored. "
                   f"You'll get a push for every new one.", priority=2)
    else:
        for l in new:
            if l["under_option"]:
                continue
            if MAX_PRICE and l["price"] and l["price"] > MAX_PRICE:
                continue
            title, msg = describe(l)
            notify(f"New: {title}", msg, click=l["url"])
            print(f"NEW -> {title} | {msg.splitlines()[0]} | {l['url']}")
        if not new:
            print(f"{datetime.now():%H:%M:%S} no new listings ({len(listings)} on page)")

    state["blocked_notified"] = None
    save_state(state)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-notify", action="store_true")
    ap.add_argument("--save-html", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="keep checking every N seconds")
    args = ap.parse_args()

    if args.test_notify:
        notify("Pararius watcher test", "If you see this, notifications work 🎉",
               click=SEARCH_URLS[0])
        print("Test notification sent.")
        return

    if args.save_html:
        html = fetch(make_session(), SEARCH_URLS[0])
        (HERE / "last_page.html").write_text(html, encoding="utf-8")
        print(f"Saved {len(html):,} characters to {HERE / 'last_page.html'}")
        return

    if not args.loop:
        sys.exit(check(args.dry_run))

    while True:
        try:
            check(args.dry_run)
        except Exception as e:  # keep the loop alive through network hiccups
            print(f"error: {e!r}")
        # jitter so requests don't look perfectly robotic
        time.sleep(max(30, args.loop + random.uniform(-0.2, 0.2) * args.loop))


if __name__ == "__main__":
    main()
