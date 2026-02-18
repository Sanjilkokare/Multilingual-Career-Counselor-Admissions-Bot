"""
rules_engine.py — Intent matching, course entity detection, counseling flow,
                   and reply builder.

The rule engine answers ONLY from configured rules in rules.json.
Career guidance uses curated templates from career_kb.json.
If no rule matches, it suggests closest topics and offers escalation.
All facts are grounded — zero hallucination by design.

v4: Career counselor upgrade — 3-category intents, intake state machine,
    roadmap builder, course_list intent, domain detection.
"""

import json
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = 15  # out of 100; below this -> unresolved

FALLBACK_REPLY = (
    "Thanks for your message! I wasn't able to find an exact match.\n\n"
    "Here are some things I can help with:\n"
    "{suggestions}\n\n"
    "Or type:\n"
    "- HUMAN -> talk to a person\n"
    "- BOOK -> schedule a free counseling call\n"
    "- ENROLL -> get the enrollment form"
)

COURSE_CLARIFY_TEMPLATE = (
    "I can help with {intent}! Which course are you asking about?\n\n"
    "{course_list}\n\n"
    "Reply with the course name or number."
)

# Career guidance intent categories (matched from rules.json intent_category)
CAREER_GUIDANCE_INTENTS = {
    "career_roadmap", "career_interest", "student_intro",
    "role_recommendation", "learning_plan", "project_portfolio",
    "resume_guidance", "interview_prep", "specialization_choice",
}

# Domain keyword map for detecting which career domain user is asking about
DOMAIN_KEYWORD_MAP = {
    "ai_data_science_beginner": [
        "data science", "data analyst", "data analysis", "ai", "artificial intelligence",
    ],
    "ml_engineer": [
        "ml engineer", "machine learning engineer", "mlops", "ml ops",
    ],
    "deep_learning_specialist": [
        "deep learning", "dl", "computer vision", "cv engineer", "nlp engineer",
        "neural network", "cnn", "rnn",
    ],
    "llm_rag_engineer": [
        "llm", "rag", "rag developer", "rag engineer", "llm engineer",
        "large language model", "chatbot developer",
    ],
    "agentic_ai_engineer": [
        "agentic ai", "ai agent", "agent developer", "agent engineer",
        "autonomous agent", "crew ai", "crewai", "autogen",
    ],
    "voicebot_developer": [
        "voicebot", "voice bot", "voice assistant", "speech", "stt", "tts",
        "conversational ai", "ivr",
    ],
    "prompt_engineering": [
        "prompt engineer", "prompt engineering", "prompting",
    ],
    "cybersecurity": [
        "cybersecurity", "cyber security", "ethical hacking", "hacking",
        "penetration testing", "pentest", "security analyst", "soc analyst",
        "infosec", "information security",
    ],
    "cloud_devops": [
        "cloud", "devops", "dev ops", "aws", "azure", "gcp",
        "kubernetes", "k8s", "docker", "terraform", "sre",
        "site reliability", "cloud engineer", "platform engineer",
    ],
    "web_dev": [
        "web development", "web dev", "frontend", "front end", "backend",
        "full stack", "fullstack", "mern", "mean", "react", "angular",
        "vue", "html", "css", "javascript", "web developer",
        "app development", "app dev", "mobile dev",
    ],
    "blockchain": [
        "blockchain", "web3", "solidity", "smart contract",
        "crypto", "ethereum", "defi", "nft",
    ],
}


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation (keep spaces), collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)  # replace punctuation with space
    text = re.sub(r"\s+", " ", text)       # collapse multiple spaces
    return text


# ---------------------------------------------------------------------------
# Word-boundary keyword matching (fixes false positives)
# ---------------------------------------------------------------------------
def _keyword_in_text(keyword: str, text: str) -> bool:
    """
    Check if keyword appears in text using word-boundary matching.
    This prevents 'hi' from matching 'which', 'ok' from matching 'booking'.
    """
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return bool(re.search(pattern, text))


# ---------------------------------------------------------------------------
# Load rules
# ---------------------------------------------------------------------------
def load_rules(path: str = "rules.json") -> dict:
    """Read and parse the rules.json file."""
    rules_path = Path(path)
    if not rules_path.exists():
        raise FileNotFoundError(f"Rules file not found: {rules_path}")

    with open(rules_path, "r", encoding="utf-8") as f:
        rules = json.load(f)

    logger.info(
        "Loaded %d rules, %d courses from %s",
        len(rules.get("rules", [])),
        len(rules.get("courses", [])),
        path,
    )
    return rules


