import json
from collections import defaultdict
from datetime import datetime

EXCEL_PATH = "Strategic Initiatives & Operations Intake Form.xlsx"
SHEET_NAME = "Strategic Initiatives _ Operati"
OUTPUT_PATH = "overlap_data.json"

ACTIVE_STATUSES = {"In Progress", "Discovery", "New", "Under Review"}

BSA_TEAM = {
    "Jordan Butler",
    "Zack Godat",
    "Jennifer Bednar",
    "Dustin Hartrick",
    "Ellen Clegg",
    "Jennifer Cummings",
    "Angela Rhodes",
    "Tori Yardley",
    "Dan Temperly",
}

HIGH_RISK_SYSTEMS = {
    "Deal Enhancement",
    "Empower Enhancement",
    "CRM/Salesforce Enhancement",
    "Data Warehouse / Analytics",
    "Multiple Systems",
}


def _clean(val, default=""):
    if val is None or str(val).strip().lower() in ("nan", "none"):
        return default
    return str(val).strip()


def load_data(path=EXCEL_PATH):
    # Local use only (reads Excel file). Not called on Render.
    import pandas as pd
    df = pd.read_excel(path, sheet_name=SHEET_NAME, header=0)
    df = df.rename(columns={
        "Ref ID": "ref_id",
        "Initiative Name": "initiative_name",
        "Request Title": "request_title",
        "Owner": "bsa_owner",
        "Requestor Name": "requestor",
        "Primary System Impacted": "system",
        "Status": "status",
        "Request Type": "request_type",
        "Request Date": "request_date",
        "Business Priority": "priority",
        "Program": "program",
    })
    cols = ["ref_id", "initiative_name", "request_title", "bsa_owner",
            "requestor", "system", "status", "request_type",
            "request_date", "priority", "program"]
    df = df[[c for c in cols if c in df.columns]].copy()
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "ref_id": _clean(row.get("ref_id"), ""),
            "initiative_name": _clean(row.get("initiative_name"), ""),
            "request_title": _clean(row.get("request_title"), ""),
            "bsa_owner": _clean(row.get("bsa_owner"), "Unassigned"),
            "requestor": _clean(row.get("requestor"), "Unknown"),
            "system": _clean(row.get("system"), "Unknown"),
            "status": _clean(row.get("status"), "Unknown"),
            "request_type": _clean(row.get("request_type"), "Unknown"),
            "request_date": _clean(row.get("request_date"), ""),
            "priority": _clean(row.get("priority"), "Unknown"),
            "program": _clean(row.get("program"), "Unknown"),
            "notes": "",
        })
    return rows


def get_active(rows):
    def has_bsa_owner(owner_str):
        owners = [o.strip() for o in str(owner_str).split(",")]
        return any(o in BSA_TEAM for o in owners)
    return [r for r in rows if r["status"] in ACTIVE_STATUSES and has_bsa_owner(r["bsa_owner"])]


def parse_owners(owner_str):
    return [o.strip() for o in owner_str.split(",") if o.strip()]


def detect_overlaps(active):
    bsa_workload = defaultdict(list)
    system_overlap = defaultdict(list)
    requestor_bsa_map = defaultdict(lambda: defaultdict(list))
    co_owner_projects = []
    project_flags = {}

    for row in active:
        ref = row["ref_id"]
        owners = parse_owners(row["bsa_owner"])
        system = row["system"]
        requestor = row["requestor"]

        for owner in owners:
            bsa_workload[owner].append(ref)

        system_overlap[system].append(ref)

        for owner in owners:
            requestor_bsa_map[requestor][owner].append(ref)

        if len(owners) > 1:
            co_owner_projects.append({
                "ref_id": ref,
                "owners": owners,
                "system": system,
                "title": row["request_title"] or row["initiative_name"],
                "status": row["status"],
            })

    requestor_multi_bsa = {}
    for req, bsa_dict in requestor_bsa_map.items():
        if len(bsa_dict) > 1:
            requestor_multi_bsa[req] = {
                bsa: refs for bsa, refs in bsa_dict.items()
            }

    for row in active:
        ref = row["ref_id"]
        owners = parse_owners(row["bsa_owner"])
        system = row["system"]
        flags = []
        score = 0

        for owner in owners:
            if len(bsa_workload[owner]) >= 10:
                flags.append(f"BSA overloaded: {owner} has {len(bsa_workload[owner])} active items")
                score += 1

        if len(system_overlap[system]) >= 5:
            flags.append(f"Hot system: {len(system_overlap[system])} active projects touch {system}")
            score += 1

        if system in HIGH_RISK_SYSTEMS:
            flags.append(f"High-risk system: {system}")
            score += 1

        if len(owners) > 1:
            flags.append(f"Co-owned by {len(owners)} BSAs - coordination needed")
            score += 1

        project_flags[ref] = {
            "overlap_score": score,
            "flags": flags,
            "risk_level": "High Touch" if score >= 3 else "Medium" if score >= 1 else "Low Touch",
        }

    return bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags


def build_output(active, bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags):
    projects = []
    for row in active:
        ref = row["ref_id"]
        flag_data = project_flags.get(ref, {"overlap_score": 0, "flags": [], "risk_level": "Low Touch"})
        bsa_owners_filtered = [o.strip() for o in row["bsa_owner"].split(",") if o.strip() in BSA_TEAM]
        bsa_display = ", ".join(bsa_owners_filtered) if bsa_owners_filtered else row["bsa_owner"]
        projects.append({
            "ref_id": ref,
            "title": (row["request_title"] or row["initiative_name"] or "Untitled")[:80],
            "initiative_name": row.get("initiative_name", ""),
            "bsa_owner": bsa_display,
            "requestor": row["requestor"],
            "system": row["system"],
            "status": row["status"],
            "priority": row["priority"],
            "program": row["program"],
            "request_type": row.get("request_type", ""),
            "request_date": row.get("request_date", ""),
            "notes": row.get("notes", ""),
            "overlap_score": flag_data["overlap_score"],
            "risk_level": flag_data["risk_level"],
            "flags": flag_data["flags"],
        })

    projects.sort(key=lambda x: x["overlap_score"], reverse=True)

    bsa_summary = []
    for bsa, refs in sorted(bsa_workload.items(), key=lambda x: -len(x[1])):
        bsa_summary.append({
            "name": bsa,
            "active_count": len(refs),
            "ref_ids": refs,
            "overloaded": len(refs) >= 10,
        })

    system_summary = []
    for sys, refs in sorted(system_overlap.items(), key=lambda x: -len(x[1])):
        system_summary.append({
            "system": sys,
            "active_count": len(refs),
            "ref_ids": refs,
            "high_risk": sys in HIGH_RISK_SYSTEMS,
        })

    req_summary = []
    for req, bsa_dict in sorted(requestor_multi_bsa.items(), key=lambda x: -len(x[1])):
        total_projects = sum(len(v) for v in bsa_dict.values())
        req_summary.append({
            "requestor": req,
            "bsa_count": len(bsa_dict),
            "total_projects": total_projects,
            "breakdown": bsa_dict,
        })

    return {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_active": len(active),
            "high_touch_count": sum(1 for p in projects if p["risk_level"] == "High Touch"),
            "medium_count": sum(1 for p in projects if p["risk_level"] == "Medium"),
            "low_touch_count": sum(1 for p in projects if p["risk_level"] == "Low Touch"),
            "co_owned_count": len(co_owner_projects),
            "multi_bsa_requestors": len(req_summary),
            "total_bsas": len(bsa_summary),
            "total_systems": len(system_summary),
        },
        "projects": projects,
        "bsa_workload": bsa_summary,
        "system_overlap": system_summary,
        "requestor_overlap": req_summary,
        "co_owner_projects": co_owner_projects,
    }


def run():
    print("Loading intake data...")
    rows = load_data(EXCEL_PATH)
    active = get_active(rows)
    print(f"  Found {len(rows)} total rows, {len(active)} active projects")

    print("Detecting overlaps...")
    bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags = detect_overlaps(active)

    high = sum(1 for f in project_flags.values() if f["risk_level"] == "High Touch")
    medium = sum(1 for f in project_flags.values() if f["risk_level"] == "Medium")
    print(f"  High Touch: {high} | Medium: {medium} | Co-owned: {len(co_owner_projects)}")

    print("Building output...")
    output = build_output(active, bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"  Written to {OUTPUT_PATH}")
    return output


if __name__ == "__main__":
    run()