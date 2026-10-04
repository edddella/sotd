#!/usr/bin/env python3
"""
Run this ONCE on your own computer to get a Spotify refresh token for the bot.

    1. In your Spotify app's settings (developer.spotify.com/dashboard), add this
       exact Redirect URI:   http://127.0.0.1:8888/callback
    2. Run:
           SPOTIFY_CLIENT_ID=xxx SPOTIFY_CLIENT_SECRET=yyy python get_refresh_token.py
       (Windows PowerShell: set them with $env:SPOTIFY_CLIENT_ID="xxx" first)
    3. Log in and click Agree in the browser tab that opens.
    4. Copy the refresh token it prints into your GitHub secret SPOTIFY_REFRESH_TOKEN.

Standard library only.
"""

import base64
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-read-private playlist-read-collaborative"

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
if not CLIENT_ID or not CLIENT_SECRET:
    sys.exit("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET first.")

STATE = secrets.token_urlsafe(16)
result = {}


class Callback(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if query.get("state", [None])[0] != STATE:
            result["error"] = "State mismatch. Try again."
        elif "error" in query:
            result["error"] = query["error"][0]
        else:
            result["code"] = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = "Done. You can close this tab." if "code" in result else f"Failed: {result.get('error')}"
        self.wfile.write(f"<h2>{msg}</h2>".encode())

    def log_message(self, *args):
        pass


auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "response_type": "code",
    "redirect_uri": REDIRECT_URI,
    "scope": SCOPES,
    "state": STATE,
})

print("Opening Spotify login in your browser...")
print("If nothing opens, paste this into your browser:\n" + auth_url + "\n")
webbrowser.open(auth_url)

server = HTTPServer(("127.0.0.1", 8888), Callback)
while not result:
    server.handle_request()

if "code" not in result:
    sys.exit(f"Authorization failed: {result['error']}")

creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
req = urllib.request.Request(
    "https://accounts.spotify.com/api/token",
    data=urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": REDIRECT_URI,
    }).encode(),
    headers={
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
)
with urllib.request.urlopen(req) as resp:
    tokens = json.loads(resp.read())

print("Success. Your refresh token (keep it secret, treat it like a password):\n")
print(tokens["refresh_token"])
