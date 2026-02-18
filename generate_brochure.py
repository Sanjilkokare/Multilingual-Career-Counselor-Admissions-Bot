"""
generate_brochure.py — Auto-generate a brochure.md from rules.json.

Run:
  python generate_brochure.py

Generates a shareable Markdown brochure with course summaries,
syllabus tables, fees, and contact info — all sourced from rules.json.
"""

import json


def generate():
    with open("rules.json", "r", encoding="utf-8") as f:
        rules = json.load(f)

    biz = rules.get("business_name", "Tech Academy")
    lines = []

    lines.append(f"# {biz} — Course Brochure\n")
    lines.append(f"**Mode:** {rules.get('mode', 'N/A')}")
    lines.append(f"**Support Hours:** {rules.get('support_hours', 'N/A')}")
    lines.append(f"**Address:** {rules.get('location', {}).get('address', 'N/A')}")
    lines.append(f"**Phone:** {rules.get('human_contact', {}).get('phone', 'N/A')}")
    lines.append(f"**Email:** {rules.get('human_contact', {}).get('email', 'N/A')}")
    lines.append("")

    # --- Quick comparison table ---
    lines.append("## Course Overview\n")
    lines.append("| Course | Duration | Level | Fees (INR) | Demo? |")
    lines.append("|--------|----------|-------|------------|-------|")
    for c in rules.get("courses", []):
        demo = "Yes" if c.get("demo_class_available") else "No"
        lines.append(
            f"| {c['name']} | {c['duration_weeks']} weeks | {c['level']} "
            f"| {c['fees']} | {demo} |"
        )
    lines.append("")

    # --- Detailed course sections ---
    for c in rules.get("courses", []):
        lines.append(f"---\n\n## {c['name']}\n")
        lines.append(f"**Duration:** {c['duration_weeks']} weeks | "
                      f"**Level:** {c['level']} | **Fees:** INR {c['fees']}\n")
        lines.append(f"**Prerequisites:** {c.get('prerequisites', 'None')}\n")

        # Outcomes
        outcomes = c.get("outcomes", [])
        if outcomes:
            lines.append("### What You'll Learn\n")
            for o in outcomes:
                lines.append(f"- {o}")
            lines.append("")

        # Syllabus table
        syllabus = c.get("syllabus_by_week", {})
        if syllabus:
            lines.append("### Syllabus\n")
            lines.append("| Week | Topics |")
            lines.append("|------|--------|")
            for week, topics in syllabus.items():
                lines.append(f"| {week} | {topics} |")
            lines.append("")

        # Projects
        projects = c.get("projects", [])
        if projects:
            lines.append("### Projects\n")
            for i, p in enumerate(projects, 1):
                lines.append(f"{i}. {p}")
            lines.append("")

        # Batch schedule
        batch = c.get("batch_schedule", {})
        if batch:
            lines.append("### Batch Schedule\n")
            if batch.get("weekday"):
                lines.append(f"- **Weekday:** {batch['weekday']}")
            if batch.get("weekend"):
                lines.append(f"- **Weekend:** {batch['weekend']}")
            lines.append("")

    # --- Footer ---
    lines.append("---\n")
    lines.append(f"### Ready to start?\n")
    booking = rules.get("booking_link", "")
    enroll = rules.get("enroll_link", "")
    phone = rules.get("human_contact", {}).get("phone", "")
    if booking:
        lines.append(f"- **Book a free counseling call:** {booking}")
    if enroll:
        lines.append(f"- **Enroll now:** {enroll}")
    if phone:
        lines.append(f"- **Call us:** {phone}")
    lines.append(f"\n*Generated from rules.json — {biz}*\n")

    brochure = "\n".join(lines)

    with open("brochure.md", "w", encoding="utf-8") as f:
        f.write(brochure)

    print(f"Brochure generated: brochure.md ({len(brochure)} chars)")


if __name__ == "__main__":
    generate()