# ---------------------------------------------------------------------------
# Load career knowledge base
# ---------------------------------------------------------------------------
_career_kb_cache: dict = {}
_career_kb_mtime: float = 0.0


def load_career_kb(path: str = "career_kb.json") -> dict:
    """Read and parse career_kb.json with mtime caching."""
    global _career_kb_cache, _career_kb_mtime

    kb_path = Path(path)
    if not kb_path.exists():
        logger.warning("Career KB not found: %s", path)
        return {}

    try:
        current_mtime = os.path.getmtime(path)
    except OSError:
        if _career_kb_cache:
            return _career_kb_cache
        return {}

    if current_mtime != _career_kb_mtime or not _career_kb_cache:
        with open(kb_path, "r", encoding="utf-8") as f:
            _career_kb_cache = json.load(f)
        _career_kb_mtime = current_mtime
        logger.info("Career KB loaded (%d roadmaps).",
                     len(_career_kb_cache.get("roadmaps", [])))

    return _career_kb_cache


# ---------------------------------------------------------------------------
# Synonym expansion (uses word-boundary matching)
# ---------------------------------------------------------------------------
def _expand_with_synonyms(message_norm: str, synonyms: dict) -> str:
    """
    Append synonym group words to the message if any synonym is found.
    Uses word-boundary matching to avoid false positives.
    """
    extra_tokens = []
    for _group_name, syn_list in synonyms.items():
        for syn in syn_list:
            if _keyword_in_text(syn, message_norm):
                extra_tokens.extend(syn_list)
                break  # one match per group is enough
    if extra_tokens:
        return message_norm + " " + " ".join(extra_tokens)
    return message_norm


# ---------------------------------------------------------------------------
# Course entity detection (word-boundary matching)
# ---------------------------------------------------------------------------
def detect_course_entity(message: str, rules_dict: dict) -> dict:
    """
    Detect which course the user is asking about.

    Returns:
        {
            "course_id": str or None,
            "course": dict or None,      # full course object
            "confidence": int (0-100),
            "matched_keywords": list[str]
        }
    """
    message_norm = normalize_text(message)
    course_kw_map = rules_dict.get("course_keywords", {})
    courses = {c["course_id"]: c for c in rules_dict.get("courses", [])}

    best = {"course_id": None, "course": None, "confidence": 0, "matched_keywords": []}

    for course_id, keywords in course_kw_map.items():
        matched = [kw for kw in keywords if _keyword_in_text(kw, message_norm)]
        if not matched:
            continue
        score = int((len(matched) / len(keywords)) * 100)
        if score > best["confidence"]:
            best = {
                "course_id": course_id,
                "course": courses.get(course_id),
                "confidence": score,
                "matched_keywords": matched,
            }

    if best["course_id"]:
        logger.info(
            "Course entity: %s (confidence %d%%, keywords: %s)",
            best["course_id"], best["confidence"], best["matched_keywords"],
        )
    return best


# ---------------------------------------------------------------------------
# Session-aware course resolution (for multi-turn memory)
# ---------------------------------------------------------------------------
def resolve_course_from_reply(message: str, rules_dict: dict) -> dict | None:
    """
    When the user replies with a number (e.g. '1', '2') or a short course
    name to a 'Which course?' clarifying question, resolve it to a course.

    Returns the course dict or None.
    """
    message_stripped = message.strip()
    courses = rules_dict.get("courses", [])

    # Check if it's a number like "1", "2", etc.
    if message_stripped.isdigit():
        idx = int(message_stripped) - 1
        if 0 <= idx < len(courses):
            return courses[idx]
        return None

    # Otherwise try normal entity detection
    entity = detect_course_entity(message, rules_dict)
    return entity.get("course")


