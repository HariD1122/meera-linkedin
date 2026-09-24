"""
Vercel serverless entry point. Telegram POSTs each new update here directly (instead
of us long-polling getUpdates), so this handler runs the whole score -> draft -> image
-> send pipeline synchronously within a single request. See main.py's cmd_listen for
the equivalent local/polling version of this same logic.
"""

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gemini_client
import persona
import telegram_client

REJECTION_MESSAGE = "Sorry the info is not relevant"


def _parse_scored_response(raw_text):
    stripped = raw_text.strip()
    match = re.match(r"SCORE:\s*(\d+)", stripped, re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not parse a SCORE line from the model's output: {stripped[:200]!r}")
    score = int(match.group(1))
    rest = stripped[match.end():].strip()
    return score, rest


def process_update(update):
    msg = update.get("channel_post") or update.get("message")
    if not msg or "text" not in msg:
        return {"skipped": True, "reason": "no text message in update"}

    note_text = msg["text"]

    system_prompt = persona.build_system_prompt()
    raw = gemini_client.generate_post_text(system_prompt, note_text)
    score, draft_text = _parse_scored_response(raw)

    if score <= 5:
        telegram_client.send_message(telegram_client.CHANNEL_ID, REJECTION_MESSAGE)
        return {"score": score, "outcome": "rejected"}

    visual_concept = gemini_client.generate_visual_concept(draft_text)
    image_prompt = (
        f"{visual_concept}\n\n"
        "Format: 9:16 vertical graphic for a LinkedIn post by Skinstinct, an Indian "
        "skincare-science brand. Clean, editorial, clinical-but-warm aesthetic. No "
        "photorealistic faces. Do not add any text, number, or claim beyond what is "
        "specified above."
    )

    image_path = "/tmp/meera_linkedin_image.png"
    gemini_client.generate_image(image_prompt, image_path)

    telegram_client.send_photo(telegram_client.CHANNEL_ID, image_path)
    telegram_client.send_long_message(telegram_client.CHANNEL_ID, draft_text)

    return {"score": score, "outcome": "sent"}


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
