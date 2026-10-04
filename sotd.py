#!/usr/bin/env python3
"""
Song of the Day bot.

Watches a Spotify playlist. When a new song shows up, posts it to a Discord webhook.

    python sotd.py               # check once and exit (GitHub Actions / cron)
    python sotd.py --loop 120    # check every 120 seconds forever (always-on machine)

Required environment variables:
    SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET
    SPOTIFY_REFRESH_TOKEN      (get it once with get_refresh_token.py)
    SPOTIFY_PLAYLIST_ID        (the ID from the playlist link)
    DISCORD_WEBHOOK_URL

Optional:
    STATE_FILE                 (default: state.json next to this script)
    TIMEZONE                   (default: America/Los_Angeles)
    DRY_RUN=1                  (print the message instead of posting it)

Only standard library. No pip install needed.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ============================================================================
#  EDIT THIS PART TO MATCH HOW YOU POST IT
#
#  Placeholders you can use:
#    {title}    song name
#    {artists}  "Artist 1, Artist 2"
#    {artist}   first artist only
#    {album}    album name
#    {url}      open.spotify.com link (Discord turns this into a player embed)
#    {number}   position in the playlist (1 = first song ever added)
#    {mdate}    day it was added, like "10/3"
#    {date}     day it was added, like "October 3"
#    {weekday}  like "Saturday"
#    {year}     like "2026"
#
#  Discord markdown works: **bold**, *italics*, __underline__, -# small text
# ============================================================================
MESSAGE_TEMPLATE = (
    "Song of the Day ({mdate})\n"
    "\n"
    "{title} - {artists}\n"
    "\n"
    "{url}"
)

# If your "Day #" count doesn't match the playlist position (say you started the
# playlist on day 15), set this to the difference. {number} = position + offset.
NUMBER_OFFSET = 0

# If a bunch of songs show up at once (you bulk-added or rebuilt the playlist),
# post at most this many and quietly mark the rest as seen. Stops it from spamming.
MAX_POSTS_PER_CHECK = 3

# Optional: make the webhook post under a custom name/avatar. Leave as None to use
# whatever name/avatar the webhook was given in Discord's settings.
WEBHOOK_USERNAME = None
WEBHOOK_AVATAR_URL = None
# ============================================================================

API = os.environ.get("SPOTIFY_API_BASE", "https://api.spotify.com/v1")
ACCOUNTS = os.environ.get("SPOTIFY_ACCOUNTS_BASE", "https://accounts.spotify.com")
STATE_FILE = Path(os.environ.get("STATE_FILE", Path(__file__).with_name("state.json")))
TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/Los_Angeles"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing environment variable: {name}")
    return value


# ---------------------------------------------------------------- HTTP helper

def http(method, url, headers=None, form=None, body=None, retries=3):
    """Small JSON-over-HTTP helper with basic 429 / 5xx retry."""
    headers = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    headers.setdefault("User-Agent", "song-of-the-day-bot/1.0")

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            retryable = e.code == 429 or e.code >= 500
            if retryable and attempt < retries:
                wait = float(e.headers.get("Retry-After") or 0) or 2 ** attempt
                try:  # Discord puts retry_after in the JSON body
                    wait = max(wait, float(json.loads(raw).get("retry_after", 0)))
                except (ValueError, AttributeError):
                    pass
                time.sleep(min(wait, 60))
                continue
            raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {raw[:500]}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url} -> {e.reason}") from None


# ---------------------------------------------------------------- Spotify

_token = {"value": None, "expires": 0.0}


def access_token():
    """Trade the long-lived refresh token for a short-lived access token (cached)."""
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]

    creds = f"{env('SPOTIFY_CLIENT_ID')}:{env('SPOTIFY_CLIENT_SECRET')}"
    resp = http(
        "POST",
        f"{ACCOUNTS}/api/token",
        headers={"Authorization": "Basic " + base64.b64encode(creds.encode()).decode()},
        form={"grant_type": "refresh_token", "refresh_token": env("SPOTIFY_REFRESH_TOKEN")},
    )
    new_refresh = resp.get("refresh_token")
    if new_refresh and new_refresh != os.environ.get("SPOTIFY_REFRESH_TOKEN"):
        # Spotify sometimes hands back a new refresh token. The old one usually keeps
        # working, but if auth ever starts failing, rerun get_refresh_token.py.
        print("Note: Spotify issued a new refresh token. Old one is still in use.")

    _token["value"] = resp["access_token"]
    _token["expires"] = time.time() + int(resp.get("expires_in", 3600))
    return _token["value"]


def spotify_get(path_or_url, params=None):
    url = path_or_url if path_or_url.startswith("http") else f"{API}{path_or_url}"
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    return http("GET", url, headers={"Authorization": f"Bearer {access_token()}"})


def playlist_snapshot(playlist_id):
    """snapshot_id changes any time the playlist changes. Cheap way to skip work."""
    return spotify_get(f"/playlists/{playlist_id}", {"fields": "snapshot_id"})["snapshot_id"]


def playlist_entries(playlist_id):
    """Every entry in the playlist, in playlist order, with its 1-based position."""
    entries = []
    url = f"{API}/playlists/{playlist_id}/items"
    params = {"limit": 50, "additional_types": "track"}
    while url:
        page = spotify_get(url, params)
        params = None  # the "next" URL already has its query string
        for raw in page.get("items", []):
            entries.append({"position": len(entries) + 1, **raw})
        url = page.get("next")
    return entries


def entry_key(entry):
    """Unique per add. Re-adding the same song later counts as new."""
    song = entry.get("item") or entry.get("track") or {}
    return f"{song.get('uri')}|{entry.get('added_at')}"


# ---------------------------------------------------------------- Discord

def format_message(entry):
    song = entry.get("item") or entry.get("track") or {}
    artists = [a.get("name", "") for a in song.get("artists", []) if a.get("name")]
    added = entry.get("added_at")
    when = (
        datetime.fromisoformat(added.replace("Z", "+00:00")).astimezone(TZ)
        if added else datetime.now(TZ)
    )
    fields = {
        "title": song.get("name", "Unknown song"),
        "artists": ", ".join(artists) or "Unknown artist",
        "artist": artists[0] if artists else "Unknown artist",
        "album": (song.get("album") or {}).get("name", ""),
        "url": (song.get("external_urls") or {}).get("spotify", ""),
        "number": entry["position"] + NUMBER_OFFSET,
        "mdate": f"{when.month}/{when.day}",
        "date": f"{when:%B} {when.day}",
        "weekday": f"{when:%A}",
        "year": when.year,
    }
    return MESSAGE_TEMPLATE.format(**fields)


def post_to_discord(text):
    if DRY_RUN:
        print("---- DRY RUN, would post: ----\n" + text + "\n------------------------------")
        return
    payload = {
        "content": text,
        "allowed_mentions": {"parse": []},  # never accidentally ping @everyone etc.
    }
    if WEBHOOK_USERNAME:
        payload["username"] = WEBHOOK_USERNAME
    if WEBHOOK_AVATAR_URL:
        payload["avatar_url"] = WEBHOOK_AVATAR_URL
    http("POST", env("DISCORD_WEBHOOK_URL"), body=payload)


# ---------------------------------------------------------------- State

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- Main logic

def check_once():
    playlist_id = env("SPOTIFY_PLAYLIST_ID")
    state = load_state()

    snapshot = playlist_snapshot(playlist_id)
    if state and state.get("snapshot_id") == snapshot:
        print("No change.")
        return

    entries = playlist_entries(playlist_id)
    playable = [e for e in entries if (e.get("item") or e.get("track")) and not e.get("is_local")]

    if state is None:
        # First run ever: remember what's already there, post nothing.
        save_state({"snapshot_id": snapshot, "seen": [entry_key(e) for e in entries]})
        print(f"First run. Marked {len(entries)} existing songs as already posted.")
        return

    seen = set(state.get("seen", []))
    new = [e for e in playable if entry_key(e) not in seen]
    new.sort(key=lambda e: e.get("added_at") or "")

    if not new:
        print("Playlist changed but no new songs (removed, reordered, or renamed).")

    to_post = new[-MAX_POSTS_PER_CHECK:]
    skipped = new[:-MAX_POSTS_PER_CHECK] if len(new) > MAX_POSTS_PER_CHECK else []
    if skipped:
        print(f"{len(new)} new songs at once. Posting the latest {len(to_post)}, skipping {len(skipped)}.")
        seen.update(entry_key(e) for e in skipped)

    for e in to_post:
        post_to_discord(format_message(e))
        seen.add(entry_key(e))
        # Save after every post so a crash halfway never double-posts.
        save_state({"snapshot_id": state.get("snapshot_id"), "seen": sorted(seen)})
        song = e.get("item") or e.get("track")
        print(f"Posted: {song.get('name')}")

    save_state({"snapshot_id": snapshot, "seen": sorted(seen)})


def main():
    parser = argparse.ArgumentParser(description="Post new Spotify playlist songs to Discord.")
    parser.add_argument("--loop", type=int, metavar="SECONDS",
                        help="keep running and check every SECONDS (minimum 30)")
    parser.add_argument("--preview", action="store_true",
                        help="print the message for the newest song, touch nothing")
    parser.add_argument("--send", action="store_true",
                        help="with --preview: actually post it (use a test channel)")
    args = parser.parse_args()

    # Fail right away on a missing secret, not hours later when a song finally shows up.
    for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN",
                 "SPOTIFY_PLAYLIST_ID", "DISCORD_WEBHOOK_URL"):
        env(name)

    if args.preview:
        entries = [e for e in playlist_entries(env("SPOTIFY_PLAYLIST_ID"))
                   if (e.get("item") or e.get("track")) and not e.get("is_local")]
        if not entries:
            sys.exit("Playlist is empty (or Spotify returned no items).")
        newest = max(entries, key=lambda e: e.get("added_at") or "")
        text = format_message(newest)
        print(text)
        if args.send:
            post_to_discord(text)
            print("\nSent to Discord.")
        return

    if not args.loop:
        check_once()
        return

    interval = max(30, args.loop)
    print(f"Checking every {interval} seconds. Ctrl+C to stop.")
    while True:
        try:
            check_once()
        except Exception as exc:  # keep the loop alive through hiccups
            print(f"[{datetime.now(TZ):%Y-%m-%d %H:%M}] Error: {exc}", file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    main()