# ---------------------------------------------------------------------------
# Intent matching (weighted keyword scoring with word-boundary + synonyms)
# ---------------------------------------------------------------------------
def match_intent(message: str, rules_dict: dict) -> dict:
    """
    Match user message against intent rules using keyword scoring
    with synonym expansion and word-boundary matching.

    Returns:
        {
            "intent": str or None,
            "confidence": int (0-100),
            "matched_keywords": list[str],
            "rule": dict or None,
            "resolved": bool,
            "intent_category": str  # "admissions", "career_guidance", or "general"
        }
    """
    message_norm = normalize_text(message)
    synonyms = rules_dict.get("synonyms", {})
    expanded = _expand_with_synonyms(message_norm, synonyms)

    best = {
        "intent": None,
        "confidence": 0,
        "matched_keywords": [],
        "rule": None,
        "resolved": False,
        "intent_category": "general",
    }

    for rule in rules_dict.get("rules", []):
        matched = []
        for kw in rule["keywords"]:
            # Check both original and expanded message with word-boundary
            if _keyword_in_text(kw, message_norm) or _keyword_in_text(kw, expanded):
                matched.append(kw)

        if not matched:
            continue

        # Score: (matched / total) * 100, with bonus for multiple matches
        base_score = (len(matched) / len(rule["keywords"])) * 100
        multi_bonus = min(len(matched) * 5, 25)  # up to 25 bonus points
        score = int(min(base_score + multi_bonus, 100))

        if score > best["confidence"]:
            category = rule.get("intent_category", "admissions")
            if rule["intent"] in ("greeting", "thanks", "contact_human", "book_call"):
                category = "general"
            best = {
                "intent": rule["intent"],
                "confidence": score,
                "matched_keywords": matched,
                "rule": rule,
                "resolved": score >= CONFIDENCE_THRESHOLD,
                "intent_category": category,
            }

    if best["intent"]:
        logger.info(
            "Intent: '%s' (confidence %d%%, resolved: %s, category: %s, keywords: %s)",
            best["intent"], best["confidence"], best["resolved"],
            best["intent_category"], best["matched_keywords"],
        )
    else:
        logger.info("No intent matched for: %s", message[:80])

    return best


# ---------------------------------------------------------------------------
# Detect career domain from user message
# ---------------------------------------------------------------------------
def detect_career_domain(message: str) -> Optional[str]:
    """
    Detect which career domain the user is interested in.
    Returns a domain_id from DOMAIN_KEYWORD_MAP, or None.
    """
    message_norm = normalize_text(message)
    best_domain = None
    best_score = 0

    for domain_id, keywords in DOMAIN_KEYWORD_MAP.items():
        matched = [kw for kw in keywords if _keyword_in_text(kw, message_norm)]
        if len(matched) > best_score:
            best_score = len(matched)
            best_domain = domain_id

    if best_domain:
        logger.info("Career domain detected: %s (score %d)", best_domain, best_score)
    return best_domain


def get_roadmap_template(domain_id: str, career_kb: dict) -> Optional[dict]:
    """Look up a roadmap template by domain_id from career_kb."""
    for roadmap in career_kb.get("roadmaps", []):
        if roadmap["domain_id"] == domain_id:
            return roadmap
    return None


# ---------------------------------------------------------------------------
# Counseling state machine
# ---------------------------------------------------------------------------
# States: "intake_0" through "intake_3" (one per question), then "complete"
INTAKE_FIELDS = ["background", "skills", "goal", "hours_per_week"]


def get_intake_question(step: int, career_kb: dict) -> Optional[str]:
    """Return the intake question for the given step (0-3)."""
    questions = career_kb.get("intake_questions", [])
    if 0 <= step < len(questions):
        return questions[step]["question"]
    return None


def parse_intake_answer(step: int, answer: str, career_kb: dict) -> str:
    """
    Parse user's answer (could be a number or text) into a clean value.
    Returns the option text if a number was given, or the raw answer.
    """
    questions = career_kb.get("intake_questions", [])
    if step >= len(questions):
        return answer.strip()

    options = questions[step].get("options", [])
    answer_stripped = answer.strip()

    # If user typed a number, map to option
    if answer_stripped.isdigit():
        idx = int(answer_stripped) - 1
        if 0 <= idx < len(options):
            return options[idx]

    # Otherwise return as-is
    return answer_stripped


def build_intake_summary(intake_data: dict) -> str:
    """Build a readable summary of intake answers."""
    lines = []
    labels = {
        "background": "Background",
        "skills": "Current Skills",
        "goal": "Goal",
        "hours_per_week": "Time/Week",
        "target_domain": "Interest Area",
    }
    for key, label in labels.items():
        val = intake_data.get(key, "")
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines) if lines else "No info collected"


