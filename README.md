# Sarvam Tech Academy — Career Counselor & Admissions Bot

A demo-ready WhatsApp auto-reply system that acts as a **free friendly career counselor** + admissions assistant for a tech coaching institute, powered by [Sarvam AI](https://www.sarvam.ai/).

**Key principles:**
- **Grounded facts:** All fees, syllabus, timings come from `rules.json` only. The LLM rephrases tone, never invents facts.
- **Curated roadmaps:** Career guidance uses templates from `career_kb.json`. LLM personalizes tone, not content.
- **Honest:** If we don't teach a domain (e.g. cybersecurity), the bot says so honestly while still providing a roadmap.

## Project Structure

```
SARVAMAI/
├── app.py              # Streamlit UI (4 tabs: Chat, Admin, Career Templates, Simulator)
├── server.py           # FastAPI webhook server for WhatsApp Cloud API
├── rules_engine.py     # Intent matching + course detection + counseling state machine
├── sarvam_client.py    # Sarvam AI client + polish + tailor_roadmap with guardrails
├── rules.json          # Knowledge base: 5 courses, 26 intents, synonyms, config
├── career_kb.json      # Curated career roadmap templates for 11 domains
├── leads.json          # Auto-captured leads (created at runtime)
├── sessions.json       # Multi-turn session state (created at runtime)
├── generate_brochure.py# Auto-generate brochure.md from rules.json
├── test.py             # Simple API test script
├── .env                # Your API keys (never commit this)
├── .gitignore          # Ignores .env, leads.json, sessions.json, __pycache__
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

## What the Bot Does

### 1. Admissions FAQ (grounded)
Answers questions about fees, syllabus, batch timings, demo class, enrollment, prerequisites, certificates, placement, refund, location — all from `rules.json`.

### 2. Career Counseling (curated + personalized)
When a user says something like "I want AI career" or "How to become a RAG developer?", the bot:
1. Detects the career domain (AI/DS, ML, DL, LLM/RAG, Agentic AI, Voicebot, Prompt Eng, Cybersecurity, Cloud/DevOps)
2. Asks 4 short intake questions (background, skills, goal, time commitment)
3. Generates a structured roadmap from curated templates in `career_kb.json`
4. Personalizes the roadmap using Sarvam AI LLM (tone only, not facts)
5. Honestly recommends our courses where relevant (or says "we don't teach this" for external domains)

### 3. Course Catalog
"What courses do you offer?" returns a clean formatted list with fees and durations.

## Courses Offered

| Course | Duration | Level | Fees (INR) |
|--------|----------|-------|------------|
| Python + DSA | 10 weeks | Beginner | 8,000 |
| Machine Learning Bootcamp | 12 weeks | Intermediate | 12,000 |
| Deep Learning (CV/NLP) | 14 weeks | Intermediate | 15,000 |
| Java + Backend | 12 weeks | Beginner | 10,000 |
| GenAI & AI Agents | 8 weeks | Intermediate | 12,000 |

## Career Roadmap Domains (11 templates)

| Domain | We Teach? | Template |
|--------|-----------|----------|
| AI & Data Science (Beginner) | Yes | 4-stage path, beginner to job-ready |
| Machine Learning Engineer | Yes | Production ML, MLOps, deployment |
| Deep Learning Specialist | Yes | CV + NLP track with papers |
| LLM / RAG Engineer | Yes | RAG pipelines, production LLM apps |
| Agentic AI Engineer | Yes | Multi-agent systems, tool use |
| Voicebot Developer | Yes | STT/TTS, telephony, Indic languages |
| Prompt Engineering | Partial | Techniques, evaluation, applied PE |
| Cybersecurity | No | Honest + provides general roadmap |
| Cloud / DevOps | No | Honest + provides general roadmap |
| Web & App Development | No | Honest + full-stack roadmap |
| Blockchain & Web3 | No | Honest + smart contract roadmap |

## How It Works

```
WhatsApp User
    │
    ▼
Meta Cloud API ──POST──▶ server.py /whatsapp/webhook
                              │
                              ├─ 0. Dedup check (Meta retries)
                              ├─ 1. Non-text ack ("Please type your question")
                              ├─ 2. Hot-reload rules.json + career_kb.json
                              ├─ 3. Check session:
                              │     ├─ counseling_intake → ask next Q or deliver roadmap
                              │     └─ pending_course → resolve "which course?" reply
                              ├─ 4. match_intent() → 3 categories:
                              │     ├─ career_guidance → start counseling flow
                              │     ├─ admissions → rule engine + template
                              │     └─ general → greeting, thanks, HUMAN, BOOK
                              ├─ 5. is_after_hours() → prepend offline notice
                              ├─ 6. polish / tailor via Sarvam LLM (tone only)
                              │
                              ▼
                        WhatsApp Cloud API (send reply back)
```

## v4 Features (Career Counselor Upgrade)

### Career Counseling Flow
- 3-category intent routing: admissions, career_guidance, general
- Domain detection: maps user messages to 11 career domains
- Multi-turn intake: 4 questions (background, skills, goal, hours/week)
- Template-first roadmaps: curated 4-stage paths from `career_kb.json`
- LLM tailoring: personalizes tone without inventing facts
- Honest recommendations: "We don't teach cybersecurity" + still provides roadmap

### Message Deduplication
Meta sometimes retries webhook delivery. The server deduplicates by message ID within a 60-second window.

### Non-Text Acknowledgement
Voice notes, images, and other non-text messages get a friendly "Please type your question" reply instead of being silently ignored.

### Webhook Signature Verification
Set `WHATSAPP_APP_SECRET` to enable `X-Hub-Signature-256` verification for production security.

### Privacy & Logging
- `LOG_LEVEL` env var controls verbosity (set to WARNING in production)
- Phone numbers are masked in all logs (last 4 digits only)
- User messages are truncated to 200 chars in lead storage

### Hot-Reload
Both `rules.json` and `career_kb.json` are reloaded on each request if file mtime changes. Edit via Streamlit admin or directly — no server restart needed.

### Existing v3 Features (preserved)
- Multi-turn "which course?" sessions
- Word-boundary keyword matching (`\b`)
- After-hours timezone behavior
- Admin auth on `/leads` and `/whatsapp/send`
- Synonym expansion
- Lead capture for unresolved queries

## Guardrails

- **Business facts:** Fees, durations, batch timings only from `rules.json`. LLM cannot invent them.
- **Career guidance:** Uses curated templates only. LLM personalizes tone, not content.
- **Honesty:** Bot says "We don't currently offer that course" for domains we don't teach.
- **No promises:** Career roadmaps include disclaimer: "Outcomes depend on your effort and opportunities."
- **Safety:** No medical/legal/salary advice. No job guarantees.
- **Fallback:** If LLM fails, unpolished rule-based reply is sent.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual keys
```

Required:
- `SARVAM_API_KEY` — from [Sarvam AI](https://www.sarvam.ai/)
- `WHATSAPP_TOKEN` — from [Meta Developer Portal](https://developers.facebook.com/) (optional for demo mode)
- `WHATSAPP_PHONE_NUMBER_ID` — from WhatsApp API Setup
- `WHATSAPP_VERIFY_TOKEN` — any secret string you choose
- `ADMIN_TOKEN` — any secret string for admin endpoint auth

Optional:
- `BUSINESS_TIMEZONE` — defaults to `Asia/Kolkata`
- `WHATSAPP_API_VERSION` — defaults to `v20.0`
- `LOG_LEVEL` — defaults to `INFO` (use `WARNING` in production)
- `WHATSAPP_APP_SECRET` — Meta App Secret for webhook signature verification

## Running

### Streamlit UI

```bash
streamlit run app.py
```

Opens at `http://localhost:8501` with 4 tabs:
- **Chatbot** — Sarvam AI multilingual chatbot
- **Admin Panel** — Status, Course CMS, FAQ rules, leads + analytics
- **Career Templates** — View/edit career_kb.json roadmap templates
- **Simulator** — Test admissions FAQ and career counseling flows

### FastAPI Server

```bash
uvicorn server:app --reload --port 8000
```

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Health check + courses/rules/roadmaps count |
| GET | `/whatsapp/webhook` | None | Meta webhook verification |
| POST | `/whatsapp/webhook` | None | Receive inbound WhatsApp messages |
| POST | `/whatsapp/send` | X-Admin-Token | Manually send a WhatsApp message |
| GET | `/leads` | X-Admin-Token | View captured leads (newest first) |

## Manual Test Script (12 Messages)

**Helper function** (paste into your shell first):

```bash
send() {
  curl -s -X POST http://localhost:8000/whatsapp/webhook \
    -H "Content-Type: application/json" \
    -d "{\"entry\":[{\"changes\":[{\"value\":{\"messages\":[{\"id\":\"msg_$RANDOM\",\"from\":\"$1\",\"type\":\"text\",\"text\":{\"body\":\"$2\"}}]}}]}]}" | python -m json.tool
}
```

### Test 1: Greeting
```bash
send 919876543210 "hello"
```
Expected: Greeting with course list. No false-positive.

### Test 2: Course list
```bash
send 919876543210 "Give me list of courses you provide"
```
Expected: Returns all 5 courses with fees and durations (course_list intent).

### Test 3: Fee inquiry with course
```bash
send 919876543210 "Python course ka fee kitna hai?"
```
Expected: course=python_dsa, intent=fees, INR 8,000.

### Test 4: Syllabus without course (multi-turn)
```bash
send 919876543210 "syllabus batao"
```
Expected: Asks "Which course?" with numbered list. Session created.

### Test 5: Reply with number
```bash
send 919876543210 "2"
```
Expected: ML Bootcamp syllabus. `session_resolved: true`.

### Test 6: Career interest — starts counseling
```bash
send 919876543210 "I'm a 1st year student, want AI career"
```
Expected: Detects career_guidance intent, starts intake flow with first question. `counseling_started: true`.

### Test 7: Answer intake Q1 (background)
```bash
send 919876543210 "1"
```
Expected: Records "Student", asks next question (skills).

### Test 8: Answer intake Q2 (skills)
```bash
send 919876543210 "2"
```
Expected: Records "Know basics", asks next question (goal).

### Test 9: Answer intake Q3 (goal)
```bash
send 919876543210 "2"
```
Expected: Records "Full-time job", asks next question (hours/week).

### Test 10: Answer intake Q4 (hours) — delivers roadmap
```bash
send 919876543210 "3"
```
Expected: Records "10-20 hours", delivers AI & Data Science roadmap with stages, projects, course recommendations. `counseling: complete`.

### Test 11: Cybersecurity (honest + roadmap)
```bash
send 919876543210 "I want cybersecurity"
```
Expected: Starts counseling flow, detects cybersecurity domain. After intake, delivers cybersecurity roadmap with honest note "We don't currently offer a dedicated cybersecurity course" + suggests Python+DSA as foundation.

### Test 12: Non-text message
```bash
curl -s -X POST http://localhost:8000/whatsapp/webhook \
  -H "Content-Type: application/json" \
  -d '{"entry":[{"changes":[{"value":{"messages":[{"id":"msg_voice","from":"919876543210","type":"audio","audio":{"id":"123"}}]}}]}]}' | python -m json.tool
```
Expected: Returns `non-text-ack` with "I can only read text messages right now."

### Admin auth test
```bash
# Without token (should fail with 401):
curl -s http://localhost:8000/leads | python -m json.tool

# With token:
curl -s http://localhost:8000/leads -H "X-Admin-Token: YOUR_TOKEN" | python -m json.tool
```

## Demo Mode

If `WHATSAPP_TOKEN` is not set, the server runs in **demo mode** — processes everything normally but logs outbound messages instead of calling Meta's API.

## Architecture Decisions

- **No hallucination:** LLM sees only base reply/roadmap + business context. It rephrases, never invents.
- **Template-first roadmaps:** Career guidance uses curated templates. LLM only personalizes tone.
- **3-category routing:** Admissions intents, career guidance intents, and general intents are routed separately.
- **Multi-turn state machine:** Sessions track both "which course?" and counseling intake flows.
- **Honest course recommendations:** For domains we don't teach, bot says so and suggests foundational courses.
- **Fallback to base:** If LLM times out or fails, the unpolished rule-based reply is sent.
- **Word-boundary matching:** All keyword checks use `\bword\b` regex to prevent false positives.
- **Hot-reload:** Both rules.json and career_kb.json reload on file change.
- **Message dedup:** Prevents duplicate processing from Meta retries.
- **Webhook signature:** Optional X-Hub-Signature-256 verification for production.
- **Privacy:** Phone masking in logs, message truncation in leads, configurable log level.
