import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv

from rules_engine import (
    load_rules,
    load_career_kb,
    match_intent,
    detect_course_entity,
    detect_career_domain,
    build_base_reply,
    build_rules_context,
    build_career_start_reply,
    format_roadmap_for_whatsapp,
    build_intake_summary,
    get_roadmap_template,
    get_suggestions,
    CAREER_GUIDANCE_INTENTS,
)
from sarvam_client import polish_whatsapp_reply, tailor_roadmap

load_dotenv()

API_KEY = os.getenv("SARVAM_API_KEY")
BASE_URL = "https://api.sarvam.ai/v1"

headers = {
    "api-subscription-key": API_KEY,
    "Content-Type": "application/json",
}

RULES_PATH = "rules.json"
CAREER_KB_PATH = "career_kb.json"
LEADS_PATH = "leads.json"
SESSIONS_PATH = "sessions.json"

st.set_page_config(page_title="Sarvam Tech Academy", page_icon="🎓", layout="wide")
st.title("Sarvam Tech Academy — Career Counselor & Admissions Bot")

# ============================================================================
# TABS
# ============================================================================
tab_chat, tab_admin, tab_career, tab_sim = st.tabs([
    "💬 Chatbot",
    "📱 Admin Panel",
    "🗺️ Career Templates",
    "🧪 Simulator",
])

# ============================================================================
# TAB 1 — Chatbot (original functionality preserved)
# ============================================================================
with tab_chat:
    st.header("Multilingual Chatbot")

    user_input = st.text_input("Ask something:", key="chat_input")

    if st.button("Send", key="chat_send"):
        if not user_input.strip():
            st.warning("Please enter a message.")
        else:
            data = {
                "model": "sarvam-m",
                "messages": [
                    {"role": "system", "content": "You are a helpful multilingual assistant."},
                    {"role": "user", "content": user_input},
                ],
            }

            response = requests.post(
                f"{BASE_URL}/chat/completions",
                json=data,
                headers=headers,
            )

            result = response.json()

            if "choices" in result:
                reply = result["choices"][0]["message"]["content"]
                st.success(reply)
            else:
                st.error(result)

