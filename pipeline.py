"""
The one place the actual score -> draft -> reference -> image -> send pipeline lives.
Both main.py (local long-polling) and api/webhook.py (Vercel) call handle_note() so the
logic can never drift out of sync between the two entry points -- that drift is exactly
what caused the caption-length bug earlier, when send logic lived in two places.
"""

import os
import re

import gemini_client
import persona
import telegram_client

REJECTION_MESSAGE = "Sorry the info is not relevant"
MIN_WORDS = 500
MAX_WORDS = 550
MAX_LENGTH_REVISIONS = 1


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


def _word_count(text):
    return len(text.split())


def _format_reference(sources):
    if not sources:
        return None
    top = sources[0]
    return f"Reference: {top['title']} — {top['uri']}"


def handle_note(note_text, image_dir="/tmp"):
    """
    Runs the full pipeline for one note and sends the result to the configured
    Telegram channel (either the rejection message or the finished post+image).
    Returns a result dict the caller can log or save locally.
    """
    system_prompt = persona.build_system_prompt()
    raw, _draft_sources = gemini_client.generate_post_text(system_prompt, note_text)
    score, draft_text = _parse_scored_response(raw)

    if score <= 5:
        telegram_client.send_message(
            telegram_client.CHANNEL_ID,
            f"{REJECTION_MESSAGE}\n\nPost rating: {score}/10",
        )
        return {"score": score, "outcome": "rejected"}

    # Word-count target is a real requirement, not a suggestion -- LLMs miss it often
    # enough on the first pass that a bounded revision loop is worth the extra latency.
    revisions = 0
    while _word_count(draft_text) not in range(MIN_WORDS, MAX_WORDS + 1) and revisions < MAX_LENGTH_REVISIONS:
        revisions += 1
        current = _word_count(draft_text)
        direction = "Shorten" if current > MAX_WORDS else "Expand"
        raw, _draft_sources = gemini_client.generate_post_text(
            system_prompt,
            note_text,
            revision_note=(
                f"Your previous draft was {current} words. {direction} it to land between "
                f"{MIN_WORDS} and {MAX_WORDS} words. Keep the same facts, voice, and score -- "
                f"do not add any new claims just to hit the count."
            ),
        )
        score, draft_text = _parse_scored_response(raw)

    # Dedicated reference search, decoupled from whatever incidental grounding happened
    # while drafting -- guarantees a real search is actually attempted every time.
    reference_sources = gemini_client.find_reference(note_text)
    reference_block = _format_reference(reference_sources)

    caption_parts = [draft_text]
    if reference_block:
        caption_parts.append(reference_block)
    caption_parts.append(f"Post rating: {score}/10")
    full_caption = "\n\n".join(caption_parts)

    visual_concept = gemini_client.generate_visual_concept(draft_text)
    image_prompt = (
        f"{visual_concept}\n\n"
        "Format: 9:16 vertical graphic for a LinkedIn post by Skinstinct, an Indian "
        "skincare-science brand. Clean, editorial, clinical-but-warm aesthetic. No "
        "photorealistic faces. Do not add any text, number, or claim beyond what is "
        "specified above."
    )
    image_path = os.path.join(image_dir, "meera_linkedin_image.png")
    gemini_client.generate_image(image_prompt, image_path)

    telegram_client.send_photo(telegram_client.CHANNEL_ID, image_path)
    telegram_client.send_long_message(telegram_client.CHANNEL_ID, full_caption)

    return {
        "score": score,
        "outcome": "sent",
        "word_count": _word_count(draft_text),
        "revisions": revisions,
        "sources": reference_sources,
        "draft_text": draft_text,
        "full_caption": full_caption,
        "image_path": image_path,
    }
