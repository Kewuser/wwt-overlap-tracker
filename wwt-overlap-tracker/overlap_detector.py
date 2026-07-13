"""
BSA Project Overlap Detector
==============================
Reads the Smartsheet intake Excel export, detects overlap patterns,
and outputs a JSON file used by the dashboard.

HOW IT WORKS (for your own understanding):
  1. Load the intake form Excel file
  2. Filter to only ACTIVE projects (In Progress, Discovery, New, Under Review)
  3. For each project, parse BSA owners and systems (some entries have multiple, comma-separated)
  4. Build three overlap maps:
       - BSA overlap: which BSAs share the same project (co-owners)
       - System overlap: which projects touch the same system
       - Requestor overlap: which requestors are submitting to multiple BSAs
  5. Flag individual projects with an overlap score (0 = clean, 1+ = overlapping)
  6. Write results to overlap_data.json for the dashboard to read

FUTURE: Replace load_data() with Smartsheet API call (see api_integration.py)
"""

import pandas as pd
import json
from collections import defaultdict
from datetime import datetime

# ─── CONFIG ──────────────────────────────────────────────────────────────────
EXCEL_PATH = "Strategic Initiatives & Operations Intake Form.xlsx"
SHEET_NAME = "Strategic Initiatives _ Operati"
OUTPUT_PATH = "overlap_data.json"

ACTIVE_STATUSES = {"In Progress", "Discovery", "New", "Under Review"}
# NOTE: Backlog excluded per Ellen Clegg (confirmed 7/1/2026). Active project count = ~144.

# BSA team filter — only show projects owned by these 8 people (confirmed scope 7/9/2026)
# Smartsheet contains data from other teams too; this keeps the dashboard BSA-only.
BSA_TEAM = {
    "Jordan Butler",
    "Zack Godat",
    "Jennifer Bednar",
    "Dustin Hartrick",
    "Ellen Clegg",
    "Jennifer Cummings",
    "Angela Rhodes",
    "Tori Yardley",
}

# Systems that are "hot" (high-overlap risk)
HIGH_RISK_SYSTEMS = {
    "Deal Enhancement",
    "Empower Enhancement",
    "CRM/Salesforce Enhancement",
    "Data Warehouse / Analytics",
    "Multiple Systems",
}
# ─────────────────────────────────────────────────────────────────────────────


def load_data(path=EXCEL_PATH):
    """Load and clean the intake Excel file."""
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
    # Keep only the columns we care about
    cols = ["ref_id", "initiative_name", "request_title", "bsa_owner",
            "requestor", "system", "status", "request_type",
            "request_date", "priority", "program"]
    df = df[cols].copy()
    df["ref_id"] = df["ref_id"].fillna("").astype(str).str.strip()
    df["bsa_owner"] = df["bsa_owner"].fillna("Unassigned").astype(str).str.strip()
    df["requestor"] = df["requestor"].fillna("Unknown").astype(str).str.strip()
    df["system"] = df["system"].fillna("Unknown").astype(str).str.strip()
    df["status"] = df["status"].fillna("Unknown").astype(str).str.strip()
    df["initiative_name"] = df["initiative_name"].fillna("").astype(str).str.strip()
    df["request_title"] = df["request_title"].fillna("").astype(str).str.strip()
    df["priority"] = df["priority"].fillna("Unknown").astype(str).str.strip()
    df["program"] = df["program"].fillna("Unknown").astype(str).str.strip()
    return df


def get_active(df):
    """
    Filter to only active projects owned by BSA team members.
    NOTE: Smartsheet has entries from many teams. This filter keeps only the
    8 confirmed BSA team members (confirmed scope update 7/9/2026).
    Also note: Smartsheet intake form = ENHANCEMENTS only, not large projects.
    Large projects are entered manually via the dashboard Add Project form.
    """
    active = df[df["status"].isin(ACTIVE_STATUSES)].copy()
    # Keep rows where at least one owner is on the BSA team
    def has_bsa_owner(owner_str):
        owners = [o.strip() for o in str(owner_str).split(",")]
        return any(o in BSA_TEAM for o in owners)
    return active[active["bsa_owner"].apply(has_bsa_owner)].copy()


def parse_owners(owner_str):
    """Split 'Jennifer Bednar, Zack Godat' into ['Jennifer Bednar', 'Zack Godat']."""
    return [o.strip() for o in owner_str.split(",") if o.strip()]


