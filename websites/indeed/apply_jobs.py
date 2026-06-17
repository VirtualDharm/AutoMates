"""
Indeed SEARCH-results auto-apply.

Give it an Indeed job-search URL. It walks every result card across all pages,
opens each job, clicks "Apply now", then WAITS while YOU fill the application
form (Indeed SmartApply / screening questions) and submit. Bot never submits.

  python3 websites/indeed/apply_jobs.py "<indeed-search-url>"
  python3 websites/indeed/apply_jobs.py "<indeed-search-url>" --headless

Modeled on NAUKRI/search_apply.py (NaukriSearchBot): logging, progress-file
dedup + resume, anti-detection options, screenshots dir, external-job skip.

Skips:
  - jobs already applied (websites/indeed/indeed_progress.json)
  - "Apply on company site" jobs (external redirect — can't automate)
"""

import json
import logging
import os
import sys
from time import sleep
from os import path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from selenium.common.exceptions import (
    NoSuchElementException,
    NoSuchWindowException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_factory import make_driver
except ImportError:
    import sys as _sys
    _sys.path.insert(0, path.dirname(path.dirname(path.dirname(path.abspath(__file__)))))
    from webdriver_factory import make_driver

# ---------------------------------------------------------------------------
# Selectors — update here when Indeed changes its DOM
# ---------------------------------------------------------------------------
# Left panel: each search result card (anchor carries the job key in data-jk)
SEARCH_CARD = (By.CSS_SELECTOR, "a.jcs-JobTitle[data-jk], div.job_seen_beacon a[data-jk]")

# Job detail / right pane: the Apply button.
# Indeed Apply (native)  -> "Apply with Indeed" / "Easily apply" widget button
# External                -> "Apply on company site" (opens new tab)
APPLY_BTN = (By.XPATH,
             "//button[contains(., 'Apply with Indeed') or contains(., 'Easily apply')]"
             " | //*[@id='indeedApplyButton']"
             " | //div[contains(@class,'indeed-apply')]//button")
COMPANY_SITE_BTN = (By.XPATH,
                    "//a[contains(., 'Apply on company site') or contains(., 'company site')]"
                    " | //a[contains(@href, '/applystart')]")

# Cloudflare bot-challenge interstitial ("Just a moment...")
CLOUDFLARE_TITLE = "just a moment"

# Native apply runs in an iframe / SmartApply screen
APPLY_FRAME = (By.CSS_SELECTOR, "iframe[title*='Apply'], iframe#indeedapply-modal-iframe")
SUCCESS_TEXTS = [
    "application submitted", "your application has been submitted",
    "successfully applied", "thanks for applying", "applied",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("websites/indeed/apply_jobs.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


class IndeedApplyBot:

    def __init__(self, search_url, headless=False,
                 progress_path="websites/indeed/indeed_progress.json",
                 profile_dir="brave-data"):
        self.search_url = search_url
        self.progress_path = progress_path
        self.progress = set(self._load_json(progress_path, default=[]))
        os.makedirs("screenshots", exist_ok=True)
        self.driver = make_driver(headless=headless, profile_dir=profile_dir, log=log)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self):
        applied = skipped = failed = external = 0
        start = 0  # Indeed paginates via `start` (0, 10, 20, ...)
        closed = False

        # One-time gate: load page 1, let the user solve Cloudflare + log in,
        # then press Enter so collection runs against the logged-in DOM.
        try:
            self.driver.get(self._page_url(self.search_url, 0))
            sleep(2)
            if self._cloudflare_blocked():
                self._wait_cloudflare()
        except NoSuchWindowException:
            log.info("Browser window closed — stopping cleanly.")
            self.go_exit()
            return
        self._pause_for_login()

        while not closed:
            page_url = self._page_url(self.search_url, start)
            log.info("=== PAGE %d ===  %s", start // 10 + 1, page_url[:70])

            try:
                self.driver.get(page_url)
                sleep(2)
                if self._cloudflare_blocked():
                    self._wait_cloudflare()
                if not self._wait(SEARCH_CARD, timeout=20):
                    log.info("No cards on page %d — done.", start // 10 + 1)
                    break
                jobs = self._collect_jobs()
            except NoSuchWindowException:
                log.info("Browser window closed — stopping cleanly.")
                break
            if not jobs:
                log.info("Empty page — done.")
                break
            log.info("Page %d: %d jobs", start // 10 + 1, len(jobs))

            for job_id, href, title in jobs:
                if job_id in self.progress:
                    skipped += 1
                    continue

                log.info("[%s] %s", job_id, title[:50])
                result = self._apply_one(href, job_id)
                if result == "closed":
                    log.warning("Browser window closed — stopping. "
                                "Job NOT marked applied; will retry next run.")
                    closed = True
                    break
                if result == "applied":
                    self.progress.add(job_id)
                    self._save_progress()
                    applied += 1
                    log.info("[%s] applied ✓", job_id)
                elif result == "external":
                    self.progress.add(job_id)  # mark so we don't retry
                    self._save_progress()
                    external += 1
                    log.info("[%s] external site — skipped", job_id)
                else:
                    failed += 1
                    log.warning("[%s] failed", job_id)
                sleep(0.5)

            start += 10

        log.info("Done. applied=%d skipped=%d external=%d failed=%d",
                 applied, skipped, external, failed)
        self.go_exit()

    # ------------------------------------------------------------------
    # Per-job apply
    # ------------------------------------------------------------------

    def _apply_one(self, href, job_id):
        try:
            # Card href is a /rc/clk tracking redirect — use the canonical
            # viewjob URL so the apply button renders in the detail pane.
            self.driver.get(f"https://in.indeed.com/viewjob?jk={job_id}")
            sleep(2)

            if self._cloudflare_blocked():
                self._wait_cloudflare()

            # External-apply jobs show "Apply on company site", no Indeed Apply
            if (self.driver.find_elements(*COMPANY_SITE_BTN)
                    and not self.driver.find_elements(*APPLY_BTN)):
                return "external"

            apply_btn = self._fast_wait(APPLY_BTN, timeout=6)
            if apply_btn is None:
                if self.driver.find_elements(*COMPANY_SITE_BTN):
                    return "external"
                log.warning("[%s] no Apply button", job_id)
                return "failed"

            self.driver.execute_script("arguments[0].click();", apply_btn)
            sleep(2)

            return self._wait_for_human(job_id)

        except NoSuchWindowException:
            log.warning("[%s] browser window closed — NOT marking applied", job_id)
            return "closed"
        except Exception as e:
            log.error("[%s] error: %s", job_id, e)
            return "failed"

    def _wait_for_human(self, job_id):
        """Apply flow open — user fills form + submits. Bot watches only."""
        log.info("[%s] apply flow open — fill form & submit in browser", job_id)
        print(f"\n{'='*55}", flush=True)
        print("📋 Indeed apply open — answer questions & submit in browser", flush=True)
        print(f"{'='*55}", flush=True)

        handles_at_start = len(self.driver.window_handles)

        for _ in range(600):   # 10 min max
            sleep(1)
            try:
                if self._success_visible():
                    log.info("[%s] application submitted ✓", job_id)
                    return "applied"
                # New tab opened to an external ATS — treat as external
                if len(self.driver.window_handles) > handles_at_start:
                    log.info("[%s] new tab opened — external apply", job_id)
                    self._close_extra_tabs(handles_at_start)
                    return "external"
            except NoSuchWindowException:
                log.warning("[%s] browser window closed — NOT marking applied", job_id)
                return "closed"
            except Exception:
                continue

        log.warning("[%s] 10-min timeout — skipping", job_id)
        return "failed"

    # ------------------------------------------------------------------
    # Collect jobs from current search page
    # ------------------------------------------------------------------

    def _collect_jobs(self):
        jobs = []
        cards = self.driver.find_elements(*SEARCH_CARD)
        for card in cards:
            job_id = card.get_attribute("data-jk")
            href = card.get_attribute("href")
            title = card.text
            if job_id and href:
                jobs.append((job_id, href, title))
        return jobs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _page_url(self, base_url, start):
        """Indeed paginates via the `start` query param (0, 10, 20, ...)."""
        parsed = urlparse(base_url)
        q = parse_qs(parsed.query)
        if start <= 0:
            q.pop("start", None)
        else:
            q["start"] = [str(start)]
        new_q = urlencode({k: v[0] for k, v in q.items()})
        return urlunparse(parsed._replace(query=new_q))

    def _wait(self, locator, timeout=15):
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(locator)
            )
            return True
        except TimeoutException:
            return False

    def _fast_wait(self, locator, timeout=4):
        try:
            return WebDriverWait(self.driver, timeout, poll_frequency=0.1).until(
                EC.visibility_of_element_located(locator)
            )
        except TimeoutException:
            return None

    def _success_visible(self):
        try:
            body = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            return any(s in body for s in SUCCESS_TEXTS)
        except Exception:
            return False

    def _pause_for_login(self):
        """Wait for the user to log in (and clear any wall) before collecting."""
        print(f"\n{'='*55}", flush=True)
        print("🔑 Log in to Indeed in the browser if needed.", flush=True)
        print("   When the job list is visible, press Enter here to start.", flush=True)
        print(f"{'='*55}", flush=True)
        try:
            input()
        except EOFError:
            # No interactive stdin — fall back to a fixed wait
            log.info("No stdin — waiting 60s for manual login.")
            sleep(60)

    def _cloudflare_blocked(self):
        """Indeed sometimes serves a Cloudflare 'Just a moment...' challenge."""
        try:
            return CLOUDFLARE_TITLE in (self.driver.title or "").lower()
        except Exception:
            return False

    def _wait_cloudflare(self):
        """Pause for the user to solve the Cloudflare challenge in the browser."""
        log.warning("Cloudflare challenge detected — solve it in the browser window.")
        print(f"\n{'='*55}", flush=True)
        print("🛑 Cloudflare check — solve it in the browser, then wait", flush=True)
        print(f"{'='*55}", flush=True)
        for _ in range(180):   # up to 3 min
            sleep(1)
            if not self._cloudflare_blocked():
                log.info("Cloudflare cleared — continuing.")
                return
        log.warning("Cloudflare still blocking after 3 min.")

    def _close_extra_tabs(self, keep_count):
        try:
            while len(self.driver.window_handles) > keep_count:
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
        except Exception:
            pass

    def _save_progress(self):
        try:
            with open(self.progress_path, "w") as f:
                json.dump(sorted(self.progress), f, indent=2)
        except Exception as e:
            log.error("save progress: %s", e)

    @staticmethod
    def _load_json(fp, default):
        try:
            with open(fp) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def go_exit(self):
        try:
            self.driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python3 websites/indeed/apply_jobs.py "<search-url>" [--headless]')
        sys.exit(1)
    url = args[0]
    headless = "--headless" in sys.argv
    bot = IndeedApplyBot(url, headless=headless)
    bot.run()
