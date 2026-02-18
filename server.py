"""
server.py — FastAPI webhook server for WhatsApp Cloud API integration.

Endpoints:
  GET  /health             -> Health check
  GET  /whatsapp/webhook   -> Meta webhook verification (hub.challenge)
  POST /whatsapp/webhook   -> Receive inbound WhatsApp messages
  POST /whatsapp/send      -> Send a WhatsApp message (admin-only)
  GET  /leads              -> View captured leads (admin-only)

v4: Career counselor upgrade — counseling flow routing, intake state machine,
    roadmap delivery, message dedup, non-text acknowledgement, webhook
    signature verification, hot-reload for career_kb.json.

Run:
  uvicorn server:app --reload --port 8000
"""

import os
import json
import hmac
import hashlib
import logging
from datetime import datetime, timezone

import requests as http_requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

from rules_engine import (
    load_rules,
    load_career_kb,
    match_intent,
    detect_course_entity,
    detect_career_domain,
    build_base_reply,
    build_rules_context,
    resolve_course_from_reply,
    is_after_hours,
    get_roadmap_template,
    format_roadmap_for_whatsapp,
    build_career_start_reply,
    build_intake_summary,
    get_intake_question,
    parse_intake_answer,
    INTAKE_FIELDS,
    CAREER_GUIDANCE_INTENTS,
    _build_template_context,
    _render_template,
)
from sarvam_client import polish_whatsapp_reply, tailor_roadmap

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("whatsapp-bot")

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "my_verify_token")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
BUSINESS_TIMEZONE = os.getenv("BUSINESS_TIMEZONE", "Asia/Kolkata")

DEMO_MODE = not WHATSAPP_TOKEN
if DEMO_MODE:
    logger.warning(
        "WHATSAPP_TOKEN not set — running in DEMO MODE (replies logged, not sent)."
    )

if not ADMIN_TOKEN:
    logger.warning(
        "ADMIN_TOKEN not set — /leads and /whatsapp/send are UNPROTECTED. "
        "Set ADMIN_TOKEN in .env for production."
    )

# ---------------------------------------------------------------------------
# Phone masking helper
# ---------------------------------------------------------------------------
def _mask_phone(phone: str) -> str:
    """Show only last 4 digits: '919876543210' -> '****3210'."""
    if len(phone) <= 4:
        return "****"
    return "****" + phone[-4:]


# ---------------------------------------------------------------------------
# Hot-reload: rules.json (check mtime on each request)
# ---------------------------------------------------------------------------
RULES_FILE = "rules.json"
CAREER_KB_FILE = "career_kb.json"
_rules_cache: dict = {}
_rules_mtime: float = 0.0
_rules_context_cache: str = ""


def _get_rules() -> dict:
    """Return rules dict, reloading from disk if file changed."""
    global _rules_cache, _rules_mtime, _rules_context_cache

    try:
        current_mtime = os.path.getmtime(RULES_FILE)
    except OSError:
        if _rules_cache:
            return _rules_cache
        raise

    if current_mtime != _rules_mtime or not _rules_cache:
        _rules_cache = load_rules(RULES_FILE)
        _rules_mtime = current_mtime
        _rules_context_cache = build_rules_context(_rules_cache)
        logger.info("Rules reloaded (mtime=%.0f).", current_mtime)

    return _rules_cache


def _get_rules_context() -> str:
    """Return cached rules context string (refreshed with rules)."""
    _get_rules()  # ensure cache is fresh
    return _rules_context_cache


def _get_career_kb() -> dict:
    """Return career KB dict with hot-reload."""
    return load_career_kb(CAREER_KB_FILE)


# Initial load at startup
try:
    _get_rules()
    logger.info("Business rules loaded successfully.")
except Exception as exc:
    logger.error("Failed to load rules.json: %s", exc)
    _rules_cache = {"rules": [], "courses": [], "business_name": "Unknown"}
    _rules_context_cache = ""

try:
    _get_career_kb()
    logger.info("Career KB loaded successfully.")
except Exception as exc:
    logger.warning("Career KB not available: %s", exc)


# ---------------------------------------------------------------------------
# Sessions storage (JSON file, keyed by phone number)
# ---------------------------------------------------------------------------
SESSIONS_FILE = "sessions.json"
SESSION_EXPIRY_MINUTES = 15  # increased for multi-step counseling