# ============================================================================
# TAB 2 — Admin Panel
# ============================================================================
with tab_admin:
    st.header("WhatsApp Bot Admin Panel")

    # --- Load rules ---
    try:
        rules = load_rules(RULES_PATH)
    except Exception as e:
        st.error(f"Could not load rules.json: {e}")
        rules = None

    if rules:
        # ==================================================================
        # Status bar
        # ==================================================================
        _lead_count = 0
        _counseling_count = 0
        if os.path.exists(LEADS_PATH):
            try:
                with open(LEADS_PATH, "r", encoding="utf-8") as _f:
                    _leads_data = json.load(_f)
                    _lead_count = len(_leads_data)
                    _counseling_count = sum(
                        1 for l in _leads_data
                        if l.get("status") in ("counseling_started", "counseling_complete")
                    )
            except (json.JSONDecodeError, IOError):
                pass

        _session_count = 0
        if os.path.exists(SESSIONS_PATH):
            try:
                with open(SESSIONS_PATH, "r", encoding="utf-8") as _f:
                    _session_count = len(json.load(_f))
            except (json.JSONDecodeError, IOError):
                pass

        _career_kb = {}
        try:
            _career_kb = load_career_kb(CAREER_KB_PATH)
        except Exception:
            pass

        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            st.metric("Courses", len(rules.get("courses", [])))
        with sc2:
            st.metric("Roadmaps", len(_career_kb.get("roadmaps", [])))
        with sc3:
            st.metric("Active Sessions", _session_count)
        with sc4:
            st.metric("Leads / Counseling", f"{_lead_count} / {_counseling_count}")

        st.markdown("---")

        # ==================================================================
        # Section 1: Business Configuration
        # ==================================================================
        st.subheader("Business Configuration")

        col1, col2 = st.columns(2)

        with col1:
            new_name = st.text_input(
                "Business Name", value=rules.get("business_name", ""), key="biz_name"
            )
            new_hours = st.text_input(
                "Support Hours", value=rules.get("support_hours", ""), key="biz_hours"
            )
            new_mode = st.text_input(
                "Mode (Online/Offline/Hybrid)", value=rules.get("mode", ""), key="biz_mode"
            )
            new_booking = st.text_input(
                "Booking Link", value=rules.get("booking_link", ""), key="biz_booking"
            )

        with col2:
            new_enroll = st.text_input(
                "Enrollment Link", value=rules.get("enroll_link", ""), key="biz_enroll"
            )
            location = rules.get("location", {})
            new_address = st.text_input(
                "Address", value=location.get("address", ""), key="loc_addr"
            )
            contact = rules.get("human_contact", {})
            new_phone = st.text_input(
                "Contact Phone", value=contact.get("phone", ""), key="con_phone"
            )
            new_email = st.text_input(
                "Contact Email", value=contact.get("email", ""), key="con_email"
            )

        if st.button("Save Business Info", key="save_biz"):
            rules["business_name"] = new_name
            rules["support_hours"] = new_hours
            rules["mode"] = new_mode
            rules["booking_link"] = new_booking
            rules["enroll_link"] = new_enroll
            rules["location"]["address"] = new_address
            rules["human_contact"]["phone"] = new_phone
            rules["human_contact"]["email"] = new_email
            try:
                with open(RULES_PATH, "w", encoding="utf-8") as f:
                    json.dump(rules, f, indent=2, ensure_ascii=False)
                st.success("Business info saved!")
            except Exception as e:
                st.error(f"Save failed: {e}")

        st.markdown("---")

        # ==================================================================
        # Section 2: Course CMS
        # ==================================================================
        st.subheader("Course CMS")

        courses = rules.get("courses", [])
        if not courses:
            st.info("No courses found in rules.json.")
        else:
            for i, course in enumerate(courses):
                with st.expander(f"📘 {course['name']} ({course['course_id']})"):
                    c1, c2 = st.columns(2)
                    with c1:
                        courses[i]["name"] = st.text_input(
                            "Course Name", value=course["name"], key=f"cn_{i}"
                        )
                        courses[i]["fees"] = st.text_input(
                            "Fees (INR)", value=course["fees"], key=f"cf_{i}"
                        )
                        courses[i]["duration_weeks"] = st.number_input(
                            "Duration (weeks)", value=course["duration_weeks"],
                            min_value=1, key=f"cd_{i}"
                        )
                        courses[i]["level"] = st.selectbox(
                            "Level", ["Beginner", "Intermediate", "Advanced"],
                            index=["Beginner", "Intermediate", "Advanced"].index(
                                course.get("level", "Beginner")
                            ),
                            key=f"cl_{i}",
                        )
                    with c2:
                        courses[i]["prerequisites"] = st.text_area(
                            "Prerequisites", value=course.get("prerequisites", ""),
                            height=80, key=f"cp_{i}"
                        )
                        batch = course.get("batch_schedule", {})
                        courses[i]["batch_schedule"]["weekday"] = st.text_input(
                            "Weekday Batch", value=batch.get("weekday", ""), key=f"bw_{i}"
                        )
                        courses[i]["batch_schedule"]["weekend"] = st.text_input(
                            "Weekend Batch", value=batch.get("weekend", ""), key=f"bwe_{i}"
                        )
                        courses[i]["demo_class_available"] = st.checkbox(
                            "Demo Class Available",
                            value=course.get("demo_class_available", True),
                            key=f"dc_{i}",
                        )

                    # Syllabus (read-only display for simplicity)
                    st.markdown("**Syllabus:**")
                    syllabus = course.get("syllabus_by_week", {})
                    for week, topics in syllabus.items():
                        st.text(f"  {week}: {topics}")

                    # Projects
                    st.markdown("**Projects:**")
                    for p in course.get("projects", []):
                        st.text(f"  - {p}")

            if st.button("Save All Courses", key="save_courses"):
                rules["courses"] = courses
                try:
                    with open(RULES_PATH, "w", encoding="utf-8") as f:
                        json.dump(rules, f, indent=2, ensure_ascii=False)
                    st.success("Courses saved!")
                except Exception as e:
                    st.error(f"Save failed: {e}")

        st.markdown("---")

        # ==================================================================
        # Section 3: FAQ Intent Rules
        # ==================================================================
        st.subheader("FAQ Intent Rules")

        for i, rule in enumerate(rules.get("rules", [])):
            category = rule.get("intent_category", "admissions")
            if rule["intent"] in ("greeting", "thanks", "contact_human", "book_call"):
                category = "general"
            badge = {"admissions": "📋", "career_guidance": "🗺️", "general": "💬"}.get(category, "")

            with st.expander(f"{badge} {rule['intent']} {'(course required)' if rule.get('course_entity_required') else ''}"):
                st.write(f"**Category:** {category}")
                st.write(f"**Keywords:** {', '.join(rule['keywords'])}")
                if rule.get("reply_template"):
                    st.code(rule["reply_template"], language="text")
                else:
                    st.info("(Handled by counseling flow engine)")

        st.markdown("---")

        # ==================================================================
        # Section 4: Leads Viewer + Analytics
        # ==================================================================
        st.subheader("Leads & Analytics")

        if os.path.exists(LEADS_PATH):
            try:
                with open(LEADS_PATH, "r", encoding="utf-8") as f:
                    leads = json.load(f)
            except (json.JSONDecodeError, IOError):
                leads = []

            if leads:
                # Analytics
                st.write(f"Total leads: **{len(leads)}**")

                # Domain distribution
                domain_counts: dict[str, int] = {}
                for lead in leads:
                    d = lead.get("domain") or lead.get("intent") or "unknown"
                    domain_counts[d] = domain_counts.get(d, 0) + 1

                if domain_counts:
                    st.write("**Most asked domains/intents:**")
                    sorted_domains = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)
                    for domain, count in sorted_domains[:8]:
                        st.write(f"  - {domain}: {count}")

                st.markdown("---")

                # Lead list (last 20)
                for lead in reversed(leads[-20:]):
                    with st.expander(
                        f"📋 {lead.get('phone', '?')[-4:]}.. — {lead.get('status', 'new')} — {lead.get('timestamp', '?')[:19]}"
                    ):
                        st.write(f"**Message:** {lead.get('message', 'N/A')}")
                        st.write(f"**Intent:** {lead.get('intent') or 'None'}")
                        st.write(f"**Domain:** {lead.get('domain') or 'None'}")
                        st.write(f"**Course:** {lead.get('course_id') or 'None'}")
                        st.write(f"**Status:** {lead.get('status', 'new')}")
                        if lead.get("intake_data"):
                            st.write("**Intake Data:**")
                            st.json(lead["intake_data"])
            else:
                st.info("No leads captured yet.")
        else:
            st.info("No leads file found.")


