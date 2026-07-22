import os
import json
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
from flask import Flask, jsonify, request, send_from_directory
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

MANUAL_PROJECTS_FILE = "manual_projects.json"
LINKS_FILE = "project_links.json"
DASHBOARD_FILE = "dashboard.html"
REFRESH_HOURS = {8, 12, 17}

app = Flask(__name__)
groq_client = Groq(api_key=GROQ_API_KEY)

_cache = {"data": None}
_cache_lock = threading.Lock()


def load_manual_projects():
    if not os.path.exists(MANUAL_PROJECTS_FILE):
        return []
    with open(MANUAL_PROJECTS_FILE, "r") as f:
        return json.load(f)


def save_manual_projects(projects):
    with open(MANUAL_PROJECTS_FILE, "w") as f:
        json.dump(projects, f, indent=2)


def next_manual_id(projects):
    existing_ids = {p["ref_id"] for p in projects if p["ref_id"].startswith("M-")}
    counter = 1
    while f"M-{counter:03d}" in existing_ids:
        counter += 1
    return f"M-{counter:03d}"


def load_links():
    if not os.path.exists(LINKS_FILE):
        return {}
    with open(LINKS_FILE, "r") as f:
        return json.load(f)


def save_links(links):
    with open(LINKS_FILE, "w") as f:
        json.dump(links, f, indent=2)


def build_full_dataset():
    from api_integration import load_data_from_smartsheet
    from overlap_detector import get_active, detect_overlaps, build_output

    df = load_data_from_smartsheet()
    active = get_active(df)

    bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags = detect_overlaps(active)
    output = build_output(active, bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags)

    manual = load_manual_projects()
    if manual:
        for mp in manual:
            bsa = mp.get("bsa_owner", "")
            flags = []
            score = 0

            if bsa in bsa_workload and len(bsa_workload[bsa]) >= 10:
                flags.append(f"BSA overloaded: {bsa} has {len(bsa_workload[bsa])} active items")
                score += 1

            system = mp.get("system", "")
            if system in system_overlap and len(system_overlap[system]) >= 5:
                flags.append(f"Hot system: {len(system_overlap[system])} active projects touch {system}")
                score += 1

            mp["overlap_score"] = score
            mp["flags"] = flags
            mp["risk_level"] = "High Touch" if score >= 3 else "Medium" if score >= 1 else "Low Touch"
            mp["source"] = "manual"

        output["projects"] = output["projects"] + manual
        output["summary"]["total_active"] += len(manual)
        output["summary"]["high_touch_count"] = sum(1 for p in output["projects"] if p["risk_level"] == "High Touch")
        output["summary"]["medium_count"] = sum(1 for p in output["projects"] if p["risk_level"] == "Medium")
        output["summary"]["low_touch_count"] = sum(1 for p in output["projects"] if p["risk_level"] == "Low Touch")

    for p in output["projects"]:
        if "source" not in p:
            p["source"] = "smartsheet"

    output["last_refreshed"] = datetime.now(CENTRAL).strftime("%B %d, %Y at %I:%M %p %Z")
    return output


def refresh_cache():
    global _cache
    print(f"[{datetime.now().strftime('%I:%M %p')}] Refreshing data from Smartsheet...")
    try:
        data = build_full_dataset()
        with _cache_lock:
            _cache["data"] = data
        print(f"[{datetime.now().strftime('%I:%M %p')}] Refresh complete. {data['summary']['total_active']} active projects.")
        return data
    except Exception as e:
        print(f"[{datetime.now().strftime('%I:%M %p')}] Refresh failed: {e}. Serving last known data.")
        return None


def start_scheduler():
    fired_today = set()

    def scheduler_loop():
        nonlocal fired_today
        while True:
            now = datetime.now()
            today = now.date()
            fired_today = {h for h in fired_today if h[0] == today}
            key = (today, now.hour)
            if now.hour in REFRESH_HOURS and key not in fired_today:
                fired_today.add(key)
                refresh_cache()
            time.sleep(30)

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.name = "BSA-AutoRefresh"
    t.start()
    print("Auto-refresh scheduler started. Will refresh at 8am, 12pm, and 5pm daily.")


@app.route("/")
def index():
    return send_from_directory(".", DASHBOARD_FILE)


