# Job Application Pipeline — Capabilities Overview

Full end-to-end autonomous job search and application system. Runs on WSL2 Ubuntu, controlled via WhatsApp or Telegram, backed by Airtable for tracking and Gobii for browser automation.

---

## Architecture at a Glance

```
WhatsApp / Telegram
       │
       ▼
 OpenClaw Agent  (GPT-4o-mini)
       │
       ├─ Job URL received ──────────► tailor_from_url.py
       │                                      │
       └─ Search command ────────────► job_pipeline.py
                                              │
                    ┌─────────────────────────┴──────────────────┐
                    │                                             │
              JSearch API                                  ai_tailoring.py
         (LinkedIn / Indeed /                        (GPT-4o-mini tailoring)
          Glassdoor / ZipRecruiter)                           │
                    │                                    pdf_generator.py
                    └─────────────────────────┬──────────────────┘
                                              │
                                       airtable_sync.py
                                    (Airtable "Job Applications")
                                              │
                                       drive_uploader.py
                                    (Google Drive PDF hosting)
                                              │
                                       gobii_apply.py
                                    (Gobii browser agent — auto-apply)
```

---

## 1. Job Discovery

### Multi-Platform Search (`job_pipeline.py`)
- Searches LinkedIn, Indeed, Glassdoor, ZipRecruiter, and Wellfound simultaneously via JSearch (RapidAPI)
- Triggered from WhatsApp/Telegram with natural language:
  - *"Search for DevOps jobs posted this week"*
  - *"Find cloud engineer roles, last 3 days, LinkedIn only"*
- CLI flags: `--query`, `--titles`, `--days`, `--boards`, `--experience`, score threshold

### URL-Based Tailoring (`tailor_from_url.py`)
- Paste any job posting URL into WhatsApp/Telegram — pipeline fetches, extracts, tailors, and syncs automatically
- **Multi-tier fetch strategy** (in order):
  1. Direct HTTP with browser headers
  2. Jina AI Reader (`r.jina.ai`) — handles Cloudflare, JS-rendered SPAs (Dice, LinkedIn, Indeed)
  3. FETCH_FAILED → prompts user to paste job description manually
- Supported platforms detected automatically: LinkedIn, Indeed, Dice, Glassdoor, ZipRecruiter, Wellfound, Lever, Greenhouse, Workday, iCIMS, Taleo, Jobvite, SmartRecruiters, Ashby

---

## 2. AI Tailoring

### Resume Tailoring (`ai_tailoring.py`)
- GPT-4o-mini rewrites every resume bullet per job using **action + artifact + tools + impact** structure
- Generates a tailored 2-sentence professional summary per job
- Produces a keyword map: JD requirement → where it appears in resume
- ATS-aligned — mirrors JD language without keyword stuffing
- Flags missing metrics with targeted questions

### Cover Letter Generation
- 250–350 word letters in strict 3-part structure: intro → 3 bullet highlights → CTA
- Each bullet maps a specific JD requirement to a proof point from the candidate profile
- Generates an email subject line per application
- Names the real company and role throughout — no placeholder brackets

---

## 3. Scoring & Filtering

- GPT-4o-mini scores each job 0.0–1.0 against the candidate profile
- Returns: `score`, `match_reasons`, `gaps`, `salary_ok`
- Configurable threshold (default 70%) — jobs below threshold skipped
- All scoring fields written to Airtable columns

---

## 4. PDF Generation (`pdf_generator.py`)

- Produces per-job tailored resume PDF and cover letter PDF using `reportlab`
- Stored locally at `~/job_applications/YYYY-MM-DD/pdfs/`
- Falls back to generic resume PDF if tailoring data unavailable

---

## 5. Google Drive Integration (`drive_uploader.py`)

- Uploads all PDFs to Google Drive folder: `Job Applications/{date}/pdfs/`
- Makes each file publicly accessible (anyone-with-link)
- Returns permanent download URLs (`drive.google.com/uc?export=download&id=...`)
- Patch Airtable `Resume PDF` and `Cover Letter PDF` fields with real Drive-hosted attachments (thumbnails visible in Airtable)
- Batch mode: retroactively upload and patch all existing records

---

## 6. Airtable Tracking (`airtable_sync.py`)

Full job tracking database auto-created and managed.

| Field | Content |
|---|---|
| Job Title | Role name |
| Company | Employer |
| Score | GPT match score (0–100) |
| Location | City/state or Remote |
| Salary | Range if listed |
| Platform | Source (linkedin / indeed / dice / url-tailor) |
| Match Reasons | Why this job fits the profile |
| Skill Gaps | Missing skills |
| Apply URL | Direct link to posting |
| Cover Letter | Full cover letter text |
| Resume PDF | Drive-hosted attachment with thumbnail |
| Cover Letter PDF | Drive-hosted attachment with thumbnail |
| Status | Workflow state (see below) |
| Applied Date | Set automatically on submission |
| Notes | Gobii task ID, blocker details, redirect URLs |

**Status workflow:**
`Pending Review` → `Ready to Apply` → `Submitted` → `Interview Scheduled` → `Offer`

- Full deduplication — already-synced jobs are skipped
- Batch sync from JSON: `python airtable_sync.py --json ~/job_applications/matches.json`

---

## 7. Autonomous Application (`gobii_apply.py`)

