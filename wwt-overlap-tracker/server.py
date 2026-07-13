"""
BSA Overlap Tracker — Flask Web Server
========================================
This is the brain of the dashboard. It:
  1. Pulls live project data from Smartsheet via api_integration.py
  2. Runs it through overlap_detector.py to score and flag projects
  3. Merges in manually added projects from manual_projects.json
  4. Serves everything to the dashboard via API endpoints
  5. Powers the Groq AI Update Assistant
 
HOW TO RUN:
  python server.py
  Then open http://localhost:5000 in your browser.
 
HOW TO DEPLOY (Day 4):
  Push to GitHub → connect to Render → add environment variables in Render dashboard.
 
ENDPOINTS:
  GET  /                      → serves dashboard.html
  GET  /api/projects          → all projects (Smartsheet + manual), overlap scores, summary
  GET  /api/manual            → manual projects only
  POST /api/manual            → add a new manual project
  PUT  /api/manual/<ref_id>   → edit an existing manual project
  DELETE /api/manual/<ref_id> → delete a manual project
  POST /api/analyze           → Groq AI suggests an update for a project
 
CREDENTIALS:
  Day 2: paste your Groq API key below (GROQ_API_KEY)
  Day 4: move all keys to Render environment variables and GitHub Secrets
"""
 
import os
import json
import uuid
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from groq import Groq
 
# ─── CREDENTIALS ─────────────────────────────────────────────────────────────
# Groq key: get it from https://console.groq.com → API Keys
# Day 2: paste your key below
# Day 4: delete the hardcoded key and rely on environment variable only
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
 
# Smartsheet credentials (already set in api_integration.py — no need to repeat here)
# ─────────────────────────────────────────────────────────────────────────────
 
# ─── CONFIG ──────────────────────────────────────────────────────────────────
MANUAL_PROJECTS_FILE = "manual_projects.json"
DASHBOARD_FILE = "dashboard.html"  # will be created on Day 3
# ─────────────────────────────────────────────────────────────────────────────
 
app = Flask(__name__)
groq_client = Groq(api_key=GROQ_API_KEY)
 
 
# ─── MANUAL PROJECTS HELPERS ─────────────────────────────────────────────────
 
def load_manual_projects():
    """Load manually added projects from JSON file."""
    if not os.path.exists(MANUAL_PROJECTS_FILE):
        return []
    with open(MANUAL_PROJECTS_FILE, "r") as f:
        return json.load(f)
 
 
def save_manual_projects(projects):
    """Save manual projects to JSON file."""
    with open(MANUAL_PROJECTS_FILE, "w") as f:
        json.dump(projects, f, indent=2)
 
 
def next_manual_id(projects):
    """Generate a unique M-badge ID for a new manual project."""
    existing_ids = {p["ref_id"] for p in projects if p["ref_id"].startswith("M-")}
    counter = 1
    while f"M-{counter:03d}" in existing_ids:
        counter += 1
    return f"M-{counter:03d}"
 
 
# ─── DATA PIPELINE ───────────────────────────────────────────────────────────
 
def build_full_dataset():
    """
    Pull Smartsheet data, run overlap detection, merge manual projects.
    Returns the full output dict that the dashboard needs.
    """
    from api_integration import load_data_from_smartsheet
    from overlap_detector import get_active, detect_overlaps, build_output
 
    # 1. Pull live Smartsheet data
    df = load_data_from_smartsheet()
    active = get_active(df)
 
    # 2. Run overlap detection
    bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags = detect_overlaps(active)
    output = build_output(active, bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags)
 
    # 3. Merge manual projects
    manual = load_manual_projects()
    if manual:
        # Run overlap detection on manual projects too (simple version)
        for mp in manual:
            # Check if manual project's BSA is overloaded
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
 
    # Mark Smartsheet projects with source
    for p in output["projects"]:
        if "source" not in p:
            p["source"] = "smartsheet"
 
    output["last_refreshed"] = datetime.now().strftime("%B %d, %Y at %I:%M %p CST")
    return output
 
 
# ─── ROUTES ──────────────────────────────────────────────────────────────────
 
@app.route("/")
def index():
    """Serve the dashboard HTML file."""
    return send_from_directory(".", DASHBOARD_FILE)
 
 
@app.route("/api/projects", methods=["GET"])
def get_projects():
    """
    Main data endpoint. Pulls live Smartsheet data, runs overlap detection,
    merges manual projects, and returns everything as JSON.
    """
    try:
        data = build_full_dataset()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
 
 
