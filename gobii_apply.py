#!/usr/bin/env python3
"""
Gobii Job Application Agent
Fetches "Ready to Apply" jobs from Airtable, dispatches a Gobii browser-use
task for each one, polls for completion, then updates Airtable status.

Usage:
    python gobii_apply.py              # process all Ready to Apply (one at a time)
    python gobii_apply.py --limit 1    # test: one job only
    python gobii_apply.py --dry-run    # print prompt, don't dispatch task
"""

import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

# ── Paths ─────────────────────────────────────────────────────────────────────
AUTH_FILE    = os.path.expanduser("~/.openclaw/agents/main/agent/auth-profiles.json")
PROFILE_PATH = os.path.expanduser("~/job_profile.json")

DRY_RUN  = "--dry-run"  in sys.argv
LIMIT    = 1
if "--limit" in sys.argv:
    idx = sys.argv.index("--limit")
    LIMIT = int(sys.argv[idx + 1])

# ── Auth ───────────────────────────────────────────────────────────────────────
with open(AUTH_FILE) as f:
    _auth = json.load(f)["profiles"]

AT_KEY      = _auth["airtable:default"]["key"]
AT_BASE_ID  = _auth["airtable:default"]["base_id"]
AT_TABLE_ID = _auth["airtable:default"]["table_id"]
GOBII_KEY      = _auth["gobii:default"]["key"]
GOBII_BASE     = _auth["gobii:default"]["base_url"]   # http://10.0.0.133:8000/api/v1
GOBII_AGENT_ID = _auth["gobii:default"].get("agent_id", "")

# ── Job board credentials (passed to Gobii agent as secrets) ──────────────────
_li   = _auth.get("linkedin:default", {})
_ind  = _auth.get("indeed:default", {})
_dice = _auth.get("dice:default", {})

JOB_BOARD_CREDS = {
    "linkedin": {"email": _li.get("email",""),  "password": _li.get("password","")},
    "indeed":   {"email": _ind.get("email",""), "password": _ind.get("password","")},
    "dice":     {"email": _dice.get("email",""), "password": _dice.get("password","")},
}

# Gobii secrets format: {domain: {username, password}}
GOBII_SECRETS = {}
if _li.get("email"):
    GOBII_SECRETS["https://www.linkedin.com"] = {
        "username": _li["email"], "password": _li["password"]
    }
if _ind.get("email"):
    GOBII_SECRETS["https://www.indeed.com"] = {
        "username": _ind["email"], "password": _ind["password"]
    }
if _dice.get("email"):
    GOBII_SECRETS["https://www.dice.com"] = {
        "username": _dice["email"], "password": _dice["password"]
    }

# ── Candidate profile ──────────────────────────────────────────────────────────
with open(PROFILE_PATH) as f:
    _profile = json.load(f)["profile"]

_per = _profile["personal"]
_exp = _profile["experience"]

APPLICANT = {
    "full_name":  _per["full_name"],
    "email":      _per["email"],
    "phone":      _per["phone"],
    "city":       _per["location"]["city"],
    "state":      _per["location"]["state"],
    "zip":        _per["location"].get("zip", ""),
    "linkedin":   _per.get("linkedin_url", ""),
    "github":     _per.get("github_url", ""),
    "title":      _exp["current_title"],
    "years":      _exp["years_total"],
    "salary_min": _profile["preferences"]["salary_expectations"]["minimum"],
}