@app.route("/api/projects", methods=["GET"])
def get_projects():
    with _cache_lock:
        cached = _cache["data"]
    if cached is not None:
        return jsonify(cached)
    data = refresh_cache()
    if data:
        return jsonify(data)
    return jsonify({"error": "Data not available yet. Try again in a moment."}), 503


@app.route("/api/refresh", methods=["GET"])
def manual_refresh():
    data = refresh_cache()
    if data:
        return jsonify({
            "success": True,
            "last_refreshed": data.get("last_refreshed"),
            "total_active": data["summary"]["total_active"]
        })
    return jsonify({"error": "Refresh failed. Check server logs."}), 500


@app.route("/api/manual", methods=["GET"])
def get_manual_projects():
    return jsonify(load_manual_projects())


@app.route("/api/manual", methods=["POST"])
def add_manual_project():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    projects = load_manual_projects()
    new_project = {
        "ref_id": next_manual_id(projects),
        "title": data.get("project_name", "Untitled"),
        "bsa_owner": data.get("bsa_owner", "Unassigned"),
        "systems": data.get("systems", []),
        "system": data.get("system", "Unknown"),
        "status": data.get("status", "New"),
        "requestor": data.get("primary_stakeholder", "Unknown"),
        "priority": data.get("business_priority", "Unknown"),
        "notes": data.get("notes", ""),
        "co_owner": data.get("co_owner", ""),
        "start_date": data.get("start_date", ""),
        "end_date": data.get("end_date", ""),
        "known_overlaps": data.get("known_overlaps", ""),
        "request_type": "Manual Entry",
        "program": "Unknown",
        "overlap_score": 0,
        "flags": [],
        "risk_level": "Low Touch",
        "source": "manual",
        "added_at": datetime.now().isoformat(),
    }

    projects.append(new_project)
    save_manual_projects(projects)
    with _cache_lock:
        _cache["data"] = None
    return jsonify({"success": True, "project": new_project}), 201


@app.route("/api/manual/<ref_id>", methods=["PUT"])
def edit_manual_project(ref_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    projects = load_manual_projects()
    for i, p in enumerate(projects):
        if p["ref_id"] == ref_id:
            projects[i].update({
                "title": data.get("project_name", p["title"]),
                "bsa_owner": data.get("bsa_owner", p["bsa_owner"]),
                "systems": data.get("systems", p.get("systems", [])),
                "system": data.get("system", p["system"]),
                "status": data.get("status", p["status"]),
                "requestor": data.get("primary_stakeholder", p["requestor"]),
                "priority": data.get("business_priority", p["priority"]),
                "notes": data.get("notes", p.get("notes", "")),
                "co_owner": data.get("co_owner", p.get("co_owner", "")),
                "start_date": data.get("start_date", p.get("start_date", "")),
                "end_date": data.get("end_date", p.get("end_date", "")),
                "known_overlaps": data.get("known_overlaps", p.get("known_overlaps", "")),
                "updated_at": datetime.now().isoformat(),
            })
            save_manual_projects(projects)
            with _cache_lock:
                _cache["data"] = None
            return jsonify({"success": True, "project": projects[i]})

    return jsonify({"error": f"Project {ref_id} not found"}), 404


@app.route("/api/manual/<ref_id>", methods=["DELETE"])
def delete_manual_project(ref_id):
    projects = load_manual_projects()
    original_count = len(projects)
    projects = [p for p in projects if p["ref_id"] != ref_id]
    if len(projects) == original_count:
        return jsonify({"error": f"Project {ref_id} not found"}), 404
    save_manual_projects(projects)
    with _cache_lock:
        _cache["data"] = None
    return jsonify({"success": True})


@app.route("/api/links", methods=["GET"])
def get_links():
    return jsonify(load_links())


@app.route("/api/links", methods=["POST"])
def add_link():
    data = request.get_json()
    if not data or not data.get("enhancement_ref_id") or not data.get("large_project_ref_id"):
        return jsonify({"error": "enhancement_ref_id and large_project_ref_id required"}), 400
    links = load_links()
    links[data["enhancement_ref_id"]] = data["large_project_ref_id"]
    save_links(links)
    return jsonify({"success": True})


@app.route("/api/links/<enhancement_ref_id>", methods=["DELETE"])
def remove_link(enhancement_ref_id):
    links = load_links()
    if enhancement_ref_id not in links:
        return jsonify({"error": "Link not found"}), 404
    del links[enhancement_ref_id]
    save_links(links)
    return jsonify({"success": True})


@app.route("/api/team-overview", methods=["POST"])
def team_overview():
    data = request.get_json()
    projects = data.get("projects", [])
    high_touch = [p for p in projects if p.get("risk_level") == "High Touch"]

    if not high_touch:
        return jsonify({"success": True, "summary": "No High Touch projects found. The team looks well-balanced right now."})

    project_lines = "\n".join([
        f"- {p['title']} | BSA: {p['bsa_owner']} | System: {p.get('system','?')} | Status: {p.get('status','?')} | Flags: {', '.join(p.get('flags', [])) or 'None'}"
        for p in high_touch[:20]
    ])

    prompt = f"""You are a BSA team coordinator at World Wide Technology (WWT).
You are reviewing the team's highest-risk active projects to prepare for a Monday team meeting.

Here are the {len(high_touch)} High Touch projects:

{project_lines}

Write a concise team overview (under 200 words) that:
1. Identifies the BSAs with the heaviest workload
2. Highlights any systems where multiple BSAs are working simultaneously
3. Flags 2-3 specific coordination risks the team should discuss
4. Ends with one concrete recommendation for the team

Be direct and practical. This is for an internal team meeting."""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
            temperature=0.3,
        )
        return jsonify({
            "success": True,
            "summary": response.choices[0].message.content.strip(),
            "high_touch_count": len(high_touch),
            "disclaimer": "AI summary only - review with your team before acting on any recommendations."
        })
    except Exception as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500


