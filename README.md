# Pararius watcher

Checks your Pararius search every ~2 minutes and sends a push to your phone for every new listing.
Runs on your own laptop (Pararius blocks cloud servers such as GitHub Actions with a Cloudflare check).

## One-time setup
1. Install the **ntfy** app on your phone, tap **+**, subscribe to a long random topic name
   (e.g. `lukas-ams-9f3k2q`). Anyone who knows the name can read it, so keep it private.
2. In this folder:
   ```
   pip install -r requirements.txt
   ```
3. Copy `settings.example.env` to `settings.env` and put your topic in it:
   ```
   NTFY_TOPIC=lukas-ams-9f3k2q
   ```
   (`settings.env` is git-ignored, so your topic never ends up on GitHub.)
4. Check that pushes arrive:
   ```
   python pararius_watch.py --test-notify
   ```

## Run it
- Windows: double-click **`start_watcher.bat`** (or run `python pararius_watch.py --loop 120`).
- Leave the window open. While it runs, Windows won't go to sleep on its own,
  but **closing the lid still puts it to sleep**, so leave the lid open and the charger in.

The first check only remembers what's already online (one "watcher is running" push).
After that you get one push per new listing, with a button that opens it.

## Handy commands
```
python pararius_watch.py --dry-run     # show what it currently sees, no pushes
python pararius_watch.py --save-html   # save the raw page to last_page.html (debugging)
git pull                               # get updates
```

## Changing the search
Set filters on pararius.com, copy the URL, and add it to `settings.env`:
```
SEARCH_URLS=https://www.pararius.com/apartments/amsterdam/0-1500
MAX_PRICE=1500
EXCLUDE_TYPES=room        # optional: skip rooms
```
Results are sorted "Newest first" by default, so checking page 1 is enough.