# ============================================================================
# TAB 3 — Career Templates Editor
# ============================================================================
with tab_career:
    st.header("Career Roadmap Templates")
    st.caption("Edit curated career roadmap templates in career_kb.json. Changes are saved to disk and hot-reloaded by the server.")

    try:
        career_kb = load_career_kb(CAREER_KB_PATH)
    except Exception as e:
        st.error(f"Could not load career_kb.json: {e}")
        career_kb = {}

    if career_kb:
        roadmaps = career_kb.get("roadmaps", [])

        for ri, rm in enumerate(roadmaps):
            we_offer = rm.get("we_offer_this", True)
            offer_tag = "" if we_offer else " [External Domain]"
            with st.expander(f"🗺️ {rm['domain']}{offer_tag} ({rm['domain_id']})"):
                # Basic info
                c1, c2 = st.columns(2)
                with c1:
                    roadmaps[ri]["domain"] = st.text_input(
                        "Domain Name", value=rm["domain"], key=f"rd_{ri}"
                    )
                    roadmaps[ri]["subtitle"] = st.text_input(
                        "Subtitle", value=rm.get("subtitle", ""), key=f"rs_{ri}"
                    )
                with c2:
                    roles = rm.get("target_roles", [])
                    roadmaps[ri]["target_roles"] = st.text_input(
                        "Target Roles (comma-separated)",
                        value=", ".join(roles),
                        key=f"rr_{ri}",
                    ).split(", ")

                    time_est = rm.get("time_estimate_hours_per_week", {})
                    roadmaps[ri]["time_estimate_hours_per_week"]["recommended"] = st.number_input(
                        "Recommended hrs/week",
                        value=time_est.get("recommended", 15),
                        min_value=1, key=f"rt_{ri}",
                    )

                # Stages (read-only for simplicity, can be edited as JSON)
                st.markdown("**Learning Stages:**")
                for stage in rm.get("stages", []):
                    st.text(f"  Stage {stage['stage']}: {stage['title']} ({stage['duration']})")
                    for t in stage.get("topics", []):
                        st.text(f"    - {t}")

                # Projects
                st.markdown("**Suggested Projects:**")
                for p in rm.get("suggested_projects", []):
                    st.text(f"  - {p}")

                # Related courses
                related = rm.get("related_our_courses", [])
                st.write(f"**Related courses we offer:** {', '.join(related)}")

                # Honesty note
                if rm.get("honesty_note"):
                    st.info(f"Honesty note: {rm['honesty_note']}")

        if st.button("Save Career Templates", key="save_career_kb"):
            career_kb["roadmaps"] = roadmaps
            try:
                with open(CAREER_KB_PATH, "w", encoding="utf-8") as f:
                    json.dump(career_kb, f, indent=2, ensure_ascii=False)
                st.success("Career templates saved!")
            except Exception as e:
                st.error(f"Save failed: {e}")

        st.markdown("---")

        # JSON editor for advanced editing
        st.subheader("Advanced: Edit Raw JSON")
        st.caption("For editing stages, topics, and other nested fields.")

        raw_json = st.text_area(
            "career_kb.json (raw)",
            value=json.dumps(career_kb, indent=2, ensure_ascii=False),
            height=400,
            key="raw_career_kb",
        )

        if st.button("Save Raw JSON", key="save_raw_career"):
            try:
                parsed = json.loads(raw_json)
                with open(CAREER_KB_PATH, "w", encoding="utf-8") as f:
                    json.dump(parsed, f, indent=2, ensure_ascii=False)
                st.success("Raw JSON saved!")
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")
            except Exception as e:
                st.error(f"Save failed: {e}")

    else:
        st.warning("career_kb.json not found. Create it to enable career roadmap templates.")


