# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Project

```bash
# Install dependency
pip install selenium

# Run interactive menu
python run.py
```

- First launch: choose `1` (login) — Chrome opens for 2-minute manual login window
- Subsequent runs: choose `2` (skip setup)
- Requires Python 3.10+

**Direct CLI invocations (bypass run.py):**

```bash
# Naukri NVites inbox
python -m NAUKRI.nvites [--headless] [--ashok|--dharmendra]

# Naukri search-results apply
python3 NAUKRI/search_apply.py "<naukri-search-url>" [--headless] [--ashok|--dharmendra]

# LinkedIn / Indeed / WellFound search-results apply (Easy-Apply, human-in-loop)
python3 LINKEDIN/apply_jobs.py "<linkedin-jobs-search-url>" [--headless]
python3 websites/indeed/apply_jobs.py "<indeed-search-url>" [--headless]
python3 WELLFOUND/apply_jobs.py "<wellfound-jobs-url>" [--headless]
```

## Lint & CI

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics   # fatal errors only (CI gate)
flake8 . --count --exit-zero --max-line-length=127 --statistics        # full warnings
```

No active tests — `pytest` line in CI is commented out.

## Architecture

**Entry point:** `run.py` — interactive numbered menu routing to platform bot classes. Menu: 1=WellFound (apply), 2=LinkedIn, 3=Naukri, 4=Bumble, 5=Indeed (apply).

**`setup.py`** — ChromeDriver download/install to `~/bin/chromedriver/` and `loginWindow()` — opens Chrome with persistent profile for manual login.

**Shared Chrome profile** at `~/bin/chrome-data` is the persistence mechanism. Every bot passes `--user-data-dir=~/bin/chrome-data` to Chrome options so login state survives across runs.

**Bot pattern** (consistent across platforms):
- `__init__`: launch Chrome with shared profile, navigate to platform URL, `sleep(2)`
- One primary action method loops over page elements, opens new tabs per item, acts, closes tab, switches back
- `go_exit()`: close driver

**Search-apply pattern (shared):** `NAUKRI/search_apply.py` (`NaukriSearchBot`) is the
reference apply engine. `LINKEDIN/apply_jobs.py`, `websites/indeed/apply_jobs.py`, and
`WELLFOUND/apply_jobs.py` mirror its structure: `__init__(search_url, headless, progress_path,
profile_dir)`, `run()` paginates + dedups via a `*_progress.json` set, `_apply_one()` opens
each job and clicks Apply, `_wait_for_human()` polls up to 10 min while the user fills the
form/modal and submits (the bot never clicks the final Submit). External / "company site"
jobs are detected and recorded as `external` so they're skipped on re-run. Each file has a
`# Selectors` block at the top — **selectors are best-effort and need live tuning** when the
portal changes its DOM; run headful the first time to watch and fix.

**Multi-account system (Naukri only):**
`NAUKRI/accounts.py` defines two accounts: `dharmendra` (default) and `ashok`. Each has its own Chrome profile (`chrome-data` vs `chrome-data-ashok`), progress files, and answers file. Pass `--ashok` or `--dharmendra` on the CLI; `run.py` prompts interactively. Default is `dharmendra` — zero migration, original filenames preserved.

**Platform modules:**