def _load_sessions() -> dict:
    """Load sessions dict from JSON file."""
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_sessions(sessions: dict):
    """Write sessions dict to JSON file."""
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)


def _get_session(phone: str) -> dict | None:
    """Get active session for phone, or None if expired/missing."""
    sessions = _load_sessions()
    session = sessions.get(phone)
    if not session:
        return None

    # Check expiry
    updated_at = session.get("updated_at", session.get("asked_at", ""))
    expiry_min = session.get("expires_after_minutes", SESSION_EXPIRY_MINUTES)
    if updated_at:
        try:
            ts = datetime.fromisoformat(updated_at)
            elapsed = (datetime.now(timezone.utc) - ts).total_seconds() / 60
            if elapsed > expiry_min:
                _clear_session(phone)
                return None
        except (ValueError, TypeError):
            pass

    return session


def _set_session(phone: str, session_data: dict):
    """Create or update a session for phone."""
    sessions = _load_sessions()
    session_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    session_data.setdefault("expires_after_minutes", SESSION_EXPIRY_MINUTES)
    sessions[phone] = session_data
    _save_sessions(sessions)
    logger.info("Session set for %s: type=%s",
                _mask_phone(phone), session_data.get("type", "unknown"))


def _clear_session(phone: str):
    """Remove session for phone."""
    sessions = _load_sessions()
    if phone in sessions:
        del sessions[phone]
        _save_sessions(sessions)
        logger.info("Session cleared for %s", _mask_phone(phone))


# ---------------------------------------------------------------------------
# Leads storage (JSON file)
# ---------------------------------------------------------------------------
LEADS_FILE = "leads.json"


def _load_leads() -> list:
    """Load leads from the JSON file."""
    if not os.path.exists(LEADS_FILE):
        return []
    try:
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_lead(lead: dict):
    """Append a lead to the JSON file."""
    leads = _load_leads()
    leads.append(lead)
    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)
    logger.info("Lead saved: %s", _mask_phone(lead.get("phone", "unknown")))


# ---------------------------------------------------------------------------
# Message deduplication (Meta retries)
# ---------------------------------------------------------------------------
_recent_message_ids: dict[str, float] = {}  # msg_id -> timestamp
DEDUP_WINDOW_SECONDS = 60


def _is_duplicate(msg_id: str) -> bool:
    """Check if message ID was recently processed."""
    now = datetime.now(timezone.utc).timestamp()

    # Clean old entries
    expired = [k for k, v in _recent_message_ids.items() if now - v > DEDUP_WINDOW_SECONDS]
    for k in expired:
        del _recent_message_ids[k]

    if msg_id in _recent_message_ids:
        return True

    _recent_message_ids[msg_id] = now
    return False


# ---------------------------------------------------------------------------
# Webhook signature verification (Meta X-Hub-Signature-256)
# ---------------------------------------------------------------------------
def _verify_signature(request_body: bytes, signature_header: str) -> bool:
    """Verify Meta webhook signature if WHATSAPP_APP_SECRET is configured."""
    if not WHATSAPP_APP_SECRET:
        return True  # skip if not configured

    if not signature_header:
        return False

    # Format: "sha256=<hex>"
    if not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[7:]
    computed_sig = hmac.new(
        WHATSAPP_APP_SECRET.encode("utf-8"),
        request_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_sig, expected_sig)


# ---------------------------------------------------------------------------
# Admin auth helper
# ---------------------------------------------------------------------------
def _check_admin_auth(request: Request):
    """Raise 401 if ADMIN_TOKEN is set and request doesn't match."""
    if not ADMIN_TOKEN:
        return  # no token configured -> open access (dev mode)
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="WhatsApp Career Counselor Bot", version="4.0.0")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class SendMessageRequest(BaseModel):
    to: str
    message: str


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    rules = _get_rules()
    career_kb = _get_career_kb()
    return {
        "status": "ok",
        "business": rules.get("business_name", "N/A"),
        "courses_loaded": len(rules.get("courses", [])),
        "rules_loaded": len(rules.get("rules", [])),
        "roadmaps_loaded": len(career_kb.get("roadmaps", [])),
        "demo_mode": DEMO_MODE,
        "active_sessions": len(_load_sessions()),
    }


