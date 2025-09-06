#!/usr/bin/env python3
"""
serve_videos_webinput.py

Run:
    python serve_videos_webinput.py

Features:
- Starts a Flask server on 0.0.0.0:8000
- First visit shows a form to input the folder path (no CLI args required)
- Builds full, percent-encoded streaming URLs for every video in the folder
- The visible URLs are "sanitized" (parentheses removed) to avoid client issues,
  but the server still serves the real file from disk by mapping the sanitized key back to the filename.
- Provides a downloadable M3U playlist of the sanitized URLs
"""

import os
import socket
import urllib.parse
from pathlib import Path
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, Response, flash

app = Flask(__name__)
app.secret_key = "local-video-server-secret"  # only used for flash messages (local use only)

PORT = 8000
HOST = "0.0.0.0"

HTML_SETUP = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Setup - Video Server</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial; padding: 20px; }
    input[type=text] { width: 100%; padding: 8px; margin: 8px 0 12px 0; box-sizing:border-box; }
    button { padding: 8px 12px; border-radius:6px; border:1px solid #666; background:#eee; }
    .note { color:#444; margin-top:12px; }
    .err { color: #b00020; }
  </style>
</head>
<body>
  <h2>Video Server — Setup</h2>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="err">{{ messages[0] }}</div>
    {% endif %}
  {% endwith %}
  <form method="post" action="/set_folder">
    <label>Folder path containing your videos (use full path):</label>
    <input type="text" name="folder" placeholder="C:\Users\harso\Downloads\Modern Family Season 2  (1080p BD x265 10bit Joy)" required>
    <div style="margin-top:8px;">
      <button type="submit">Load Folder</button>
    </div>
  </form>
  <div class="note">
    The server listens on <strong>port {{port}}</strong>. After you load the folder, open this page from your iPhone using:
    <br><strong>http://{{ip}}:{{port}}/</strong>
  </div>
</body>
</html>
"""

HTML_INDEX = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Video Server - {{folder_name}}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial; padding: 18px; }
    h1 { margin-bottom: 6px; }
    .note { color: #666; margin-bottom: 18px; }
    ul { padding-left: 18px; }
    li { margin: 10px 0; }
    a { text-decoration: none; color: #0a66c2; word-break: break-all; }
    .small { font-size: .95rem; color:#222 }
    .meta { font-size: .85rem; color:#555 }
    .buttons { margin-top: 14px; }
    button { margin-right: 8px; padding: 8px 12px; border-radius:6px; border:1px solid #ccc; background:#f6f6f6; }
    .hint { color:#444; margin-top:12px }
  </style>
</head>
<body>
  <h1>Video Server — {{folder_name}}</h1>
  <div class="note">Open this page on your iPhone by visiting: <strong>{{server_url}}</strong></div>

  <div class="buttons">
    <form method="get" action="/playlist.m3u" style="display:inline">
      <button type="submit">Download M3U Playlist (all files)</button>
    </form>
    <form method="get" action="/reset" style="display:inline">
      <button type="submit">Change Folder</button>
    </form>
  </div>

  <ul>
  {% for item in files %}
    <li>
      <div class="small"><strong>{{item.display_name}}</strong></div>
      <div class="meta"><a href="{{item.url}}" target="_blank">{{item.url}}</a></div>
    </li>
  {% endfor %}
  </ul>

  <div class="hint">Tip: Use VLC for iOS → Network → Open Network Stream → paste one of the URLs above to stream directly (do not open MKV in Safari — Safari often downloads instead of streaming).</div>
</body>
</html>
"""

# runtime configuration
app.config['VIDEO_DIR'] = None
app.config['SANITIZED_MAP'] = {}   # sanitized_key -> actual filename
app.config['HOST_IP'] = None

def get_local_ip():
    """Return a LAN IP address for the current machine (best-effort)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.m4v'}

def is_video_file(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTS

def sanitize_visible(name: str) -> str:
    """Remove parentheses from visible filename and collapse double spaces."""
    s = name.replace('(', '').replace(')', '')
    # optionally remove other trouble characters (keep commas and apostrophes if you like)
    s = " ".join(s.split())
    return s

@app.route("/", methods=["GET"])
def root():
    if not app.config['VIDEO_DIR']:
        return render_template_string(HTML_SETUP, port=PORT, ip=get_local_ip())
    # build listing
    folder = Path(app.config['VIDEO_DIR'])
    files = []
    host_ip = app.config['HOST_IP'] or get_local_ip()
    for p in sorted(folder.iterdir()):
        if p.is_file() and is_video_file(p):
            actual_name = p.name
            # get sanitized key used in URL
            sanitized_key = urllib.parse.quote(app.config['SANITIZED_MAP'].get(actual_name, actual_name), safe='')
            url = f"http://{host_ip}:{PORT}/files/{sanitized_key}"
            display_name = app.config['SANITIZED_MAP'].get(actual_name, actual_name)
            files.append({"display_name": display_name, "url": url})
    server_url = f"http://{get_local_ip()}:{PORT}/"
    return render_template_string(HTML_INDEX, files=files, server_url=server_url, folder_name=folder.name)

@app.route("/set_folder", methods=["POST"])
def set_folder():
    folder = request.form.get("folder", "").strip()
    if not folder:
        flash("Folder path is empty.")
        return redirect(url_for('root'))
    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        flash("Folder does not exist or is not a directory. Check the path and try again.")
        return redirect(url_for('root'))

    # build sanitized map: sanitized_display -> actual filename
    sanitized_map = {}
    collisions = {}
    for p in sorted(folder_path.iterdir()):
        if p.is_file() and is_video_file(p):
            actual = p.name
            sanitized = sanitize_visible(actual)
            # ensure uniqueness: if collision, append an index
            key = sanitized
            i = 1
            while key in sanitized_map.values():
                i += 1
                key = f"{sanitized} {i}"
            sanitized_map[actual] = key

    app.config['VIDEO_DIR'] = str(folder_path.resolve())
    app.config['SANITIZED_MAP'] = sanitized_map
    app.config['HOST_IP'] = get_local_ip()
    return redirect(url_for('root'))

@app.route("/reset", methods=["GET"])
def reset():
    app.config['VIDEO_DIR'] = None
    app.config['SANITIZED_MAP'] = {}
    return redirect(url_for('root'))

@app.route("/files/<path:key>")
def serve_by_key(key):
    """Key is the sanitized (and percent-encoded) identifier. Map back to actual filename."""
    # percent-decode key to match sanitized_map values
    decoded = urllib.parse.unquote(key)
    folder = Path(app.config['VIDEO_DIR'])
    # find actual filename whose sanitized value equals decoded
    found = None
    for actual, sanitized in app.config['SANITIZED_MAP'].items():
        if sanitized == decoded:
            found = actual
            break
    if not found:
        # fallback: try matching actual filename directly
        for p in folder.iterdir():
            if p.name == decoded:
                found = p.name
                break
    if not found:
        return "File not found", 404
    # send the real file (Flask will handle range requests)
    return send_from_directory(app.config['VIDEO_DIR'], found, conditional=True)

@app.route("/playlist.m3u")
def playlist_m3u():
    folder = Path(app.config['VIDEO_DIR'])
    lines = ["#EXTM3U"]
    host_ip = app.config['HOST_IP'] or get_local_ip()
    for actual, sanitized in app.config['SANITIZED_MAP'].items():
        encoded = urllib.parse.quote(sanitized, safe='')
        url = f"http://{host_ip}:{PORT}/files/{encoded}"
        lines.append(url)
    body = "\n".join(lines) + "\n"
    return Response(body, mimetype="audio/x-mpegurl", headers={"Content-Disposition": f"attachment; filename=videos.m3u"})

if __name__ == "__main__":
    print("Starting server on 0.0.0.0:%d" % PORT)
    print("Open on this machine: http://localhost:%d/" % PORT)
    print("Open on another device: http://%s:%d/" % (get_local_ip(), PORT))
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