Browser automation via **Gobii AI** (self-hosted at `10.0.0.133:8000`).

### How it works
1. Fetches all Airtable records with `Status = "Ready to Apply"`
2. For each job, dispatches a Gobii browser-use task assigned to **"Benjamin Job Applier"** agent
3. Agent navigates to the apply URL, signs in, fills the form, uploads PDFs, and submits
4. Polls for task completion (up to 45 min)
5. Updates Airtable with result: status, confirmation number, blocker details

### Credentials handled automatically
| Platform | Action |
|---|---|
| LinkedIn | Signs in with stored credentials — never creates new account |
| Indeed | Signs in with stored credentials |
| Dice | Signs in with stored credentials |
| Company ATS | Creates account if needed using candidate email/password |

### Platform decision rules

**Attempt these:**
- LinkedIn Easy Apply (in-page form)
- LinkedIn / Indeed / Dice → redirects to Greenhouse, Lever, or Ashby
- Direct `greenhouse.io`, `lever.co`, `ashby.com` URLs

**Skip & log to manual shortlist:**
- Any Workday / `myworkdayjobs.com` / Taleo / iCIMS URL
- LinkedIn/Indeed/Dice redirecting to Workday, Taleo, or iCIMS
- Hidden/broken forms (modal not triggering, form not visible in DOM)

Skipped jobs are logged to Airtable Notes with the redirect URL — becoming a **manual apply shortlist** of high-value roles already vetted as relevant.

### Gobii agent
- Named agent **"Benjamin Job Applier"** visible in Gobii dashboard under Agents tab
- Linked browser-use agent: `3c738466-0521-4fa8-8abd-490c8d94f455`
- Credentials passed via Gobii `secrets` field (domain-scoped, not exposed in logs)
- **Bright Data MCP** attached (CAPTCHA bypass capability)

---

## 8. WhatsApp / Telegram Agent Integration

Fully controllable from your phone via OpenClaw (`@Wezaai_bot` on Telegram, WhatsApp `+12539888504`).

| Message | What runs |
|---|---|
| `Search for DevOps jobs` | `job_pipeline.py --query "devops engineer"` |
| `Find cloud roles posted this week` | `job_pipeline.py --query "cloud engineer" --days 7` |
| Paste any job URL | `tailor_from_url.py "URL"` → tailored PDFs + Airtable record |
| Job URL with `--no-pdf` | Skips PDF generation, faster |

After tailoring, the agent replies with only a short notification:
```
✅ Tailored: DevOps Engineer at Acme Corp
📍 Remote  💰 $120,000–$150,000/yr
📊 Match score: 87%
✉️  Subject: DevOps Engineer — Benjamin Mbugua
🔗 Review & download PDFs: https://airtable.com/appXXX/tblXXX/recXXX
```

---

## 9. Security & Credential Management

- All credentials stored in `~/.openclaw/agents/main/agent/auth-profiles.json` (local, never committed)
- OAuth tokens (`drive_token.json`, `sheets_token.json`) excluded from git
- Gobii credentials passed as domain-scoped secrets — not in task prompt logs
- `.gitignore` covers all sensitive files

---

## 10. Supported Job Platforms

| Platform | Search | URL Tailor | Auto-Apply |
|---|---|---|---|
| LinkedIn | ✅ JSearch | ✅ Jina fallback | ✅ Easy Apply |
| Indeed | ✅ JSearch | ✅ Jina fallback | ✅ Native + Greenhouse/Lever redirects |
| Glassdoor | ✅ JSearch | ✅ | — |
| ZipRecruiter | ✅ JSearch | ✅ | — |
| Wellfound | ✅ JSearch | ✅ | — |
| Dice | ✅ URL tailor | ✅ Jina fallback | ✅ Standard forms |
| Greenhouse | ✅ URL tailor | ✅ | ✅ |
| Lever | ✅ URL tailor | ✅ | ✅ |
| Ashby | — | ✅ | ✅ |
| Workday | — | ✅ | ⏭ Skip → manual list |
| Taleo | — | ✅ | ⏭ Skip → manual list |
| iCIMS | — | ✅ | ⏭ Skip → manual list |

---

## Key Files

| File | Purpose |
|---|---|
| `job_pipeline.py` | Main search → score → tailor → PDF → Airtable pipeline |
| `tailor_from_url.py` | URL-based tailoring with Jina fallback |
| `ai_tailoring.py` | GPT-4o-mini resume + cover letter engine |
| `airtable_sync.py` | Airtable create/update/query helpers |
| `pdf_generator.py` | reportlab PDF generation |
| `drive_uploader.py` | Google Drive upload + public URL helper |
| `gobii_apply.py` | Gobii browser agent job application dispatcher |
| `linkedin_apply.py` | Playwright Easy Apply automation (alternative) |
| `it_support_ea_search.py` | IT support focused search with Easy Apply verification |

---

## Credentials Required

| Service | Purpose |
|---|---|
| OpenAI | GPT-4o-mini scoring, tailoring, cover letters |
| RapidAPI (JSearch) | Job search across 5 platforms |
| Airtable PAT | Job tracking database |
| Google OAuth | Drive PDF hosting |
| LinkedIn | Auto-apply sign-in |
| Indeed | Auto-apply sign-in |
| Dice | Auto-apply sign-in |
| Gobii API Key | Browser automation agent |