# ---------------------------------------------------------------------------
# GET /whatsapp/webhook — Meta verification
# ---------------------------------------------------------------------------
@app.get("/whatsapp/webhook")
async def verify_webhook(request: Request):
    """Return hub.challenge if verify_token matches."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully.")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Webhook verification failed. Token mismatch.")
    logger.info(f"Received token: {token}")
    logger.info(f"Expected token: {WHATSAPP_VERIFY_TOKEN}")


# ---------------------------------------------------------------------------
# POST /whatsapp/webhook — Inbound messages
# ---------------------------------------------------------------------------
@app.post("/whatsapp/webhook")
async def receive_message(request: Request):
    """
    Receive an inbound WhatsApp message. Routing:
      1. Dedup check
      2. Non-text acknowledgement
      3. Session resolver (counseling intake or admissions "which course?")
      4. Career guidance intent -> counseling flow
      5. Admissions intent -> rule engine
      6. Fallback -> lead capture
    """
    rules = _get_rules()
    rules_context = _get_rules_context()
    career_kb = _get_career_kb()

    # --- Read raw body for signature verification ---
    raw_body = await request.body()

    # --- Verify webhook signature ---
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    if WHATSAPP_APP_SECRET and not _verify_signature(raw_body, sig_header):
        logger.warning("Webhook signature verification failed.")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # --- Parse JSON body ---
    try:
        body = json.loads(raw_body)
        logger.info("Inbound webhook payload received.")
    except Exception:
        logger.error("Could not parse webhook JSON body.")
        return JSONResponse({"status": "error", "detail": "Invalid JSON"}, status_code=400)

    # --- Defensive WhatsApp payload parsing ---
    try:
        entry = body.get("entry", [])
        if not entry:
            return JSONResponse({"status": "ignored"})

        changes = entry[0].get("changes", [])
        if not changes:
            return JSONResponse({"status": "ignored"})

        value = changes[0].get("value", {})

        # --- Log delivery status updates (sent/delivered/read/failed) ---
        statuses = value.get("statuses", [])
        if statuses:
            for s in statuses:
                logger.info(
                    "MESSAGE STATUS UPDATE: id=%s status=%s recipient=%s timestamp=%s errors=%s",
                    s.get("id"), s.get("status"), s.get("recipient_id"),
                    s.get("timestamp"), s.get("errors", []),
                )
            if not value.get("messages"):
                return JSONResponse({"status": "status_update_logged"})

        messages = value.get("messages", [])
        if not messages:
            return JSONResponse({"status": "ignored"})

        msg = messages[0]
        msg_id = msg.get("id", "")
        msg_type = msg.get("type", "")
        sender = msg.get("from", "unknown")

    except (IndexError, KeyError, TypeError) as exc:
        logger.error("Payload parsing error: %s", exc)
        return JSONResponse({"status": "error", "detail": "Parse error"}, status_code=400)

    # --- Message deduplication ---
    if msg_id and _is_duplicate(msg_id):
        logger.info("Duplicate message %s from %s — ignoring.", msg_id, _mask_phone(sender))
        return JSONResponse({"status": "duplicate"})

    # --- Non-text message handling ---
    if msg_type != "text":
        logger.info("Non-text message (%s) from %s.", msg_type, _mask_phone(sender))
        ack_reply = (
            "I can only read text messages right now. "
            "Please type your question and I'll be happy to help!"
        )
        send_whatsapp_message(sender, ack_reply)
        return JSONResponse({"status": "ok", "reason": "non-text-ack", "reply": ack_reply})

    user_text = msg.get("text", {}).get("body", "").strip()
    if not user_text:
        return JSONResponse({"status": "ignored"})

    logger.info("Message from %s: '%s'", _mask_phone(sender), user_text[:60])

    # --- After-hours check ---
    after_hours = is_after_hours(rules, BUSINESS_TIMEZONE)
    after_hours_prefix = ""
    if after_hours:
        after_hours_prefix = rules.get("after_hours_reply", "") + "\n\n"
        logger.info("After-hours mode active for %s.", _mask_phone(sender))

    # --- Session check ---
    session = _get_session(sender)

    # ===================================================================
    # ROUTE 1: Active counseling intake session
    # ===================================================================
    if session and session.get("type") == "counseling_intake":
        return _handle_counseling_intake(
            sender, user_text, session, rules, rules_context,
            career_kb, after_hours_prefix,
        )

    # ===================================================================
    # ROUTE 2: Active "which course?" session (existing admissions flow)
    # ===================================================================
    if session and session.get("type", "") == "pending_course":
        pending_intent = session.get("pending_intent")
        logger.info("Admissions session for %s: pending_intent=%s",
                     _mask_phone(sender), pending_intent)

        course = resolve_course_from_reply(user_text, rules)
        if course:
            _clear_session(sender)
            matched_rule = None
            for rule in rules.get("rules", []):
                if rule["intent"] == pending_intent:
                    matched_rule = rule
                    break

            if matched_rule:
                ctx = _build_template_context(course, rules)
                template = matched_rule.get("reply_template", "")
                base_reply = _render_template(template, ctx)

                polished_reply = polish_whatsapp_reply(base_reply, user_text, rules_context)
                final_reply = after_hours_prefix + polished_reply
                send_result = send_whatsapp_message(sender, final_reply)

                return JSONResponse({
                    "status": "ok",
                    "intent": pending_intent,
                    "course_id": course.get("course_id"),
                    "resolved": True,
                    "session_resolved": True,
                    "base_reply": base_reply,
                    "polished_reply": final_reply,
                    "send_result": send_result,
                })

        # Could not resolve — clear session, proceed normally
        _clear_session(sender)
        logger.info("Session reply not resolved for %s, proceeding normally.",
                     _mask_phone(sender))

    # ===================================================================
    # ROUTE 3: Intent matching (admissions + career + general)
    # ===================================================================
    intent_result = match_intent(user_text, rules)
    entity_result = detect_course_entity(user_text, rules)

    logger.info(
        "Intent: %s | Confidence: %d%% | Course: %s | Category: %s | Resolved: %s",
        intent_result.get("intent"),
        intent_result.get("confidence", 0),
        entity_result.get("course_id"),
        intent_result.get("intent_category"),
        intent_result.get("resolved"),
    )

    # --- Career guidance intents -> start counseling flow ---
    # Trigger counseling if:
    #   a) A career_guidance intent is resolved, OR
    #   b) A career_guidance intent was detected (even low confidence) AND a career domain is detected, OR
    #   c) No intent matched but a career domain keyword is clearly present
    intent_name = intent_result.get("intent", "")
    intent_category = intent_result.get("intent_category", "")
    domain_id_precheck = detect_career_domain(user_text)
    should_counsel = (
        (intent_name in CAREER_GUIDANCE_INTENTS and intent_result.get("resolved"))
        or (intent_category == "career_guidance" and domain_id_precheck is not None)
        or (intent_name is None and domain_id_precheck is not None)
    )
    if should_counsel:
        domain_id = domain_id_precheck
        base_reply = build_career_start_reply(user_text, domain_id, career_kb, rules)

        # Create counseling intake session
        _set_session(sender, {
            "type": "counseling_intake",
            "intake_step": 0,
            "target_domain": domain_id,
            "intake_data": {"target_domain": domain_id or ""},
            "original_message": user_text[:200],
        })

        polished_reply = polish_whatsapp_reply(base_reply, user_text, rules_context)
        final_reply = after_hours_prefix + polished_reply
        send_result = send_whatsapp_message(sender, final_reply)

        # Capture lead for career interest
        _save_lead({
            "phone": sender,
            "message": user_text[:200],
            "intent": intent_name,
            "domain": domain_id,
            "status": "counseling_started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return JSONResponse({
            "status": "ok",
            "intent": intent_name,
            "intent_category": "career_guidance",
            "domain": domain_id,
            "counseling_started": True,
            "base_reply": base_reply,
            "polished_reply": final_reply,
            "send_result": send_result,
        })

    # --- Admissions / general intents -> rule engine ---
    base_reply = build_base_reply(intent_result, entity_result, rules, user_text)

    # --- Create session if bot asked "Which course?" ---
    rule = intent_result.get("rule")
    if (
        rule
        and rule.get("course_entity_required")
        and entity_result.get("course") is None
        and not rule.get("all_courses_template")
    ):
        _set_session(sender, {
            "type": "pending_course",
            "pending_intent": rule["intent"],
        })

    # --- Capture lead if unresolved ---
    if not intent_result.get("resolved") and intent_result.get("intent") is None:
        _save_lead({
            "phone": sender,
            "message": user_text[:200],
            "intent": intent_result.get("intent"),
            "course_id": entity_result.get("course_id"),
            "status": "new",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # --- Polish with Sarvam LLM ---
    polished_reply = polish_whatsapp_reply(base_reply, user_text, rules_context)
    final_reply = after_hours_prefix + polished_reply

    # --- Send reply ---
    send_result = send_whatsapp_message(sender, final_reply)

    return JSONResponse({
        "status": "ok",
        "intent": intent_result.get("intent"),
        "confidence": intent_result.get("confidence", 0),
        "course_id": entity_result.get("course_id"),
        "intent_category": intent_result.get("intent_category", "general"),
        "resolved": intent_result.get("resolved", False),
        "after_hours": after_hours,
        "base_reply": base_reply,
        "polished_reply": final_reply,
        "send_result": send_result,
    })


# ---------------------------------------------------------------------------
# Counseling intake handler (multi-turn state machine)
# ---------------------------------------------------------------------------
def _handle_counseling_intake(
    sender: str,
    user_text: str,
    session: dict,
    rules: dict,
    rules_context: str,
    career_kb: dict,
    after_hours_prefix: str,
) -> JSONResponse:
    """
    Handle a user in an active counseling intake session.
    Steps through 4 questions, then delivers the roadmap.
    """
    intake_step = session.get("intake_step", 0)
    intake_data = session.get("intake_data", {})
    domain_id = session.get("target_domain")

    # Allow user to exit counseling
    if user_text.strip().upper() in ("CANCEL", "STOP", "EXIT", "QUIT"):
        _clear_session(sender)
        reply = "No problem! You can ask me anything else anytime. Type BOOK to schedule a counseling call."
        send_whatsapp_message(sender, after_hours_prefix + reply)
        return JSONResponse({"status": "ok", "counseling": "cancelled"})

    # Record answer for current step
    if intake_step < len(INTAKE_FIELDS):
        field = INTAKE_FIELDS[intake_step]
        parsed = parse_intake_answer(intake_step, user_text, career_kb)
        intake_data[field] = parsed
        intake_step += 1

    # If more questions remain, ask the next one
    if intake_step < len(INTAKE_FIELDS):
        next_q = get_intake_question(intake_step, career_kb)
        if not next_q:
            next_q = f"Tell me about your {INTAKE_FIELDS[intake_step]}."

        _set_session(sender, {
            "type": "counseling_intake",
            "intake_step": intake_step,
            "target_domain": domain_id,
            "intake_data": intake_data,
            "original_message": session.get("original_message", ""),
        })

        polished = polish_whatsapp_reply(next_q, user_text, rules_context)
        send_whatsapp_message(sender, after_hours_prefix + polished)

        return JSONResponse({
            "status": "ok",
            "counseling": "intake_in_progress",
            "step": intake_step,
            "next_field": INTAKE_FIELDS[intake_step],
        })

    # --- All questions answered -> deliver roadmap ---
    _clear_session(sender)

    # If no domain was detected earlier, try again from intake data
    if not domain_id:
        combined = session.get("original_message", "") + " " + " ".join(intake_data.values())
        domain_id = detect_career_domain(combined)

    # Default to ai_data_science_beginner if still unknown
    if not domain_id:
        domain_id = "ai_data_science_beginner"

    roadmap = get_roadmap_template(domain_id, career_kb)
    if not roadmap:
        # Fallback: suggest our courses
        fallback = (
            "Thanks for sharing your details! Based on your profile, "
            "I recommend starting with our foundational courses:\n\n"
        )
        for c in rules.get("courses", [])[:3]:
            fallback += f"- {c['name']} (INR {c['fees']}, {c['duration_weeks']} wks)\n"
        fallback += f"\nBook a free counseling call for personalized advice: {rules.get('booking_link', '')}"
        send_whatsapp_message(sender, after_hours_prefix + fallback)
        return JSONResponse({"status": "ok", "counseling": "complete_fallback"})

    # Format roadmap
    base_roadmap = format_roadmap_for_whatsapp(roadmap, intake_data, rules)
    intake_summary = build_intake_summary(intake_data)

    # Tailor with LLM
    original_msg = session.get("original_message", "")
    tailored = tailor_roadmap(base_roadmap, intake_summary, original_msg, rules_context)

    final_reply = after_hours_prefix + tailored
    send_result = send_whatsapp_message(sender, final_reply)

    # Update lead with completed counseling
    _save_lead({
        "phone": sender,
        "message": original_msg[:200],
        "intent": "career_roadmap",
        "domain": domain_id,
        "intake_data": intake_data,
        "status": "counseling_complete",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return JSONResponse({
        "status": "ok",
        "counseling": "complete",
        "domain": domain_id,
        "intake_data": intake_data,
        "base_roadmap_length": len(base_roadmap),
        "send_result": send_result,
    })


# ---------------------------------------------------------------------------
# POST /whatsapp/send — Manual send (admin-only)
# ---------------------------------------------------------------------------
@app.post("/whatsapp/send")
async def send_message_endpoint(req: SendMessageRequest, request: Request):
    _check_admin_auth(request)
    result = send_whatsapp_message(req.to, req.message)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# GET /whatsapp/test-template — Send hello_world template (delivery test)
# ---------------------------------------------------------------------------
@app.get("/whatsapp/test-template")
async def test_template(to: str):
    """Send the pre-approved hello_world template to verify delivery pipeline."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": "hello_world",
            "language": {"code": "en_US"},
        },
    }
    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"
        f"/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = http_requests.post(url, json=payload, headers=headers, timeout=15)
    logger.info("Template test to %s — status %d — body: %s", to, resp.status_code, resp.text)
    return JSONResponse({"status_code": resp.status_code, "body": resp.json()})