@app.route("/api/draft-update", methods=["POST"])
def draft_update():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    project = data.get("project", {})
    context = data.get("context", "")

    project_context = f"""
Project: {project.get('title', 'Unknown')}
BSA Owner: {project.get('bsa_owner', 'Unknown')}
Status: {project.get('status', 'Unknown')}
System: {project.get('system', 'Unknown')}
Priority: {project.get('priority', 'Unknown')}
Stakeholder: {project.get('requestor', 'Unknown')}
Notes: {project.get('notes', 'None')}
Overlap Flags: {', '.join(project.get('flags', [])) or 'None'}
""".strip()

    prompt = f"""You are a BSA at World Wide Technology (WWT) drafting a status update for a stakeholder.

Project details:
{project_context}

Additional context from the BSA:
{context or 'None provided'}

Write a professional, concise status update email or Teams message (under 120 words) that:
1. States the current status clearly
2. Mentions next steps or timeline
3. Flags any blockers or dependencies if relevant
4. Is written in first person from the BSA's perspective

Keep it friendly but professional. No jargon. This will be sent to the project stakeholder."""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.4,
        )
        return jsonify({
            "success": True,
            "draft": response.choices[0].message.content.strip(),
            "disclaimer": "Review and edit before sending. AI draft only - you know your stakeholder best."
        })
    except Exception as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500


@app.route("/api/analyze", methods=["POST"])
def analyze_project():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    project = data.get("project", {})
    question = data.get("question", "What update do you suggest for this project?")

    project_context = f"""
Project: {project.get('title', 'Unknown')}
Ref ID: {project.get('ref_id', 'Unknown')}
BSA Owner: {project.get('bsa_owner', 'Unknown')}
Status: {project.get('status', 'Unknown')}
System: {project.get('system', 'Unknown')}
Priority: {project.get('priority', 'Unknown')}
Risk Level: {project.get('risk_level', 'Unknown')}
Overlap Flags: {', '.join(project.get('flags', [])) or 'None'}
Notes: {project.get('notes', 'None')}
""".strip()

    prompt = f"""You are a BSA assistant at World Wide Technology (WWT).

Project:
{project_context}

BSA's notes:
{question}

Provide a concise, practical suggestion for how to update or handle this project.
Keep your response under 150 words. Be direct and actionable.
Remember: you are only suggesting - the BSA makes the final decision."""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
        )
        return jsonify({
            "success": True,
            "suggestion": response.choices[0].message.content.strip(),
            "disclaimer": "AI suggestion only - BSA makes the final decision. Do not use this to directly update Smartsheet."
        })
    except Exception as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    start_scheduler()
    print(f"Starting BSA Overlap Tracker on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)