# ---------------------------------------------------------------------------
# Roadmap formatter (template-first, no hallucination)
# ---------------------------------------------------------------------------
def format_roadmap_for_whatsapp(
    roadmap: dict,
    intake_data: dict,
    rules_dict: dict,
) -> str:
    """
    Build a WhatsApp-friendly roadmap reply from a curated template.
    This is the GROUNDED base — LLM only tailors tone afterward.
    """
    domain = roadmap.get("domain", "Unknown")
    subtitle = roadmap.get("subtitle", "")
    we_offer = roadmap.get("we_offer_this", True)
    honesty_note = roadmap.get("honesty_note", "")

    lines = []

    # Header
    lines.append(f"*Career Roadmap: {domain}*")
    if subtitle:
        lines.append(f"_{subtitle}_")
    lines.append("")

    # Honesty note for domains we don't teach
    if not we_offer and honesty_note:
        lines.append(f"Honest note: {honesty_note}")
        lines.append("")

    # Target roles
    roles = roadmap.get("target_roles", [])
    if roles:
        lines.append(f"Target roles: {', '.join(roles)}")
        lines.append("")

    # 4-stage path (compact for WhatsApp)
    lines.append("*Your Learning Path:*")
    for stage in roadmap.get("stages", []):
        stage_num = stage.get("stage", "?")
        title = stage.get("title", "")
        duration = stage.get("duration", "")
        topics = stage.get("topics", [])
        checkpoint = stage.get("checkpoint", "")

        lines.append(f"\nStage {stage_num}: {title} ({duration})")
        for t in topics[:4]:  # limit to 4 topics for WhatsApp brevity
            lines.append(f"  - {t}")
        if checkpoint:
            lines.append(f"  Checkpoint: {checkpoint}")

    # Suggested projects
    projects = roadmap.get("suggested_projects", [])
    if projects:
        lines.append("\n*Suggested Projects:*")
        for i, p in enumerate(projects, 1):
            lines.append(f"  {i}. {p}")

    # Time estimate
    time_est = roadmap.get("time_estimate_hours_per_week", {})
    if time_est:
        lines.append(f"\nRecommended: {time_est.get('recommended', '?')} hrs/week")

    # Course recommendation (truthful)
    related = roadmap.get("related_our_courses", [])
    if related:
        courses_by_id = {c["course_id"]: c for c in rules_dict.get("courses", [])}
        rec_lines = []
        for cid in related:
            c = courses_by_id.get(cid)
            if c:
                rec_lines.append(f"  - {c['name']} (INR {c['fees']}, {c['duration_weeks']} wks)")
        if rec_lines:
            if we_offer:
                lines.append("\n*Our relevant courses:*")
            else:
                lines.append("\n*Foundational courses that may help:*")
            lines.extend(rec_lines)

    # Disclaimer
    disclaimer = rules_dict.get(
        "career_guidance_disclaimer",
        "Note: Outcomes depend on your effort and opportunities."
    )
    lines.append(f"\n_{disclaimer}_")

    # CTA
    booking_link = rules_dict.get("booking_link", "")
    if booking_link:
        lines.append(f"\nWant a personalized plan? Book a free call: {booking_link}")
    lines.append("Type HUMAN to talk to a counselor.")

    return "\n".join(lines)


def build_career_start_reply(
    message: str,
    domain_id: Optional[str],
    career_kb: dict,
    rules_dict: dict,
) -> str:
    """
    When a career intent is detected, either:
    - Start intake flow (if domain is clear or broad)
    - Or ask which domain they're interested in
    """
    if domain_id:
        roadmap = get_roadmap_template(domain_id, career_kb)
        domain_name = roadmap.get("domain", domain_id) if roadmap else domain_id
        intro = (
            f"Great choice! I can help you plan a career path in *{domain_name}*.\n\n"
            "Let me ask a few quick questions to personalize your roadmap.\n\n"
        )
    else:
        intro = (
            "I'd love to help you with career guidance!\n\n"
            "Let me ask a few quick questions to give you a personalized roadmap.\n\n"
        )

    # Ask first intake question
    first_q = get_intake_question(0, career_kb)
    if first_q:
        return intro + first_q
    return intro + "What's your current background? (student / working / career switcher)"


