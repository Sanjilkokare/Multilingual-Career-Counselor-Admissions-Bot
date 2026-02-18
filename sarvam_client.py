"""
sarvam_client.py — Reusable client for Sarvam AI API.

Provides:
  - sarvam_chat(): General-purpose chat completion.
  - polish_whatsapp_reply(): Rephrases rule-based answers in WhatsApp tone.
  - tailor_roadmap(): Personalizes a curated roadmap template for a user's profile.
    STRICT: never adds new facts. Falls back to base_reply on any failure.

v4: Added tailor_roadmap() for career counselor upgrade.
"""

import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_BASE_URL = "https://api.sarvam.ai/v1"
REQUEST_TIMEOUT = 30  # seconds


def _get_headers() -> dict:
    """Return headers required by the Sarvam API."""
    if not SARVAM_API_KEY:
        raise ValueError(
            "SARVAM_API_KEY is not set. Add it to your .env file."
        )
    return {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Generic chat completion
# ---------------------------------------------------------------------------
def sarvam_chat(messages: list[dict], model: str = "sarvam-m") -> str:
    """
    Send messages to Sarvam AI and return the assistant's reply.
    Returns an error string (starting with '[Error]') on failure.
    """
    payload = {"model": model, "messages": messages}

    try:
        response = requests.post(
            f"{SARVAM_BASE_URL}/chat/completions",
            json=payload,
            headers=_get_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"]

        logger.error("Unexpected Sarvam API response: %s", data)
        return "[Error] Unexpected response from Sarvam AI."

    except requests.exceptions.Timeout:
        logger.error("Sarvam API request timed out.")
        return "[Error] Request timed out. Please try again."
    except requests.exceptions.RequestException as exc:
        logger.error("Sarvam API request failed: %s", exc)
        return f"[Error] API request failed: {exc}"


# ---------------------------------------------------------------------------
# WhatsApp reply polisher (with strict guardrails)
# ---------------------------------------------------------------------------
POLISH_SYSTEM_PROMPT = """\
You are a WhatsApp reply assistant for a tech coaching academy.

STRICT RULES — FOLLOW ALL OF THEM:
1. Rewrite the BASE REPLY in a warm, friendly WhatsApp tone.
2. Keep the reply under 60-80 words. Be concise.
3. Do NOT add any new facts, prices, timings, course names, or details
   beyond what is in the BASE REPLY and BUSINESS CONTEXT.
4. If the base reply contains URLs or links, keep them EXACTLY as they are.
   Do not shorten, modify, or remove any link.
5. If the base reply asks a question, keep the question clear and crisp.
6. If the base reply already looks complete, just rephrase it slightly.
7. Use simple language. Short sentences. Emojis are okay (1-2 max).
8. Reply in the same language the user wrote in. If user wrote Hinglish,
   reply in Hinglish.
9. NEVER invent or hallucinate information. Only rephrase what's given.
"""


def polish_whatsapp_reply(
    base_reply: str,
    user_message: str,
    rules_context: str,
    bypass: bool = False,
) -> str:
    """
    Use Sarvam LLM to rephrase base_reply in WhatsApp-friendly tone.

    Args:
        base_reply:     Factual answer from the rule engine.
        user_message:   Original user message.
        rules_context:  Business summary for grounding.
        bypass:         If True, skip LLM and return base_reply as-is.

    Returns:
        Polished reply, or base_reply unchanged on any failure.
    """
    # Polish bypass mode
    if bypass:
        logger.info("Polish bypass enabled — returning base reply as-is.")
        return base_reply

    user_prompt = (
        f"USER MESSAGE: {user_message}\n\n"
        f"BASE REPLY (use ONLY these facts — do not add anything):\n{base_reply}\n\n"
        f"BUSINESS CONTEXT (for reference only):\n{rules_context}\n\n"
        "Now rewrite the BASE REPLY for WhatsApp. Keep it short, friendly, and factual."
    )

    messages = [
        {"role": "system", "content": POLISH_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    polished = sarvam_chat(messages)

    # Fallback: if LLM call failed, return the unpolished base reply
    if polished.startswith("[Error]"):
        logger.warning("Polish failed (%s) — using base reply as fallback.", polished)
        return base_reply

    return polished


# ---------------------------------------------------------------------------
# Career roadmap tailor (personalizes curated template for user profile)
# ---------------------------------------------------------------------------
TAILOR_SYSTEM_PROMPT = """\
You are a friendly career counselor for a tech coaching academy.

STRICT RULES — FOLLOW ALL OF THEM:
1. You are given a CURATED ROADMAP and a USER PROFILE.
2. Rewrite the roadmap in a warm, encouraging WhatsApp-friendly tone.
3. Personalize the advice based on the user's background, skills, and goals.
4. Do NOT invent new stages, topics, or course names beyond what is in the ROADMAP.
5. Do NOT mention specific fees or prices UNLESS they appear in the ROADMAP text.
6. Do NOT promise jobs, salaries, or guaranteed outcomes.
7. Keep WhatsApp formatting: use *bold* for headers, short bullets, and emojis sparingly.
8. Keep the total reply under 300 words (WhatsApp readability).
9. If the roadmap mentions courses we offer, include them. If it says we don't teach
   a domain, be honest about that.
10. Reply in the same language the user wrote in. Hinglish is fine.
11. Always end with the disclaimer and a call-to-action from the roadmap.
12. NEVER hallucinate facts. Only rephrase and personalize what's given.
"""


def tailor_roadmap(
    base_roadmap: str,
    intake_summary: str,
    user_message: str,
    rules_context: str,
    bypass: bool = False,
) -> str:
    """
    Use Sarvam LLM to personalize a curated roadmap template.

    Args:
        base_roadmap:   Formatted roadmap from rules_engine.format_roadmap_for_whatsapp().
        intake_summary: User's intake profile summary.
        user_message:   Original user message that triggered the flow.
        rules_context:  Business summary for grounding.
        bypass:         If True, skip LLM and return base_roadmap as-is.

    Returns:
        Personalized roadmap, or base_roadmap unchanged on any failure.
    """
    if bypass:
        logger.info("Tailor bypass — returning base roadmap as-is.")
        return base_roadmap

    user_prompt = (
        f"USER'S ORIGINAL MESSAGE: {user_message}\n\n"
        f"USER PROFILE:\n{intake_summary}\n\n"
        f"CURATED ROADMAP (use ONLY these facts — do not add new topics or courses):\n"
        f"{base_roadmap}\n\n"
        f"BUSINESS CONTEXT (for reference only):\n{rules_context}\n\n"
        "Now rewrite this roadmap in a personalized, encouraging WhatsApp tone. "
        "Adjust emphasis based on the user's skill level and goals. "
        "Keep all facts, links, and course recommendations exactly as given."
    )

    messages = [
        {"role": "system", "content": TAILOR_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    tailored = sarvam_chat(messages)

    if tailored.startswith("[Error]"):
        logger.warning("Tailor failed (%s) — using base roadmap as fallback.", tailored)
        return base_roadmap

    return tailored