def detect_overlaps(active_df):
    """
    Core overlap detection logic.

    Returns:
      - bsa_workload:   dict { bsa_name: [list of ref_ids] }
      - system_overlap: dict { system: [list of ref_ids] }
      - requestor_multi_bsa: dict { requestor: { bsa: [ref_ids] } }  — requestors who submit to >1 BSA
      - co_owner_projects: list of { ref_id, owners, system } for projects with 2+ BSA owners
      - project_flags: dict { ref_id: { overlap_score, flags[] } }
    """

    bsa_workload = defaultdict(list)
    system_overlap = defaultdict(list)
    requestor_bsa_map = defaultdict(lambda: defaultdict(list))
    co_owner_projects = []
    project_flags = {}

    for _, row in active_df.iterrows():
        ref = row["ref_id"]
        owners = parse_owners(row["bsa_owner"])
        system = row["system"]
        requestor = row["requestor"]

        # BSA workload map
        for owner in owners:
            bsa_workload[owner].append(ref)

        # System overlap map
        system_overlap[system].append(ref)

        # Requestor → BSA map
        for owner in owners:
            requestor_bsa_map[requestor][owner].append(ref)

        # Co-ownership (2+ BSAs on one project)
        if len(owners) > 1:
            co_owner_projects.append({
                "ref_id": ref,
                "owners": owners,
                "system": system,
                "title": row["request_title"] or row["initiative_name"],
                "status": row["status"],
            })

    # Requestors submitting to multiple BSAs
    requestor_multi_bsa = {}
    for req, bsa_dict in requestor_bsa_map.items():
        if len(bsa_dict) > 1:
            requestor_multi_bsa[req] = {
                bsa: refs for bsa, refs in bsa_dict.items()
            }

    # Per-project overlap flags
    for _, row in active_df.iterrows():
        ref = row["ref_id"]
        owners = parse_owners(row["bsa_owner"])
        system = row["system"]
        flags = []
        score = 0

        # Flag 1: BSA overloaded (10+ active items — threshold confirmed by Ellen 7/1/2026)
        for owner in owners:
            if len(bsa_workload[owner]) >= 10:
                flags.append(f"BSA overloaded: {owner} has {len(bsa_workload[owner])} active items")
                score += 1

        # Flag 2: System has 5+ active projects (hot system)
        if len(system_overlap[system]) >= 5:
            flags.append(f"Hot system: {len(system_overlap[system])} active projects touch {system}")
            score += 1

        # Flag 3: High-risk system
        if system in HIGH_RISK_SYSTEMS:
            flags.append(f"High-risk system: {system}")
            score += 1

        # Flag 4: Co-ownership (coordination risk)
        if len(owners) > 1:
            flags.append(f"Co-owned by {len(owners)} BSAs — coordination needed")
            score += 1

        project_flags[ref] = {
            "overlap_score": score,
            "flags": flags,
            "risk_level": "High Touch" if score >= 3 else "Medium" if score >= 1 else "Low Touch",
        }

    return bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags


def build_output(active_df, bsa_workload, system_overlap, requestor_multi_bsa, co_owner_projects, project_flags):
    """Package everything into a JSON structure for the dashboard."""

    # Build project list
    projects = []
    for _, row in active_df.iterrows():
        ref = row["ref_id"]
        flag_data = project_flags.get(ref, {"overlap_score": 0, "flags": [], "risk_level": "Low Touch"})
        projects.append({
            "ref_id": ref,
            "title": (row["request_title"] or row["initiative_name"] or "Untitled")[:80],
            "bsa_owner": row["bsa_owner"],
            "requestor": row["requestor"],
            "system": row["system"],
            "status": row["status"],
            "priority": row["priority"],
            "program": row["program"],
            "request_type": row["request_type"],
            "overlap_score": flag_data["overlap_score"],
            "risk_level": flag_data["risk_level"],
            "flags": flag_data["flags"],
        })

    # Sort by overlap score desc
    projects.sort(key=lambda x: x["overlap_score"], reverse=True)

    # BSA summary
    bsa_summary = []
    for bsa, refs in sorted(bsa_workload.items(), key=lambda x: -len(x[1])):
        bsa_summary.append({
            "name": bsa,
            "active_count": len(refs),
            "ref_ids": refs,
            "overloaded": len(refs) >= 10,
        })

    # System summary
    system_summary = []
    for sys, refs in sorted(system_overlap.items(), key=lambda x: -len(x[1])):
        system_summary.append({
            "system": sys,
            "active_count": len(refs),
            "ref_ids": refs,
            "high_risk": sys in HIGH_RISK_SYSTEMS,
        })

    # Requestor multi-BSA
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
            "total_active": len(active_df),
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
    df = load_data(EXCEL_PATH)
    active = get_active(df)
    print(f"  Found {len(df)} total rows, {len(active)} active projects")

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
    print("\nDone. Open dashboard.html in your browser to see results.")
    return output


if __name__ == "__main__":
    run()