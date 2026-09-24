"""
Thin wrapper around the Telegram Bot API. Windows curl/schannel needs revocation
checking disabled to reach api.telegram.org in this environment, so requests calls
go through a session with verify=True but we accept the standard requests cert store
(requests uses its own bundled CA store, not schannel, so it doesn't hit the same issue).
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = int(os.environ["TELEGRAM_CHANNEL_ID"])
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

_DIR = os.path.dirname(os.path.abspath(__file__))
OFFSET_FILE = os.path.join(_DIR, ".update_offset")


def _load_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE, "r") as f:
            content = f.read().strip()
            return int(content) if content else None
    return None


def _save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def get_updates(offset=None, timeout=25):
    params = {"timeout": timeout, "allowed_updates": '["message","channel_post"]'}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json()["result"]


def wait_for_next_message(max_wait_seconds=180, poll_timeout=15):
    """
    No trigger, no chat filtering -- long-polls for the very next message/channel_post
    the bot receives from anyone, anywhere (including the "my notes" channel), and
    returns its text directly as the content to draft from.

    Returns the note dict, or None if nothing arrived within max_wait_seconds.
    Persists the offset to disk so re-running the script never re-processes an old
    message.
    """
    offset = _load_offset()
    deadline = time.time() + max_wait_seconds

    while time.time() < deadline:
        remaining = max(1, min(poll_timeout, int(deadline - time.time())))
        updates = get_updates(offset=offset, timeout=remaining)

        for update in updates:
            offset = update["update_id"] + 1
            _save_offset(offset)

            msg = update.get("channel_post") or update.get("message")
            if not msg or "text" not in msg:
                continue

            return {
                "text": msg["text"],
                "chat_id": msg["chat"]["id"],
                "date": msg["date"],
            }

    return None


def send_message(chat_id, text):
    resp = requests.post(
        f"{API_BASE}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {result}")
    return result["result"]


def send_long_message(chat_id, text, chunk_size=4000):
    """
    Telegram's sendMessage text limit is 4096 chars. Our drafts are usually well under
    that, but this splits on paragraph boundaries (falling back to a hard cut) just in
    case, rather than silently truncating real content.
    """
    if len(text) <= chunk_size:
        return [send_message(chat_id, text)]

    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)

    return [send_message(chat_id, chunk) for chunk in chunks]


def send_photo(chat_id, photo_path, caption=None):
    """
    Telegram's sendPhoto caption limit is 1024 chars -- well under a typical LinkedIn
    post's length, so caption is only for short captions. For the full post text, send
    the photo with no caption and follow up with send_long_message.
    """
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    with open(photo_path, "rb") as f:
        resp = requests.post(
            f"{API_BASE}/sendPhoto",
            data=data,
            files={"photo": f},
            timeout=60,
        )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto failed: {result}")
    return result["result"]
