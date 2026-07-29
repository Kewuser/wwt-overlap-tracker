import os
import json
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
from flask import Flask, jsonify, request, send_from_directory

MANUAL_PROJECTS_FILE = "manual_projects.json"
LINKS_FILE = "project_links.json"
DASHBOARD_FILE = "dashboard.html"
BSA_TEAM_FILE = "bsa_team.json"
SETTINGS_FILE = "settings.json"
REFRESH_HOURS = {8, 12, 17}

DEFAULT_BSA_TEAM = [
    "Angela Rhodes", "Dan Temperly", "Dustin Hartrick", "Ellen Clegg",
    "Jennifer Bednar", "Jennifer Cummings", "Jordan Butler", "Tori Yardley", "Zack Godat"
]

app = Flask(__name__)

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


def load_bsa_team():
    if not os.path.exists(BSA_TEAM_FILE):
        return DEFAULT_BSA_TEAM.copy()
    with open(BSA_TEAM_FILE, "r") as f:
        return json.load(f)


def save_bsa_team(team):
    with open(BSA_TEAM_FILE, "w") as f:
        json.dump(sorted(team), f, indent=2)


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {"intake_form_url": ""}
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)


def save_settings_data(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def build_full_dataset():
    from api_integration import load_data_from_smartsheet
    import overlap_detector
    from overlap_detector import get_active, detect_overlaps, build_output
    overlap_detector.BSA_TEAM = set(load_bsa_team())

    df = load_data_from_smartsheet()
    active = get_active(df)

    bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags = detect_overlaps(active)
    output = build_output(active, bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags)

    manual = load_manual_projects()
    if manual:
        for mp in manual:
            mp["overlap_score"] = 0
            mp["flags"] = []
            mp["risk_level"] = "Low Touch"
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
    print(f"[{datetime.now(CENTRAL).strftime('%I:%M %p')}] Refreshing data from Smartsheet...")
    try:
        data = build_full_dataset()
        with _cache_lock:
            _cache["data"] = data
        print(f"[{datetime.now(CENTRAL).strftime('%I:%M %p')}] Refresh complete. {data['summary']['total_active']} active projects.")
        return data
    except Exception as e:
        print(f"[{datetime.now(CENTRAL).strftime('%I:%M %p')}] Refresh failed: {e}. Serving last known data.")
        return None


def start_scheduler():
    fired_today = set()

    def scheduler_loop():
        nonlocal fired_today
        while True:
            now = datetime.now(CENTRAL)
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
        "added_at": datetime.now(CENTRAL).isoformat(),
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
                "updated_at": datetime.now(CENTRAL).isoformat(),
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


@app.route("/api/bsa-team", methods=["GET"])
def get_bsa_team():
    return jsonify(load_bsa_team())


@app.route("/api/bsa-team", methods=["POST"])
def add_bsa_member():
    data = request.get_json()
    name = data.get("name", "").strip() if data else ""
    if not name:
        return jsonify({"error": "Name required"}), 400
    team = load_bsa_team()
    if name not in team:
        team.append(name)
        save_bsa_team(team)
    return jsonify({"success": True, "team": sorted(team)})


@app.route("/api/bsa-team/<name>", methods=["DELETE"])
def remove_bsa_member(name):
    team = load_bsa_team()
    if name not in team:
        return jsonify({"error": "Not found"}), 404
    team = [m for m in team if m != name]
    save_bsa_team(team)
    return jsonify({"success": True, "team": team})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    settings = load_settings()
    settings.update(data)
    save_settings_data(settings)
    return jsonify({"success": True})


@app.route("/api/links/<enhancement_ref_id>", methods=["DELETE"])
def remove_link(enhancement_ref_id):
    links = load_links()
    if enhancement_ref_id not in links:
        return jsonify({"error": "Link not found"}), 404
    del links[enhancement_ref_id]
    save_links(links)
    return jsonify({"success": True})




if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    start_scheduler()
    print(f"Starting BSA Overlap Tracker on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)