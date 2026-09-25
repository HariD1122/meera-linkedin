"""
Vercel serverless entry point. Telegram POSTs each new update here directly (instead
of us long-polling getUpdates), so this handler runs the whole pipeline synchronously
within a single request. The actual logic lives in pipeline.py, shared with main.py
(the local long-polling entry point) so the two can't drift out of sync.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline


def process_update(update):
    msg = update.get("channel_post") or update.get("message")
    if not msg or "text" not in msg:
        return {"skipped": True, "reason": "no text message in update"}

    result = pipeline.handle_note(msg["text"], image_dir="/tmp")
    return {k: v for k, v in result.items() if k not in ("draft_text", "full_caption", "image_path")}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        if expected_secret:
            got_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if got_secret != expected_secret:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"unauthorized")
                return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"

        try:
            update = json.loads(body)
            result = process_update(update)
        except Exception as exc:
            # Still ack with 200 so Telegram doesn't retry-storm the same failing
            # update -- the error is visible in Vercel's function logs either way.
            print(f"webhook error: {exc}")
            result = {"error": str(exc)}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"meera-linkedin webhook is up")
