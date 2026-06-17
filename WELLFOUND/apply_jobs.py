"""
WellFound (AngelList) SEARCH-results auto-apply.

Give it a WellFound jobs URL. It walks the result cards, opens each job, clicks
Apply, then WAITS while YOU write the message and click the final Submit. Bot
never submits.

  python3 WELLFOUND/apply_jobs.py "<wellfound-jobs-url>"
  python3 WELLFOUND/apply_jobs.py "<wellfound-jobs-url>" --headless

Modeled on NAUKRI/search_apply.py (NaukriSearchBot): logging, progress-file
dedup + resume, anti-detection options, screenshots dir, external-job skip.

Skips:
  - jobs already applied (WELLFOUND/wellfound_progress.json)
  - external-redirect jobs ("Apply on company site" / new tab)

Note: WellFound is a single scrolling list (no classic pagination); the bot
scrolls to lazy-load more cards until no new ones appear.
"""

import json
import logging
import os
import sys
from time import sleep
from os import path

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
    _sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))
    from webdriver_factory import make_driver

# ---------------------------------------------------------------------------
# Selectors — update here when WellFound changes its DOM
# ---------------------------------------------------------------------------
# Job cards in the scrolling results list (carry a stable test id)
SEARCH_CARD = (By.CSS_SELECTOR,
               "div[data-test='JobSearchResult'], div.styles_component__Ow6di a[href*='/jobs/']")
# Apply button inside a card / on the job detail
APPLY_BTN = (By.XPATH,
             "//button[contains(., 'Apply') and not(contains(., 'Applied'))]")
# External "apply on company site" link
COMPANY_SITE_BTN = (By.XPATH,
                    "//a[contains(translate(., 'APPLY', 'apply'), 'apply') "
                    "and (contains(@href, 'http') and not(contains(@href, 'wellfound')))]")
# The apply modal (message box + submit)
APPLY_MODAL = (By.CSS_SELECTOR, "div[role='dialog'], div.styles_modal__bsk3F")
SUCCESS_TEXTS = [
    "application sent", "applied", "your application has been sent",
    "message sent", "successfully applied",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("WELLFOUND/apply_jobs.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


class WellFoundApplyBot:

    def __init__(self, search_url, headless=False,
                 progress_path="WELLFOUND/wellfound_progress.json",
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
        closed = False

        try:
            self.driver.get(self.search_url)
            sleep(3)
            if not self._wait(SEARCH_CARD, timeout=20):
                log.info("No job cards found — done.")
                self.go_exit()
                return
            jobs = self._collect_jobs_scrolling()
        except NoSuchWindowException:
            log.info("Browser window closed — stopping cleanly.")
            self.go_exit()
            return

        log.info("Collected %d jobs", len(jobs))

        for job_id, href, title in jobs:
            if closed:
                break
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

        log.info("Done. applied=%d skipped=%d external=%d failed=%d",
                 applied, skipped, external, failed)
        self.go_exit()

    # ------------------------------------------------------------------
    # Per-job apply
    # ------------------------------------------------------------------

    def _apply_one(self, href, job_id):
        try:
            self.driver.get(href)
            sleep(2)

            apply_btn = self._fast_wait(APPLY_BTN, timeout=6)
            if apply_btn is None:
                if self.driver.find_elements(*COMPANY_SITE_BTN):
                    return "external"
                log.warning("[%s] no Apply button", job_id)
                return "failed"

            handles_at_start = len(self.driver.window_handles)
            self.driver.execute_script("arguments[0].click();", apply_btn)
            sleep(2)

            # External apply opens a new tab
            if len(self.driver.window_handles) > handles_at_start:
                log.info("[%s] new tab opened — external apply", job_id)
                self._close_extra_tabs(handles_at_start)
                return "external"

            modal = self._fast_wait(APPLY_MODAL, timeout=5)
            if modal is None:
                if self._success_visible():
                    return "applied"
                return "external"

            return self._wait_for_human(job_id)

        except NoSuchWindowException:
            log.warning("[%s] browser window closed — NOT marking applied", job_id)
            return "closed"
        except Exception as e:
            log.error("[%s] error: %s", job_id, e)
            return "failed"

    def _wait_for_human(self, job_id):
        """Modal open — user writes message + clicks Submit. Bot watches only."""
        log.info("[%s] apply modal open — write message & submit in browser", job_id)
        print(f"\n{'='*55}", flush=True)
        print("📋 WellFound apply open — write message & submit in browser", flush=True)
        print(f"{'='*55}", flush=True)

        for _ in range(600):   # 10 min max
            sleep(1)
            try:
                if self._success_visible():
                    log.info("[%s] application sent ✓", job_id)
                    return "applied"
                if not self.driver.find_elements(*APPLY_MODAL):
                    log.info("[%s] modal closed — applied ✓", job_id)
                    return "applied"
            except NoSuchWindowException:
                log.warning("[%s] browser window closed — NOT marking applied", job_id)
                return "closed"
            except Exception:
                continue

        log.warning("[%s] 10-min timeout — skipping", job_id)
        return "failed"

    # ------------------------------------------------------------------
    # Collect jobs by scrolling the lazy-loaded list
    # ------------------------------------------------------------------

    def _collect_jobs_scrolling(self):
        seen = {}
        stale_rounds = 0
        while stale_rounds < 3:
            before = len(seen)
            for card in self.driver.find_elements(*SEARCH_CARD):
                try:
                    link = card if card.tag_name == "a" else card.find_element(
                        By.CSS_SELECTOR, "a[href*='/jobs/']")
                    href = link.get_attribute("href")
                    title = link.text
                except NoSuchElementException:
                    continue
                if not href:
                    continue
                job_id = href.rstrip("/").split("/")[-1].split("-")[0]
                if job_id and job_id not in seen:
                    seen[job_id] = (job_id, href.split("?")[0], title)
            # scroll to load more
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            sleep(2)
            stale_rounds = stale_rounds + 1 if len(seen) == before else 0
        return list(seen.values())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        print('Usage: python3 WELLFOUND/apply_jobs.py "<jobs-url>" [--headless]')
        sys.exit(1)
    url = args[0]
    headless = "--headless" in sys.argv
    bot = WellFoundApplyBot(url, headless=headless)
    bot.run()
