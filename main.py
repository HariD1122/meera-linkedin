"""
MEERA LINKEDIN — local entry point (long-polling). The actual pipeline logic lives in
pipeline.py, shared with api/webhook.py (the Vercel entry point) so the two can't drift.

Usage:
    python main.py listen [--wait SECONDS]
        No trigger, no chat filtering. Long-polls for the very next message the bot
        receives from anyone, anywhere (including the "my notes" channel), and runs it
        through pipeline.handle_note() -- see that module for the full score/draft/
        reference/image/send behavior. A local copy of the result is saved under
        output/<timestamp>/ either way, for your own record.

    python main.py send <output_dir>
        Manual utility: (re)sends a previously saved output/<timestamp>/ folder's
        image+caption to the configured Telegram channel. Not part of the normal
        auto-send flow above -- only needed if you want to resend something by hand.
"""

import argparse
import json
import os
import shutil
import sys
import time

import pipeline
import telegram_client

OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def cmd_listen(wait_seconds):
    print(f"Listening for the next message (up to {wait_seconds}s)...")
    note = telegram_client.wait_for_next_message(max_wait_seconds=wait_seconds)

    if note is None:
        print("No new message arrived in that window. Send one and try again.")
        return

    print(f"Got note: {note['text'][:120]!r}")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(OUTPUT_ROOT, timestamp)
    os.makedirs(out_dir, exist_ok=True)

    result = pipeline.handle_note(note["text"], image_dir=out_dir)
    print(f"Relevance score: {result['score']}/10")

    if result["outcome"] == "rejected":
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"source_note": note, **result}, f, indent=2)
        print(f"Score {result['score']}/10 (<=5) -- sent rejection message, nothing drafted.")
        return

    with open(os.path.join(out_dir, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(result["full_caption"])

    saved_image_path = os.path.join(out_dir, "image.png")
    if result["image_path"] != saved_image_path and os.path.exists(result["image_path"]):
        shutil.move(result["image_path"], saved_image_path)

    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"source_note": note, **{k: v for k, v in result.items() if k != "image_path"}},
            f,
            indent=2,
        )

    print(f"Score {result['score']}/10 (>=6), {result['word_count']} words, "
          f"{result['revisions']} revision(s) -- sent directly to the channel.")
    print(f"Local copy saved to {out_dir}")


def cmd_send(out_dir):
    caption_path = os.path.join(out_dir, "caption.txt")
    image_path = os.path.join(out_dir, "image.png")

    if not os.path.exists(caption_path) or not os.path.exists(image_path):
        print(f"Missing caption.txt or image.png in {out_dir}")
        sys.exit(1)

    with open(caption_path, "r", encoding="utf-8") as f:
        caption = f.read()

    result = telegram_client.send_photo(telegram_client.CHANNEL_ID, image_path)
    telegram_client.send_long_message(telegram_client.CHANNEL_ID, caption)
    print(f"Sent. Telegram photo message_id={result['message_id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEERA LINKEDIN pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    listen_p = sub.add_parser("listen", help="Wait for a new note, score it, and auto-send if relevant")
    listen_p.add_argument("--wait", type=int, default=120, help="Max seconds to wait for a new note")

    send_p = sub.add_parser("send", help="Manually (re)send a saved draft folder to the Telegram channel")
    send_p.add_argument("out_dir", help="Path to an output/<timestamp> draft folder")

    args = parser.parse_args()

    if args.command == "listen":
        cmd_listen(args.wait)
    elif args.command == "send":
        cmd_send(args.out_dir)
