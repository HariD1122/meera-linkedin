"""
MEERA LINKEDIN — reusable pipeline.

Usage:
    python main.py listen [--wait SECONDS]
        No trigger, no chat filtering. Long-polls for the very next message the bot
        receives from anyone, anywhere (including the "my notes" channel), and scores
        it 0-10 for relevance to the meera-skinstinct-voice skill.

        Score <= 5: sends "Sorry the info is not relevant" to the configured Telegram
        channel. Nothing is drafted.

        Score >= 6: drafts a LinkedIn post in Meera's voice, generates a matching 9:16
        image (built from a visual concept grounded in the draft's own content), and
        sends both directly to the configured Telegram channel. NO human review step --
        this happens automatically, per explicit instruction. A local copy is still
        saved under output/<timestamp>/ for your own record either way.

    python main.py send <output_dir>
        Manual utility: (re)sends a previously saved output/<timestamp>/ folder's
        image+caption to the configured Telegram channel. Not part of the normal
        auto-send flow above -- only needed if you want to resend something by hand.
"""

import argparse
import json
import os
import re
import sys
import time

import persona
import telegram_client
import gemini_client

OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
REJECTION_MESSAGE = "Sorry the info is not relevant"


def _parse_scored_response(raw_text):
    """
    Expects the model output as:
        SCORE: <0-10>

        <post text, only present if score >= 6>
    Raises if the SCORE line can't be found -- we never want to silently treat an
    unparsable response as a pass or a fail.
    """
    stripped = raw_text.strip()
    match = re.match(r"SCORE:\s*(\d+)", stripped, re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not parse a SCORE line from the model's output: {stripped[:200]!r}")

    score = int(match.group(1))
    rest = stripped[match.end():].strip()
    return score, rest


def cmd_listen(wait_seconds):
    print(f"Listening for the next message (up to {wait_seconds}s)...")
    note = telegram_client.wait_for_next_message(max_wait_seconds=wait_seconds)

    if note is None:
        print("No new message arrived in that window. Send one and try again.")
        return

    print(f"Got note: {note['text'][:120]!r}")

    system_prompt = persona.build_system_prompt()
    raw = gemini_client.generate_post_text(system_prompt, note["text"])
    score, draft_text = _parse_scored_response(raw)
    print(f"Relevance score: {score}/10")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(OUTPUT_ROOT, timestamp)
    os.makedirs(out_dir, exist_ok=True)

    if score <= 5:
        telegram_client.send_message(telegram_client.CHANNEL_ID, REJECTION_MESSAGE)
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"source_note": note, "score": score, "outcome": "rejected"}, f, indent=2)
        print(f"Score {score}/10 (<=5) -- sent rejection message to the channel, nothing drafted.")
        return

    visual_concept = gemini_client.generate_visual_concept(draft_text)
    image_prompt = (
        f"{visual_concept}\n\n"
        "Format: 9:16 vertical graphic for a LinkedIn post by Skinstinct, an Indian "
        "skincare-science brand. Clean, editorial, clinical-but-warm aesthetic. No "
        "photorealistic faces. Do not add any text, number, or claim beyond what is "
        "specified above."
    )

    image_path = os.path.join(out_dir, "image.png")
    gemini_client.generate_image(image_prompt, image_path)

    with open(os.path.join(out_dir, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(draft_text)

    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "source_note": note,
                "score": score,
                "outcome": "sent",
                "visual_concept": visual_concept,
                "image_prompt": image_prompt,
            },
            f,
            indent=2,
        )

    photo_result = telegram_client.send_photo(telegram_client.CHANNEL_ID, image_path)
    telegram_client.send_long_message(telegram_client.CHANNEL_ID, draft_text)
    print(f"Score {score}/10 (>=6) -- sent directly to the channel. "
          f"photo message_id={photo_result['message_id']}")
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