# ---------------------------------------------------------------------------
# GET /leads — View captured leads (admin-only)
# ---------------------------------------------------------------------------
@app.get("/leads")
async def get_leads(request: Request, limit: int = 50):
    """Return the most recent leads (newest first)."""
    _check_admin_auth(request)
    leads = _load_leads()
    leads.reverse()
    return JSONResponse({"total": len(leads), "leads": leads[:limit]})


# ---------------------------------------------------------------------------
# Helper: send via WhatsApp Cloud API
# ---------------------------------------------------------------------------
def send_whatsapp_message(to: str, text: str) -> dict:
    """Send a text message via WhatsApp Cloud API (or log in demo mode)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    if DEMO_MODE:
        logger.info("[DEMO] Would send to %s: %s", _mask_phone(to), text[:100])
        return {"status": "demo", "payload": payload}

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"
        f"/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        resp = http_requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code >= 400:
            # Log Meta's FULL error body before raising — this is the only
            # way to see the real error code (e.g. 131030 = recipient not
            # in allowed list, 190 = expired token, etc.)
            try:
                error_body = resp.json()
            except ValueError:
                error_body = resp.text
            logger.error(
                "WhatsApp API error %d for %s — Meta response: %s",
                resp.status_code, _mask_phone(to), json.dumps(error_body, indent=2),
            )
            resp.raise_for_status()
        logger.info("Message sent to %s (status %d).", _mask_phone(to), resp.status_code)
        return {"status": "sent", "whatsapp_response": resp.json()}
    except http_requests.exceptions.Timeout:
        logger.error("WhatsApp send timed out for %s.", _mask_phone(to))
        return {"status": "error", "detail": "Timeout"}
    except http_requests.exceptions.RequestException as exc:
        logger.error("WhatsApp send failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    rules = _get_rules()
    career_kb = _get_career_kb()
    logger.info("=== WhatsApp Career Counselor Bot v4.0 Started ===")
    logger.info("Demo mode: %s", DEMO_MODE)
    logger.info("Admin auth: %s", "ENABLED" if ADMIN_TOKEN else "DISABLED")
    logger.info("Webhook signature: %s", "ENABLED" if WHATSAPP_APP_SECRET else "DISABLED")
    logger.info("Business: %s", rules.get("business_name", "N/A"))
    logger.info("Courses: %d | Rules: %d | Roadmaps: %d",
                len(rules.get("courses", [])),
                len(rules.get("rules", [])),
                len(career_kb.get("roadmaps", [])))
    logger.info("Timezone: %s", BUSINESS_TIMEZONE)