# ---------------------------------------------------------------------------
# Suggestion mode (when unresolved)
# ---------------------------------------------------------------------------
def get_suggestions(message: str, rules_dict: dict) -> dict:
    """
    When the main match is unresolved, return top suggested intents
    and top suggested courses.
    """
    message_norm = normalize_text(message)
    synonyms = rules_dict.get("synonyms", {})
    expanded = _expand_with_synonyms(message_norm, synonyms)

    # Score all intents
    scored_intents = []
    for rule in rules_dict.get("rules", []):
        matched = [kw for kw in rule["keywords"] if _keyword_in_text(kw, expanded)]
        score = len(matched)
        if score > 0:
            scored_intents.append({
                "intent": rule["intent"],
                "description": f"Ask about {rule['intent'].replace('_', ' ')}",
                "score": score,
            })

    scored_intents.sort(key=lambda x: x["score"], reverse=True)
    top_intents = scored_intents[:3]

    # If no intents matched at all, suggest popular ones
    if not top_intents:
        top_intents = [
            {"intent": "fees", "description": "Ask about course fees"},
            {"intent": "syllabus", "description": "Ask about course syllabus"},
            {"intent": "career_roadmap", "description": "Get a career roadmap"},
            {"intent": "demo_class", "description": "Book a free demo class"},
        ]

    # Score all courses
    course_kw_map = rules_dict.get("course_keywords", {})
    courses_list = rules_dict.get("courses", [])
    courses_by_id = {c["course_id"]: c for c in courses_list}

    scored_courses = []
    for cid, keywords in course_kw_map.items():
        matched = [kw for kw in keywords if _keyword_in_text(kw, expanded)]
        if matched:
            course = courses_by_id.get(cid, {})
            scored_courses.append({
                "course_id": cid,
                "name": course.get("name", cid),
                "score": len(matched),
            })

    scored_courses.sort(key=lambda x: x["score"], reverse=True)
    top_courses = scored_courses[:2]

    return {
        "suggested_intents": [{"intent": s["intent"], "description": s["description"]} for s in top_intents],
        "suggested_courses": [{"course_id": s["course_id"], "name": s["name"]} for s in top_courses],
    }


# ---------------------------------------------------------------------------
# After-hours check
# ---------------------------------------------------------------------------
def is_after_hours(rules_dict: dict, timezone_name: str = "Asia/Kolkata") -> bool:
    """
    Check if the current time falls outside business support hours.
    """
    schedule = rules_dict.get("support_schedule")
    if not schedule:
        return False

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        logger.warning("zoneinfo not available; skipping after-hours check.")
        return False

    try:
        tz = ZoneInfo(timezone_name)
    except KeyError:
        logger.warning("Unknown timezone '%s'; skipping after-hours check.", timezone_name)
        return False

    now = datetime.now(tz)
    day_name = now.strftime("%A")

    working_days = schedule.get("days", [])
    if day_name not in working_days:
        return True

    start_hour = schedule.get("start_hour", 0)
    start_minute = schedule.get("start_minute", 0)
    end_hour = schedule.get("end_hour", 23)
    end_minute = schedule.get("end_minute", 59)

    current_minutes = now.hour * 60 + now.minute
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute

    return current_minutes < start_minutes or current_minutes >= end_minutes


# ---------------------------------------------------------------------------
# Template rendering (supports course.* placeholders)
# ---------------------------------------------------------------------------
def _resolve_placeholder(key: str, context: dict) -> str:
    """Resolve a dotted placeholder against a flat or nested dict."""
    parts = key.split(".")
    value = context
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return "{" + key + "}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _render_template(template: str, context: dict) -> str:
    """Replace {placeholder} tokens with values from context dict."""
    def replacer(match):
        return _resolve_placeholder(match.group(1), context)
    return re.sub(r"\{(\w+(?:\.\w+)*)\}", replacer, template)


def _build_template_context(course: dict | None, rules_dict: dict) -> dict:
    """
    Merge course data + business data into a flat-ish context dict
    for template rendering.
    """
    ctx = dict(rules_dict)  # shallow copy

    if course:
        ctx["course"] = dict(course)

        # Pre-format syllabus for readability
        syllabus = course.get("syllabus_by_week", {})
        if syllabus:
            ctx["course"]["syllabus"] = "\n".join(
                f"  {week}: {topics}" for week, topics in syllabus.items()
            )

        # Pre-format projects list
        projects = course.get("projects", [])
        ctx["course"]["projects"] = " | ".join(projects)
        ctx["course"]["projects_list"] = "\n".join(
            f"  {i+1}. {p}" for i, p in enumerate(projects)
        )

    return ctx