# ============================================================================
# TAB 4 — Test Simulator (Admissions + Career Counseling)
# ============================================================================
with tab_sim:
    st.header("Test Message Simulator")
    st.caption(
        "Simulate WhatsApp messages. Tests both admissions FAQs and career counseling flows."
    )

    # --- Load data ---
    try:
        sim_rules = load_rules(RULES_PATH)
        sim_career_kb = load_career_kb(CAREER_KB_PATH)
    except Exception as e:
        st.error(f"Could not load data: {e}")
        sim_rules = None
        sim_career_kb = {}

    if sim_rules:
        # --- Mode selector ---
        sim_mode = st.radio(
            "Simulation mode:",
            ["Admissions FAQ", "Career Counseling (full flow)"],
            horizontal=True,
            key="sim_mode",
        )

        if sim_mode == "Admissions FAQ":
            # =============================================
            # Admissions FAQ Simulator (existing)
            # =============================================
            test_msg = st.text_input(
                "Simulated message:",
                key="test_msg",
                placeholder="e.g. Python course ka fee kitna hai?",
            )

            col_run, col_esc = st.columns([1, 3])
            with col_run:
                run_sim = st.button("Run", key="run_sim")
            with col_esc:
                st.caption("Try: HUMAN | BOOK | ENROLL | list of courses")

            if run_sim:
                if not test_msg.strip():
                    st.warning("Enter a test message first.")
                else:
                    with st.spinner("Running rule engine..."):
                        intent_result = match_intent(test_msg, sim_rules)
                        entity_result = detect_course_entity(test_msg, sim_rules)
                        base_reply = build_base_reply(
                            intent_result, entity_result, sim_rules, test_msg
                        )

                    st.markdown("---")

                    # Metrics row
                    m1, m2, m3, m4 = st.columns(4)
                    with m1:
                        st.metric("Intent", intent_result.get("intent") or "None")
                    with m2:
                        st.metric("Confidence", f"{intent_result.get('confidence', 0)}%")
                    with m3:
                        st.metric("Course", entity_result.get("course_id") or "None")
                    with m4:
                        category = intent_result.get("intent_category", "general")
                        st.metric("Category", category)

                    if intent_result.get("matched_keywords"):
                        st.info(f"Matched keywords: {', '.join(intent_result['matched_keywords'])}")

                    if entity_result.get("matched_keywords"):
                        st.info(f"Course keywords: {', '.join(entity_result['matched_keywords'])}")

                    # Check if this would trigger career counseling
                    if intent_result.get("intent") in CAREER_GUIDANCE_INTENTS:
                        domain_id = detect_career_domain(test_msg)
                        st.warning(f"This message would start a *career counseling flow*. Domain detected: {domain_id or 'general'}")
                        st.info("Switch to 'Career Counseling' mode to test the full flow.")

                    # Suggestions (when unresolved)
                    if not intent_result.get("resolved") and intent_result.get("intent") is None:
                        suggestions = get_suggestions(test_msg, sim_rules)
                        st.warning("Unresolved query — suggestions generated:")
                        for s in suggestions.get("suggested_intents", []):
                            st.write(f"  - {s['description']}")
                        for s in suggestions.get("suggested_courses", []):
                            st.write(f"  - Course: {s['name']}")

                    # Base reply
                    st.markdown("**Base Reply (grounded, no AI):**")
                    st.code(base_reply, language="text")

                    # Polish
                    with st.spinner("Polishing with Sarvam AI..."):
                        context = build_rules_context(sim_rules)
                        polished = polish_whatsapp_reply(base_reply, test_msg, context)

                    st.markdown("**Polished Reply (WhatsApp tone):**")
                    st.success(polished)

                    # Escalation preview
                    st.markdown("---")
                    st.caption("Escalation shortcuts:")
                    esc1, esc2, esc3 = st.columns(3)
                    with esc1:
                        st.code("HUMAN -> Talk to person", language="text")
                    with esc2:
                        st.code("BOOK -> Schedule call", language="text")
                    with esc3:
                        st.code("ENROLL -> Get form", language="text")

        else:
            # =============================================
            # Career Counseling Simulator (new)
            # =============================================
            st.subheader("Career Counseling Flow Simulator")
            st.caption(
                "Simulates the full intake flow: initial message -> 4 intake questions -> roadmap delivery."
            )

            # Initial career message
            career_msg = st.text_input(
                "Career question (triggers counseling):",
                key="career_msg",
                placeholder="e.g. I'm a 2nd year student, want AI career",
            )

            # Intake answers
            st.markdown("**Intake answers (simulated):**")
            intake_c1, intake_c2 = st.columns(2)
            with intake_c1:
                bg = st.selectbox("Background", ["Student", "Fresh graduate", "Working professional", "Career switcher"], key="sim_bg")
                skills = st.selectbox("Skills", ["Complete beginner", "Know basics", "Intermediate", "Advanced"], key="sim_skills")
            with intake_c2:
                goal = st.selectbox("Goal", ["Internship", "Full-time job", "Build projects", "Upskill", "Explore"], key="sim_goal")
                hours = st.selectbox("Hours/week", ["<5 hours", "5-10 hours", "10-20 hours", "20+ hours"], key="sim_hours")

            if st.button("Run Career Counseling Flow", key="run_career_sim"):
                if not career_msg.strip():
                    st.warning("Enter a career message first.")
                else:
                    st.markdown("---")

                    # Step 1: Detect intent + domain
                    with st.spinner("Step 1: Detecting career intent..."):
                        intent_result = match_intent(career_msg, sim_rules)
                        domain_id = detect_career_domain(career_msg)

                    m1, m2, m3 = st.columns(3)
                    with m1:
                        st.metric("Intent", intent_result.get("intent") or "None")
                    with m2:
                        st.metric("Category", intent_result.get("intent_category", "general"))
                    with m3:
                        st.metric("Domain", domain_id or "general")

                    # Step 2: Show intake start
                    with st.spinner("Step 2: Building intake start reply..."):
                        start_reply = build_career_start_reply(
                            career_msg, domain_id, sim_career_kb, sim_rules
                        )
                    st.markdown("**Bot asks first intake question:**")
                    st.code(start_reply, language="text")

                    # Step 3: Build intake data from form
                    intake_data = {
                        "background": bg,
                        "skills": skills,
                        "goal": goal,
                        "hours_per_week": hours,
                        "target_domain": domain_id or "",
                    }

                    st.markdown("**Simulated intake answers:**")
                    st.json(intake_data)

                    # Step 4: Generate roadmap
                    if not domain_id:
                        domain_id = "ai_data_science_beginner"
                        st.info(f"No specific domain detected, defaulting to: {domain_id}")

                    roadmap = get_roadmap_template(domain_id, sim_career_kb)
                    if roadmap:
                        with st.spinner("Step 3: Generating grounded roadmap..."):
                            base_roadmap = format_roadmap_for_whatsapp(
                                roadmap, intake_data, sim_rules
                            )

                        st.markdown("**Base Roadmap (grounded, template-based):**")
                        st.code(base_roadmap, language="text")

                        # Step 5: Tailor with LLM
                        with st.spinner("Step 4: Tailoring with Sarvam AI..."):
                            context = build_rules_context(sim_rules)
                            intake_summary = build_intake_summary(intake_data)
                            tailored = tailor_roadmap(
                                base_roadmap, intake_summary, career_msg, context
                            )

                        st.markdown("**Tailored Roadmap (personalized via LLM):**")
                        st.success(tailored)
                    else:
                        st.error(f"No roadmap template found for domain: {domain_id}")

            st.markdown("---")
            st.caption("Quick test messages to try:")
            st.code(
                "I'm a 1st year student, want AI career\n"
                "I want cybersecurity\n"
                "How to become a RAG developer?\n"
                "I want career in cloud devops\n"
                "I'm a working professional, want to switch to ML",
                language="text",
            )
