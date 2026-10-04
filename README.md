# Song of the Day bot

Add a song to the Spotify playlist. A few minutes later it shows up in Discord, formatted the way you post it.

Spotify has no "playlist changed" notification, so this polls. Every check asks Spotify for the playlist's `snapshot_id`, which changes whenever the playlist does. If it hasn't changed, the check ends there. If it has, the bot finds songs it hasn't posted yet and posts them.

## What you need first

- **Spotify Premium on the account that owns the Spotify developer app.** Spotify made this mandatory for Development Mode apps in 2026.
- **You must own the playlist or be a collaborator on it.** Spotify's API no longer returns the songs in anyone else's playlist.
- **A Discord webhook for the channel.** Creating one needs the Manage Webhooks permission. If it's your friend's server and you don't have that, ask them to make it and send you the URL.

## Setup (about 20 minutes)

### 1. Discord webhook
Server Settings → Integrations → Webhooks → New Webhook. Pick the channel. Give it a name and avatar if you want. Click **Copy Webhook URL**.

Treat that URL like a password. Anyone who has it can post in that channel.

### 2. Spotify app
1. Go to https://developer.spotify.com/dashboard and click **Create app**.
2. Name and description can be anything.
3. Redirect URI: `http://127.0.0.1:8888/callback` (exactly that, `localhost` won't work).
4. Tick **Web API**. Save.
5. Open the app's settings and copy the **Client ID** and **Client Secret**.

### 3. Get your refresh token (one time, on your own computer)
macOS / Linux:
```bash
SPOTIFY_CLIENT_ID=paste_id SPOTIFY_CLIENT_SECRET=paste_secret python3 get_refresh_token.py
```
Windows PowerShell:
```powershell
$env:SPOTIFY_CLIENT_ID="paste_id"; $env:SPOTIFY_CLIENT_SECRET="paste_secret"; python get_refresh_token.py
```
Log in, click Agree, copy the token it prints.

If you get a 403 later, go to your app's **User Management** tab on the dashboard and add your own Spotify account's email.

### 4. Playlist ID
Share → Copy link to playlist. The ID is the part between `/playlist/` and `?`:
`https://open.spotify.com/playlist/`**`37i9dQZF1DXcBWIGoYBM5M`**`?si=...`

### 5. Set your format
Open `sotd.py` and edit `MESSAGE_TEMPLATE` near the top. The placeholders are listed right above it.

Check it with real data before going live:
```bash
export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... SPOTIFY_REFRESH_TOKEN=... \
       SPOTIFY_PLAYLIST_ID=... DISCORD_WEBHOOK_URL=...
python3 sotd.py --preview          # prints the message for the newest song
python3 sotd.py --preview --send   # actually posts it
```
Point `DISCORD_WEBHOOK_URL` at a webhook in your own test server for `--send` so your friends don't see the test posts.

### 6. Put it on GitHub Actions
1. Make a new GitHub repo and push this folder to it.
2. Repo → Settings → Secrets and variables → Actions → **New repository secret**. Add all five:
   `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_PLAYLIST_ID`, `DISCORD_WEBHOOK_URL`
3. Actions tab → **Song of the Day** → **Run workflow**. The first run posts nothing on purpose. It records every song already in the playlist so it doesn't spam the whole history.
4. Add a song to the playlist. Within about 10 to 20 minutes it should post.

## Public or private repo?

This matters more than it looks.

**Public repo:** Actions minutes are free and unlimited. Every 10 minutes is fine. Your secrets stay hidden, and the code and `state.json` don't contain anything sensitive.

**Private repo:** GitHub Free gives you 2,000 Actions minutes a month, shared across all your private repos, and every run bills as at least one full minute. Every 10 minutes is about 4,300 runs a month, more than double the allowance. Once you hit the cap, every workflow on your account stops until the month resets, including your other bots. If you go private, switch the cron line in `.github/workflows/sotd.yml` to the every-30-minutes one (about 1,440 runs a month).

Either way, GitHub's scheduler is best effort. Runs can show up 5 to 20 minutes late when GitHub is busy. For a once-a-day post that's fine. If you truly need it within 2 minutes, see below.

## Running it on an always-on machine instead
A Raspberry Pi, an old laptop, or a free cloud VM works. Set the five environment variables, then:
```bash
python3 sotd.py --loop 120
```
That checks every 2 minutes forever. Use `tmux`, `screen`, `nohup`, or a systemd service so it keeps going after you close the terminal.

## How it decides what's new
Each playlist entry is identified by song + the time it was added. The bot keeps every one it has seen in `state.json`. So:
- Removing or reordering songs never triggers a post.
- Re-adding a song you removed earlier does count as new.
- Adding several at once posts the newest 3 (`MAX_POSTS_PER_CHECK`) and silently marks the rest as seen.
- Local files and unavailable songs are skipped.

To re-post something, delete its line from `state.json`, set `snapshot_id` to anything else, and run again.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| `HTTP 400 invalid_grant` on `/api/token` | Refresh token is wrong or revoked. Rerun `get_refresh_token.py`. |
| `HTTP 403` on `/playlists/.../items` | You don't own or collaborate on the playlist, your account lacks Premium, or you're not in User Management. |
| `HTTP 404` from Discord | Webhook was deleted. Make a new one and update the secret. |
| Nothing posts, no errors | Check the Actions tab. Scheduled workflows in a public repo pause after 60 days with no commits, but the bot's own state commits normally keep it awake. |