# ── Airtable helpers ──────────────────────────────────────────────────────────
def at_get(path, params=None):
    url = f"https://api.airtable.com/v0/{AT_BASE_ID}/{AT_TABLE_ID}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AT_KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def at_patch(record_id, fields):
    url     = f"https://api.airtable.com/v0/{AT_BASE_ID}/{AT_TABLE_ID}/{record_id}"
    payload = json.dumps({"fields": fields}).encode()
    req     = urllib.request.Request(
        url, data=payload, method="PATCH",
        headers={"Authorization": f"Bearer {AT_KEY}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_ready_jobs(limit=1):
    formula = urllib.parse.quote('Status="Ready to Apply"')
    data = at_get("", {"filterByFormula": 'Status="Ready to Apply"', "pageSize": limit})
    return data.get("records", [])


def attachment_url(fields, field_name):
    """Return the first attachment URL from an Airtable attachment field."""
    items = fields.get(field_name, [])
    if items and isinstance(items, list):
        return items[0].get("url", "")
    return ""


# ── Gobii helpers ─────────────────────────────────────────────────────────────
def gobii_post(path, payload):
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{GOBII_BASE}{path}", data=data, method="POST",
        headers={"X-API-Key": GOBII_KEY, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def gobii_get(path):
    req = urllib.request.Request(
        f"{GOBII_BASE}{path}",
        headers={"X-API-Key": GOBII_KEY}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def create_task(prompt):
    """Create a task assigned to the named agent, with job board credentials as secrets."""
    payload = {
        "prompt":          prompt,
        "requires_vision": True,
    }
    if GOBII_SECRETS:
        payload["secrets"] = GOBII_SECRETS

    if GOBII_AGENT_ID:
        return gobii_post(f"/agents/browser-use/{GOBII_AGENT_ID}/tasks/", payload)
    else:
        return gobii_post("/tasks/browser-use/", payload)


def poll_task(task_id, timeout=1200, interval=15):
    """Poll until completed/failed/cancelled or timeout."""
    # Use agent-scoped path if agent is configured, else global path
    if GOBII_AGENT_ID:
        task_path   = f"/agents/browser-use/{GOBII_AGENT_ID}/tasks/{task_id}"
        result_path = f"/agents/browser-use/{GOBII_AGENT_ID}/tasks/{task_id}/result/"
    else:
        task_path   = f"/tasks/browser-use/{task_id}/"
        result_path = f"/tasks/browser-use/{task_id}/result/"

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = gobii_get(task_path)
            status = result.get("status", "")
            if status in ("completed", "failed", "cancelled"):
                try:
                    return gobii_get(result_path)
                except Exception:
                    return result
            print(f"    [{status}] waiting…", flush=True)
        except Exception as e:
            print(f"    poll error: {e}")
        time.sleep(interval)
    return {"status": "timeout"}


# ── Prompt builder ────────────────────────────────────────────────────────────
def build_prompt(record, resume_url, cover_url, cover_text):
    f      = record["fields"]
    title  = f.get("Job Title", "")
    co     = f.get("Company", "")
    url    = f.get("Apply URL", "")
    loc    = f.get("Location", "")
    salary = f.get("Salary", "Not listed")

    cover_snippet = cover_text[:2000] if cover_text else "Not provided"

    return f"""You are an expert job application agent. Your sole task is to fully complete and submit a job application on behalf of the candidate below.

══════════════════════════════════════════════════
 CANDIDATE
══════════════════════════════════════════════════
Full Name   : {APPLICANT['full_name']}
Email       : {APPLICANT['email']}
Phone       : {APPLICANT['phone']}
City/State  : {APPLICANT['city']}, {APPLICANT['state']} {APPLICANT['zip']}
LinkedIn    : {APPLICANT['linkedin']}
GitHub      : {APPLICANT['github']}
Current Role: {APPLICANT['title']}
Experience  : {APPLICANT['years']} years in IT Support / DevOps / Cloud Engineering
Work Auth   : Authorized to work in the US — does NOT require sponsorship
Salary Exp  : ${APPLICANT['salary_min']:,}/yr minimum

══════════════════════════════════════════════════
 JOB TARGET
══════════════════════════════════════════════════
Title   : {title}
Company : {co}
Location: {loc}
Salary  : {salary}
URL     : {url}

══════════════════════════════════════════════════
 RESUME PDF (direct download — upload this file)
══════════════════════════════════════════════════
{resume_url if resume_url else "⚠ No resume URL available — do NOT apply, return REQUIRES_MANUAL"}

══════════════════════════════════════════════════
 COVER LETTER PDF (upload if site accepts PDF)
══════════════════════════════════════════════════
{cover_url if cover_url else "None — paste text instead"}

══════════════════════════════════════════════════
 COVER LETTER TEXT (paste if site has a text box)
══════════════════════════════════════════════════
{cover_snippet}

══════════════════════════════════════════════════
 STEP-BY-STEP INSTRUCTIONS
══════════════════════════════════════════════════
1. Navigate to the apply URL: {url}
2. Click "Apply", "Apply Now", or "Easy Apply" button.
3. LOGIN / ACCOUNT CREDENTIALS:
   - LinkedIn  → sign in: {JOB_BOARD_CREDS['linkedin']['email']} / {JOB_BOARD_CREDS['linkedin']['password']}
   - Indeed    → sign in: {JOB_BOARD_CREDS['indeed']['email']} / {JOB_BOARD_CREDS['indeed']['password']}
   - Dice      → sign in: {JOB_BOARD_CREDS['dice']['email'] or JOB_BOARD_CREDS['linkedin']['email']} / {JOB_BOARD_CREDS['dice']['password'] or JOB_BOARD_CREDS['linkedin']['password']}
   - Company ATS / any other site that requires account creation:
       Email: {APPLICANT['email']}   Password: Muteule@2026
   Do NOT create a new account on LinkedIn, Indeed, or Dice — always sign in.
4. Fill every form field using candidate info above. Never skip a required field.
5. RESUME UPLOAD: download and upload from the Resume PDF URL above.
   - If the site shows a "Upload Resume" or "Attach Resume" button, use it.
   - File name: {APPLICANT['full_name'].replace(' ','_')}_Resume.pdf
6. COVER LETTER: if there is an upload field, upload the cover PDF URL.
   If there is only a text box, paste the cover letter text provided above.
7. Standard answers for common screening questions:
   - Work authorization: Yes, authorized to work in the US
   - Visa sponsorship required: No
   - Salary expectation: {APPLICANT['salary_min']}
   - How did you find this job: LinkedIn / Job Board / Indeed
   - Years of experience: {APPLICANT['years']}
   - Willing to relocate: No (already local / remote)
   - Desired start date: Immediately / 2 weeks
   - Veteran status: I am not a veteran
   - Disability status: I prefer not to say / No disability
8. Review all fields before submitting.
9. Click the final SUBMIT / SEND APPLICATION button.
10. Wait for the confirmation page. Capture the confirmation text or number.

══════════════════════════════════════════════════
 PLATFORM RULES — what to attempt vs skip
══════════════════════════════════════════════════
✅ PROCEED on these platforms:
  - LinkedIn Easy Apply (native in-page form)
  - LinkedIn → redirects to Greenhouse, Lever, or Ashby
  - Indeed native apply
  - Indeed → redirects to Greenhouse or Lever
  - Dice → standard forms, Greenhouse/Lever redirects
  - Direct URLs: greenhouse.io, lever.co, ashby.com

🚫 SKIP & LOG (return status=REQUIRES_MANUAL) on these:
  - Any URL containing: workday.com, myworkdayjobs.com, taleo.net, icims.com
  - LinkedIn/Indeed/Dice that redirects to Workday, Taleo, iCIMS
  - Unknown custom subdomains (e.g. jobs.somecompany.com with unrecognised ATS)
  - Hidden or broken application forms (modal not triggering, form not visible)
  When skipping, set blocker to the exact redirect URL or platform name found.

══════════════════════════════════════════════════
 GENERAL RULES
══════════════════════════════════════════════════
- NEVER fabricate experience, skills, or credentials not listed above.
- If a CAPTCHA cannot be solved automatically, stop — do NOT guess.
- If the site requires a phone/SMS verification code, stop.
- If you reach a dead end or the site is broken, return status=BLOCKED.

══════════════════════════════════════════════════
 RETURN FORMAT (JSON only, no markdown)
══════════════════════════════════════════════════
{{
  "status": "SUBMITTED" | "BLOCKED" | "REQUIRES_MANUAL",
  "platform": "exact platform/ATS name (e.g. LinkedIn Easy Apply, Greenhouse, Workday)",
  "redirect_url": "the URL the apply button redirected to, if any",
  "confirmation": "confirmation number or success message text",
  "blocker": "describe the blocker or skip reason if status != SUBMITTED",
  "applied_at": "ISO 8601 timestamp"
}}"""


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"  GOBII JOB APPLICATION AGENT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Mode: {'DRY RUN' if DRY_RUN else 'LIVE'} | Limit: {LIMIT}")
    print(f"{'='*60}\n")

    records = fetch_ready_jobs(limit=LIMIT)
    if not records:
        print("  No 'Ready to Apply' records found in Airtable.")
        return

    print(f"  Found {len(records)} job(s) to apply to.\n")

    for i, record in enumerate(records, 1):
        fields    = record["fields"]
        record_id = record["id"]
        title     = fields.get("Job Title", "Unknown")
        company   = fields.get("Company", "Unknown")
        apply_url = fields.get("Apply URL", "")

        print(f"  [{i}/{len(records)}] {title} @ {company}")
        print(f"      URL: {apply_url[:70]}")

        # Get PDF URLs from Airtable attachments
        resume_url  = attachment_url(fields, "Resume PDF")
        cover_url   = attachment_url(fields, "Cover Letter PDF")
        cover_text  = fields.get("Cover Letter", "")

        if not resume_url:
            print("  ⚠ No Resume PDF attached — skipping (mark as Requires Manual)")
            at_patch(record_id, {"Status": "Requires Manual", "Notes": (fields.get("Notes","") + "\n⚠ No resume PDF — skipped by Gobii agent").strip()})
            continue

        print(f"      Resume: {resume_url[:60]}…")
        print(f"      Cover:  {cover_url[:60] + '…' if cover_url else 'text only'}")

        prompt = build_prompt(record, resume_url, cover_url, cover_text)

        if DRY_RUN:
            print("\n" + "-"*60)
            print("  [DRY RUN] Prompt preview (first 800 chars):")
            print(prompt[:800])
            print("-"*60 + "\n")
            continue

        # Dispatch Gobii task
        print("  Dispatching Gobii browser task…")
        try:
            task = create_task(prompt)
        except Exception as e:
            print(f"  ✗ Failed to create task: {e}")
            continue

        task_id = task.get("id", "")
        status  = task.get("status", "")
        print(f"  ✓ Task created: {task_id} (status: {status})")

        # Update Airtable with task ID while waiting
        at_patch(record_id, {
            "Notes": (fields.get("Notes", "") + f"\n🤖 Gobii task: {task_id}").strip()
        })

        # Poll for result
        print("  Polling for result (up to 45 min)…")
        result = poll_task(task_id, timeout=2700, interval=20)

        final_status = result.get("status", "timeout")
        print(f"  Task finished: {final_status}")

        # Parse agent output — Gobii returns result as JSON string in "result" key
        import re as _re
        raw_result   = result.get("result") or result.get("output") or ""
        app_status   = "Unknown"
        confirmation = ""
        blocker      = ""
        platform     = ""

        redirect_url = ""
        if isinstance(raw_result, str) and raw_result.strip():
            m = _re.search(r'\{.*\}', raw_result, _re.DOTALL)
            if m:
                try:
                    parsed       = json.loads(m.group())
                    app_status   = parsed.get("status", "Unknown")
                    confirmation = parsed.get("confirmation", "")
                    blocker      = parsed.get("blocker", "")
                    platform     = parsed.get("platform", "")
                    redirect_url = parsed.get("redirect_url", "")
                except json.JSONDecodeError:
                    app_status = "Unknown"
        elif isinstance(raw_result, dict):
            app_status   = raw_result.get("status", "Unknown")
            confirmation = raw_result.get("confirmation", "")
            blocker      = raw_result.get("blocker", "")
            platform     = raw_result.get("platform", "")
            redirect_url = raw_result.get("redirect_url", "")

        # Map to Airtable status
        # REQUIRES_MANUAL = skipped (Workday/Taleo/iCIMS) → add to manual shortlist
        # BLOCKED = agent tried but hit a technical blocker
        airtable_status_map = {
            "SUBMITTED":       "Submitted",
            "BLOCKED":         "Pending Review",
            "REQUIRES_MANUAL": "Pending Review",
        }
        new_at_status = airtable_status_map.get(app_status, "Pending Review")

        skip_label = "⏭ Skipped (manual shortlist)" if app_status == "REQUIRES_MANUAL" else f"🤖 Gobii: {app_status}"
        notes_append = (
            f"\n{skip_label} via {platform}"
            + (f"\n↪ Redirect: {redirect_url}" if redirect_url else "")
            + (f"\n✅ Confirmation: {confirmation}" if confirmation else "")
            + (f"\n⚠ Reason: {blocker}" if blocker else "")
        )

        fields_update = {
            "Status": new_at_status,
            "Notes":  (fields.get("Notes", "") + notes_append).strip(),
        }
        if new_at_status == "Submitted":
            fields_update["Applied Date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        at_patch(record_id, fields_update)

        if app_status == "SUBMITTED":
            print(f"  ✓ SUBMITTED via {platform}")
            print(f"  ✓ Confirmation: {confirmation}")
        elif app_status == "REQUIRES_MANUAL":
            print(f"  ⏭ SKIPPED → manual shortlist ({platform})")
            if redirect_url:
                print(f"    Redirect: {redirect_url}")
        else:
            print(f"  ⚠ {app_status} via {platform}")
            if blocker:
                print(f"    Reason: {blocker[:120]}")
        print(f"  Airtable → {new_at_status}")
        print()

        time.sleep(2)

    print(f"{'='*60}")
    print(f"  Done. Processed {len(records)} job(s).")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
