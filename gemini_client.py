"""
Thin wrapper around the Gemini API (text generation with Google Search grounding,
and 9:16 image generation).
"""

import base64
import os

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

API_KEY = os.environ["GEMINI_API_KEY"]
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

TEXT_MODEL = "gemini-flash-latest"
IMAGE_MODEL = "gemini-2.5-flash-image"


def _extract_sources(candidate):
    """
    Pulls real, verifiable URLs out of the API's own grounding metadata -- never from
    text the model wrote itself. A model can hallucinate a plausible-looking citation
    even mid-sentence in an otherwise grounded response; the grounding metadata is the
    one part of the response that's guaranteed to reflect an actual search result.
    """
    grounding = candidate.get("groundingMetadata") or {}
    chunks = grounding.get("groundingChunks") or []
    sources = []
    seen = set()
    for chunk in chunks:
        web = chunk.get("web") or {}
        uri = web.get("uri")
        title = web.get("title") or uri
        if uri and uri not in seen:
            seen.add(uri)
            sources.append({"title": title, "uri": uri})
    return sources


def generate_post_text(system_prompt, note_text, revision_note=None):
    """
    Drafts the LinkedIn post. Google Search grounding is enabled so that any general,
    non-Skinstinct-specific scientific claim Meera's voice would make (e.g. how a
    named compound behaves) can be checked rather than guessed — it must NOT be used
    to invent Skinstinct-specific facts, stats, or stories; the persona prompt already
    instructs the model on that boundary.

    revision_note: optional feedback for a follow-up pass (e.g. wrong word count) --
    appended to the same user turn rather than a fresh conversation, so the model still
    has the original note in context.

    Returns (raw_text, sources) -- raw_text still has the SCORE line for the caller to
    parse; sources is whatever real citations grounded this particular call (may be
    empty if the model didn't need to search).
    """
    url = f"{API_BASE}/{TEXT_MODEL}:generateContent?key={API_KEY}"
    user_text = f"Raw note from Meera's Telegram notes channel:\n\n{note_text}"
    if revision_note:
        user_text += f"\n\nRevision needed: {revision_note}"

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.7},
    }
    resp = requests.post(url, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data}")
    candidate = candidates[0]
    parts = candidate["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts).strip()
    return text, _extract_sources(candidate)


def find_reference(note_text):
    """
    Dedicated search for one real, relevant article or piece of research related to the
    note's topic -- separate from generate_post_text's incidental grounding, so a
    reference search always actually happens rather than depending on whether the model
    felt it needed to search while drafting. Returns only verified sources from the
    API's grounding metadata (see _extract_sources) -- never model-written citation text.
    """
    url = f"{API_BASE}/{TEXT_MODEL}:generateContent?key={API_KEY}"
    prompt = f"""Search for one real, relevant article, study, or piece of research that relates to
the topic below, suitable as a supporting reference for a skincare-science LinkedIn post. Prefer
dermatology/cosmetic-chemistry sources, but any genuinely relevant real source is acceptable.
Briefly name what you found in one sentence.

TOPIC:
{note_text}"""
    resp = requests.post(
        url,
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
        },
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return []
    return _extract_sources(candidates[0])


def generate_visual_concept(draft_text):
    """
    Turns the finished caption into a concrete, self-explanatory visual concept for the
    9:16 image -- so the image communicates the post's actual point on its own, not just
    a mood/texture that happens to match the topic. Grounded strictly in the draft's own
    content (no new facts/numbers), same anti-hallucination boundary as the caption.
    """
    url = f"{API_BASE}/{TEXT_MODEL}:generateContent?key={API_KEY}"
    prompt = f"""You are art-directing a 9:16 vertical LinkedIn graphic to accompany the post below,
written by Meera Pillai for Skinstinct (a skincare-science brand). The goal: someone scrolling past
should understand the post's core point from the image ALONE, without reading the caption.

In 2-4 sentences, describe a concrete visual concept: what's actually depicted (e.g. a simple
labeled diagram, a comparison, an illustrated mechanism), and exactly what text or numbers (if any)
appear on it. Any text/numbers you specify MUST be taken directly from the post below -- never
invent a new figure, label, or claim that isn't already in the post.

Style: clean, editorial, clinical-but-warm, minimal, soft neutral palette (off-white / pale sage /
muted clay), no photorealistic faces, no decorative filler that doesn't carry the post's actual
meaning.

POST:
{draft_text}"""
    resp = requests.post(
        url,
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates for visual concept: {data}")
    parts = candidates[0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def generate_image(image_prompt, out_path):
    """
    Generates a 9:16 image and writes it to out_path (PNG).
    """
    url = f"{API_BASE}/{IMAGE_MODEL}:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": image_prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "9:16"},
        },
    }
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no image candidates: {data}")

    for part in candidates[0]["content"]["parts"]:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            image_bytes = base64.b64decode(inline["data"])
            with open(out_path, "wb") as f:
                f.write(image_bytes)
            return out_path

    raise RuntimeError(f"No image data in Gemini response: {data}")