| Module | Class | What it does |
|--------|-------|--------------|
| `LINKEDIN/apply_jobs.py` | `LinkedInApplyBot` | **Preferred LinkedIn apply.** Walks a jobs search-result URL, Easy-Apply only, pauses for human on the modal, skips external, resumes from linkedin_progress.json |
| `LINKEDIN/recommended_page.py` | `LinkedinBot` | Legacy: applies to LinkedIn recommended Easy Apply jobs, paginates (brittle, auto-submits) |
| `LINKEDIN/search_jobs.py` | `LinkedinBot` | Legacy: searches jobs by position+location, applies Easy Apply (brittle, auto-submits) |
| `LINKEDIN/connect_people.py` | `LinkedinBot` | Connects to people at a given company |
| `LINKEDIN/connect_recruiter.py` | `LinkedinBot` | Searches posts by keyword, sends connection with note to recruiters |
| `LINKEDIN/profile_stalker.py` | `LinkedinBot` | Opens profiles from a seed profile and keeps navigating |
| `LINKEDIN/constants.py` | — | `LINKEDIN_CANDIDATE_INFO` — recruiter message; **edit before using connect_recruiter** |
| `NAUKRI/recommended_jobs.py` | `NaukriBot` | Clicks recommended jobs on Naukri, applies via "apply-button" |
| `NAUKRI/nvites.py` | `NaukriNVitesBot` | Processes NVites inbox (2-panel): clicks each card, clicks Apply, waits for user to fill chat screening questions and Save; resumes from progress.json |
| `NAUKRI/search_apply.py` | `NaukriSearchBot` | Walks Naukri search-result pages, applies to each job; skips "Apply on company site" jobs; resumes from search_progress.json |
| `NAUKRI/accounts.py` | — | Account registry for multi-user support; `resolve(argv)` picks account from CLI flags |
| `BUMBLE/swipe.py` | `BumbleBot` | Right-swipes all Bumble profiles in a loop |
| `WELLFOUND/apply_jobs.py` | `WellFoundApplyBot` | Walks a WellFound jobs URL (scroll-loaded list), clicks Apply, pauses for human on message modal, skips external, resumes from wellfound_progress.json |
| `WELLFOUND/main_page.py` | `WellFound` | Legacy stub; `connect()` uses LinkedIn selectors, `apply()` not implemented — superseded by `apply_jobs.py` |
| `websites/indeed/apply_jobs.py` | `IndeedApplyBot` | Walks an Indeed search URL, clicks "Apply now", pauses for human on SmartApply, skips company-site/external, resumes from indeed_progress.json |

**Selector locations:** Each Naukri script has a `# Selectors` block at the top — update CSS selectors and XPaths there when Naukri changes its DOM.

**Progress & log files:**

| File | Purpose |
|------|---------|
| `LINKEDIN/linkedin_progress.json` / `LINKEDIN/apply_jobs.log` | LinkedIn apply progress + log |
| `websites/indeed/indeed_progress.json` / `websites/indeed/apply_jobs.log` | Indeed apply progress + log |
| `WELLFOUND/wellfound_progress.json` / `WELLFOUND/apply_jobs.log` | WellFound apply progress + log |
| `NAUKRI/progress.json` | NVites applied IDs — dharmendra |
| `NAUKRI/progress_ashok.json` | NVites applied IDs — ashok |
| `NAUKRI/search_progress.json` | Search-apply job IDs — dharmendra |
| `NAUKRI/search_progress_ashok.json` | Search-apply job IDs — ashok |
| `NAUKRI/answers.json` | Screening question answers — dharmendra |
| `NAUKRI/answers_ashok.json` | Screening question answers — ashok |
| `NAUKRI/nvites.log` | NVites run log |
| `NAUKRI/search_apply.log` | Search-apply run log |
| `NAUKRI/pending_question.json` | Temp file written when NVitesBot hits an unknown screening question; deleted once answered |

## Key Customization Points

- `LINKEDIN/constants.py` → `LINKEDIN_CANDIDATE_INFO`: recruiter connection message (keep under 300 chars)
- `config.ini`: LinkedIn CSS class names / XPaths — update when LinkedIn DOM changes
- `NAUKRI/answers.json` (or `answers_ashok.json`): populate `current_ctc`, `expected_ctc`, `notice_period`, `total_experience`, location, etc. before running NVites — bot matches questions via `KEYWORD_MAP` in `nvites.py`
- `BUMBLE/swipe.py`: uses explicit chromedriver path (`~/bin/chromedriver/chromedriver`) unlike other bots that use `Service()` with auto-detection
- `WELLFOUND/main_page.py`: uses explicit chromedriver path (same as Bumble)
