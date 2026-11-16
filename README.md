# Pararius watcher

Checks your Pararius search and sends a push to your phone for every new listing.

## 1. Phone setup (2 min)
1. Install the **ntfy** app (Android / iOS).
2. Tap **+** and subscribe to a topic with a long random name, e.g. `lukas-ams-flat-x7k29q`.
   Anyone who knows the name can read it, so don't use something guessable.

## 2. Test on your laptop
```bash
pip install -r requirements.txt
python pararius_watch.py --dry-run                              # does Pararius let us in? what does it see?
NTFY_TOPIC=lukas-ams-flat-x7k29q python pararius_watch.py --test-notify   # does the push arrive?
```
(Windows PowerShell: `$env:NTFY_TOPIC="lukas-ams-flat-x7k29q"; python pararius_watch.py --test-notify`)

Keep it running (laptop must stay awake – sleep/standby pauses it):
```bash
NTFY_TOPIC=lukas-ams-flat-x7k29q python pararius_watch.py --loop 120
```
The first check only remembers what's already online (one "watcher is running" push). After that, you get one push per new listing.

## 3. Test on GitHub Actions (tells us if datacenter IPs get blocked)
1. Create a new repo on github.com and upload all files, including `.github/workflows/watch.yml`.
2. Repo **Settings → Secrets and variables → Actions**: add secret `NTFY_TOPIC`.
3. **Actions** tab → *Pararius watch* → **Run workflow** (dry run ticked) → open the log.
   - List of listings = not blocked
   - `BLOCKED: HTTP 403 ...` = Pararius blocks GitHub's servers

## Changing the search
Set your filters on pararius.com, copy the URL, and set it as `SEARCH_URLS`
(comma-separate several). Default: `https://www.pararius.com/apartments/amsterdam/0-1500`.
