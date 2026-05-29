# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Project

```bash
# Install dependency
sudo apt install python3-selenium   # Ubuntu
pip install selenium                 # or via pip

# Run
python run.py
```

- First launch: choose `1` (login) — Chrome opens LinkedIn + Naukri for 2-minute manual login window
- Subsequent runs: choose `2` (skip setup)
- Requires Python 3.10+

## Lint & CI

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics   # fatal errors only (CI gate)
flake8 . --count --exit-zero --max-line-length=127 --statistics        # full warnings
```

No active tests — `pytest` line in CI is commented out.

## Architecture

**Entry point:** `run.py` — interactive numbered menu that routes to platform bot classes.

**`setup.py`** — two responsibilities:
1. ChromeDriver download/install to `~/bin/chromedriver/`
2. `loginWindow()` — opens Chrome with persistent profile so user can log in manually; session saved to `~/bin/chrome-data/`

**Shared Chrome profile** at `~/bin/chrome-data` is the persistence mechanism — all bots reuse it so login state survives across runs. Every bot class passes `--user-data-dir=~/bin/chrome-data` to Chrome options.

**Bot pattern** (consistent across all platforms):
- `__init__`: launch Chrome with shared profile, navigate to platform URL, `sleep(2)`
- One primary action method that loops over page elements, opens new tabs for each item, acts, closes tab, switches back to original window
- `go_exit()`: close driver

**Platform modules:**

| Module | Class | What it does |
|--------|-------|--------------|
| `LINKEDIN/recommended_page.py` | `LinkedinBot` | Applies to LinkedIn recommended Easy Apply jobs, paginates |
| `LINKEDIN/search_jobs.py` | `LinkedinBot` | Searches jobs by position+location, applies Easy Apply |
| `LINKEDIN/connect_people.py` | `LinkedinBot` | Connects to people at a given company |
| `LINKEDIN/connect_recruiter.py` | `LinkedinBot` | Searches posts by keyword, sends connection with note to recruiters |
| `LINKEDIN/profile_stalker.py` | `LinkedinBot` | Opens profiles from a seed profile and keeps navigating |
| `LINKEDIN/constants.py` | — | `LINKEDIN_CANDIDATE_INFO` — the message sent to recruiters; **edit this before using connect_recruiter** |
| `NAUKRI/recommended_jobs.py` | `NaukriBot` | Clicks recommended jobs on Naukri, applies via "apply-button" |
| `BUMBLE/swipe.py` | `BumbleBot` | Right-swipes all Bumble profiles in a loop |
| `websites/indeed/recommended_jobs.py` | `NaukriBot` | Copy of Naukri bot structure — navigates to Naukri URL (not Indeed); in progress |

**`config.ini`** — stores LinkedIn CSS class names for job cards and Easy Apply button XPaths. These go stale when LinkedIn updates its DOM; update here when selectors break.

## Key Customization Points

- `LINKEDIN/constants.py` → `LINKEDIN_CANDIDATE_INFO`: recruiter connection message (keep under 300 chars for LinkedIn limit)
- `config.ini`: update XPaths/class names when LinkedIn DOM changes
- `BUMBLE/swipe.py`: `BumbleBot.__init__` uses an explicit chromedriver path (`~/bin/chromedriver/chromedriver`) unlike other bots that use `Service()` with auto-detection