@app.route("/api/manual", methods=["GET"])
def get_manual_projects():
    """Return all manually added projects."""
    return jsonify(load_manual_projects())
 
 
@app.route("/api/manual", methods=["POST"])
def add_manual_project():
    """
    Add a new manual project.
    Expected JSON body (7 fields confirmed by Ellen):
    {
        "project_name": "...",
        "bsa_owner": "...",
        "system": "...",
        "status": "...",
        "primary_stakeholder": "...",
        "business_priority": "...",
        "notes": "..."
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
 
    projects = load_manual_projects()
    new_project = {
        "ref_id": next_manual_id(projects),
        "title": data.get("project_name", "Untitled"),
        "bsa_owner": data.get("bsa_owner", "Unassigned"),
        "system": data.get("system", "Unknown"),
        "status": data.get("status", "New"),
        "requestor": data.get("primary_stakeholder", "Unknown"),
        "priority": data.get("business_priority", "Unknown"),
        "notes": data.get("notes", ""),
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
    return jsonify({"success": True, "project": new_project}), 201
 
 
@app.route("/api/manual/<ref_id>", methods=["PUT"])
def edit_manual_project(ref_id):
    """
    Edit an existing manual project (triggered by the Edit button on the dashboard).
    Overwrites all fields with the new values from the form.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
 
    projects = load_manual_projects()
    for i, p in enumerate(projects):
        if p["ref_id"] == ref_id:
            projects[i].update({
                "title": data.get("project_name", p["title"]),
                "bsa_owner": data.get("bsa_owner", p["bsa_owner"]),
                "system": data.get("system", p["system"]),
                "status": data.get("status", p["status"]),
                "requestor": data.get("primary_stakeholder", p["requestor"]),
                "priority": data.get("business_priority", p["priority"]),
                "notes": data.get("notes", p.get("notes", "")),
                "updated_at": datetime.now().isoformat(),
            })
            save_manual_projects(projects)
            return jsonify({"success": True, "project": projects[i]})
 
    return jsonify({"error": f"Project {ref_id} not found"}), 404
 
 
@app.route("/api/manual/<ref_id>", methods=["DELETE"])
def delete_manual_project(ref_id):
    """Delete a manual project by its M-badge ref_id."""
    projects = load_manual_projects()
    original_count = len(projects)
    projects = [p for p in projects if p["ref_id"] != ref_id]
 
    if len(projects) == original_count:
        return jsonify({"error": f"Project {ref_id} not found"}), 404
 
    save_manual_projects(projects)
    return jsonify({"success": True})
 
 
@app.route("/api/analyze", methods=["POST"])
def analyze_project():
    """
    Groq AI Update Assistant.
    Takes a project (Smartsheet or manual) and a question/context from the BSA,
    then returns an AI-suggested update. The BSA always decides whether to accept it.
 
    Expected JSON body:
    {
        "project": { ...project fields... },
        "question": "What should I update the status to given this situation: ..."
    }
 
    NOTE: This NEVER writes to Smartsheet directly. It only suggests.
          The BSA copies the suggestion and updates Smartsheet manually.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
 
    project = data.get("project", {})
    question = data.get("question", "What update do you suggest for this project?")
 
    # Build context for the AI
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
 
    prompt = f"""You are a BSA (Business Systems Analyst) assistant at World Wide Technology (WWT).
You help BSAs manage their project workload on the Strategic Initiatives & Operations intake tracker.
 
Here is the project you are reviewing:
{project_context}
 
BSA's question or context:
{question}
 
Provide a concise, practical suggestion for how to update or handle this project.
Focus on status updates, priority adjustments, or coordination recommendations.
Keep your response under 150 words. Be direct and actionable.
Remember: you are only suggesting — the BSA makes the final decision."""
 
    try:
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
        )
        suggestion = response.choices[0].message.content.strip()
        return jsonify({
            "success": True,
            "suggestion": suggestion,
            "disclaimer": "AI suggestion only — BSA makes the final decision. Do not use this to directly update Smartsheet."
        })
    except Exception as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500
 
 
# ─── MAIN ────────────────────────────────────────────────────────────────────
 
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    print(f"Starting BSA Overlap Tracker server on http://localhost:{port}")
    print(f"Dashboard: http://localhost:{port}/")
    print(f"API:       http://localhost:{port}/api/projects")
    app.run(host="0.0.0.0", port=port, debug=debug)
 