# AutoMates — Job Application Bots: Demo Guide

Automated job-apply bots for **Naukri, LinkedIn, Indeed, WellFound**. The bot opens each
job and fills the easy-apply form. **You stay in control** — for most platforms the bot
pauses and waits for *you* to review and click the final **Submit** button.

---

## 1. One-Time Setup

You need this only once on a new machine.

### Requirements
- **Python 3.10 or newer**
- **Google Chrome** installed
- Internet connection

### Install
```bash
# 1. Go to the project folder
cd /Users/mac/Projects2/AutoMates

# 2. Activate the Python environment
source ~/.venv/bin/activate

# 3. Install the one dependency
pip install selenium
```

---

## 2. First Launch — Log In

The bots reuse a saved Chrome login, so you log in by hand **once** per website.

```bash
python run.py
```

A menu appears. **Choose option `1` (login).**
- Chrome opens automatically.
- You get a **2-minute window** to log into the website (LinkedIn / Naukri / etc.) by hand.
- Type your username + password in that Chrome window like normal.
- Wait for the timer — your login is now saved.

> Do this for each website you plan to use.

**On every later run, choose `2` (skip setup)** — your login is remembered.

---

## 3. Easiest Way to Run — The Menu

```bash
cd /Users/mac/Projects2/AutoMates
source ~/.venv/bin/activate
python run.py
```

Then pick a number:

| Number | Platform |
|--------|----------|
| 1 | WellFound (apply) |
| 2 | LinkedIn |
| 3 | Naukri |
| 4 | Bumble |
| 5 | Indeed (apply) |

Follow the on-screen prompts. **This is the recommended way for a first demo.**

---

## 4. Direct Commands (Advanced)

If you already have a **search-results URL** from the website, you can run a bot directly.

> **How to get a search URL:** go to the website, search jobs (with your filters), copy the
> URL from the browser address bar, and paste it in quotes.

Always run setup first:
```bash
cd /Users/mac/Projects2/AutoMates && source ~/.venv/bin/activate
```

### LinkedIn
```bash
python3 LINKEDIN/apply_jobs.py "<linkedin-jobs-search-url>"
```

### Indeed
```bash
python3 websites/indeed/apply_jobs.py "<indeed-search-url>"
```

### WellFound
```bash
python3 WELLFOUND/apply_jobs.py "<wellfound-jobs-url>"
```

### Naukri — NVites inbox
```bash
python -m NAUKRI.nvites
```

### Naukri — search results
```bash
python3 NAUKRI/search_apply.py "<naukri-search-url>"
```

**Add `--headless` to any command** to run without showing the Chrome window:
```bash
python3 LINKEDIN/apply_jobs.py "<linkedin-jobs-search-url>" --headless
```

> Tip: the **first** time on any platform, run it **without** `--headless` so you can watch
> what the bot does and step in if needed.

---

## 5. How It Works (What to Expect)

1. Bot opens the search results.
2. For each job, it opens the job and clicks **Apply / Easy Apply**.
3. If a form/modal appears, the bot **pauses up to 10 minutes** so *you* fill any extra
   questions and click the final **Submit** yourself.
4. Jobs that send you to an external company website are **skipped** automatically.
5. Already-applied jobs are remembered, so re-running **continues where it left off** —
   it won't re-apply to the same job.

---

## 6. Multiple Accounts (Naukri only)

Two profiles are supported. Add a flag:
```bash
python -m NAUKRI.nvites --dharmendra      # default
python -m NAUKRI.nvites --ashok           # second account
```

---

## 7. Quick Troubleshooting

| Problem | Fix |
|---------|-----|
| "Not logged in" / login screen shows | Run `python run.py` → option `1` and log in again. |
| Bot can't find a button / does nothing | The website changed its layout. Run without `--headless` to watch; selectors may need an update. |
| `selenium not found` | Run `pip install selenium` inside the activated environment. |
| Chrome version mismatch | Update Chrome to the latest version. |

---

## ⚠️ Important
- **Never share your passwords in chat, screenshots, or this file.**
- Always review applications before final submit — the bot fills, **you** approve.
- Use responsibly and within each platform's terms.