# ---------------------------------------------------------------------------
# Build the base reply — grounded in rules.json ONLY
# ---------------------------------------------------------------------------
def build_base_reply(
    intent_result: dict,
    entity_result: dict,
    rules_dict: dict,
    message: str = "",
) -> str:
    """
    Produce a factual reply from matched intent + course entity.

    Logic:
    1. If no intent matched -> fallback with suggestions.
    2. If intent requires course entity but none detected -> clarifying question.
    3. If intent matched + has "all_courses_template" and no course -> show all.
    4. Normal case -> fill template with course + business data.
    """
    # --- Escalation shortcuts ---
    msg_upper = message.strip().upper()
    if msg_upper == "HUMAN":
        contact = rules_dict.get("human_contact", {})
        return (
            f"Connecting you with our team!\n\n"
            f"Phone: {contact.get('phone', 'N/A')}\n"
            f"Email: {contact.get('email', 'N/A')}\n"
            f"Available: {contact.get('available', 'N/A')}\n\n"
            f"A counselor will assist you shortly."
        )
    if msg_upper == "BOOK":
        return (
            f"Book a free counseling call here:\n"
            f"{rules_dict.get('booking_link', 'N/A')}\n\n"
            f"Or call us: {rules_dict.get('human_contact', {}).get('phone', 'N/A')}"
        )
    if msg_upper == "ENROLL":
        return (
            f"Enroll using this form:\n"
            f"{rules_dict.get('enroll_link', 'N/A')}\n\n"
            f"Need help choosing a course? Type BOOK to schedule a counseling call."
        )

    # --- No intent matched -> fallback ---
    if not intent_result.get("resolved") and intent_result.get("intent") is None:
        suggestions = get_suggestions(message, rules_dict)
        suggestion_lines = []
        for s in suggestions["suggested_intents"]:
            suggestion_lines.append(f"  - {s['description']}")
        for s in suggestions["suggested_courses"]:
            suggestion_lines.append(f"  - Ask about: {s['name']}")
        suggestion_text = "\n".join(suggestion_lines) if suggestion_lines else "  - Fees, syllabus, demo class, career roadmap"

        return FALLBACK_REPLY.format(suggestions=suggestion_text)

    rule = intent_result.get("rule", {})
    course = entity_result.get("course")
    needs_course = rule.get("course_entity_required", False)

    # --- Intent needs a course but none detected ---
    if needs_course and course is None:
        all_tmpl = rule.get("all_courses_template")
        if all_tmpl:
            all_fees_lines = []
            for c in rules_dict.get("courses", []):
                all_fees_lines.append(
                    f"  - {c['name']}: INR {c['fees']} ({c['duration_weeks']} weeks)"
                )
            ctx = dict(rules_dict)
            ctx["all_fees"] = "\n".join(all_fees_lines)
            return _render_template(all_tmpl, ctx)

        courses = rules_dict.get("courses", [])
        course_list = "\n".join(
            f"  {i+1}. {c['name']}" for i, c in enumerate(courses)
        )
        intent_label = rule.get("intent", "that").replace("_", " ")
        return COURSE_CLARIFY_TEMPLATE.format(
            intent=intent_label,
            course_list=course_list,
        )

    # --- Career guidance intents with empty templates should not render empty ---
    template = rule.get("reply_template", "")
    if not template.strip():
        # This is a career guidance intent that should have been routed
        # to the counseling flow. Provide a helpful fallback.
        booking = rules_dict.get("booking_link", "")
        return (
            "I'd love to help you with career guidance!\n\n"
            "You can:\n"
            "- Ask about our courses (fees, syllabus, batches)\n"
            "- Get a career roadmap (just tell me your interest area!)\n"
            f"- Book a free counseling call: {booking}\n\n"
            "What would you like to know?"
        )

    # --- Normal: fill template ---
    ctx = _build_template_context(course, rules_dict)
    return _render_template(template, ctx)


# ---------------------------------------------------------------------------
# Context builder for LLM polisher
# ---------------------------------------------------------------------------
def build_rules_context(rules_dict: dict) -> str:
    """Compact summary of business info for the LLM polish prompt."""
    lines = [
        f"Business: {rules_dict.get('business_name', 'N/A')}",
        f"Hours: {rules_dict.get('support_hours', rules_dict.get('business_hours', 'N/A'))}",
        f"Mode: {rules_dict.get('mode', 'N/A')}",
    ]

    for c in rules_dict.get("courses", []):
        lines.append(f"Course: {c['name']} -- INR {c['fees']} -- {c['duration_weeks']}wk")

    contact = rules_dict.get("human_contact", {})
    if contact.get("phone"):
        lines.append(f"Contact: {contact['phone']}")

    booking = rules_dict.get("booking_link", "")
    if booking:
        lines.append(f"Booking: {booking}")

    return "\n".join(lines)
