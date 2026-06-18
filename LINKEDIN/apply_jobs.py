"""
LinkedIn SEARCH-results auto-apply (Easy Apply only).

Give it a LinkedIn jobs search URL. It walks every result card across all
pages, opens each job, clicks Easy Apply, then WAITS while YOU fill the
application form/questions and click the final Submit (bot never submits).
Then it moves to the next job.

  python3 LINKEDIN/apply_jobs.py "<linkedin-jobs-search-url>"
  python3 LINKEDIN/apply_jobs.py "<linkedin-jobs-search-url>" --headless

Modeled on NAUKRI/search_apply.py (NaukriSearchBot): logging, progress-file
dedup + resume, anti-detection options, screenshots dir, external-job skip.

Skips:
  - jobs already applied (LINKEDIN/linkedin_progress.json)
  - non-Easy-Apply jobs (the "Apply" button redirects to an external site)
"""

import json
import logging
import os
import re
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
    _sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))
    from webdriver_factory import make_driver

# ---------------------------------------------------------------------------
# Selectors — update here when LinkedIn changes its DOM
# ---------------------------------------------------------------------------
# Left panel: each job card on a search-results page (carries the job id)
SEARCH_CARD = (By.CSS_SELECTOR, "div.job-card-container[data-job-id], li.scaffold-layout__list-item[data-occludable-job-id]")
CARD_TITLE_LINK = (By.CSS_SELECTOR, "a.job-card-list__title, a.job-card-container__link")

# Job detail page: Easy Apply control. LinkedIn's SDUI layout renders it as an
# <a> (not a <button>) with aria-label "Easy Apply to this job" and an href
# ending in /apply/?openSDUIApplyFlow=true. Classic layout uses a <button> with
# aria-label "Easy Apply". Match both; class names are hashed and unusable.
EASY_APPLY_BTN = (By.XPATH,
                  "//a[contains(@aria-label, 'Easy Apply')]"
                  " | //button[contains(@aria-label, 'Easy Apply')]"
                  " | //a[contains(@href, 'openSDUIApplyFlow')]"
                  " | //*[(self::a or self::button)]"
                  "[.//span[contains(normalize-space(.), 'Easy Apply')]]")
# Any apply control at all (used to tell 'external' from 'page not loaded')
ANY_APPLY_BTN = (By.XPATH,
                 "//a[contains(@aria-label, 'Apply')]"
                 " | //button[contains(@aria-label, 'Apply')]"
                 " | //a[contains(@href, '/apply')]"
                 " | //*[(self::a or self::button)]"
                 "[.//span[contains(normalize-space(.), 'Apply')]]")

# The Easy Apply modal that opens after clicking
APPLY_MODAL = (By.CSS_SELECTOR, "div.jobs-easy-apply-modal, div[data-test-modal][role='dialog']")
# Multi-step modal buttons (identified by aria-label; classes are hashed)
NEXT_BTN = (By.XPATH,
            "//button[@aria-label='Continue to next step']"
            " | //button[@data-easy-apply-next-button]"
            " | //button[@aria-label='Review your application']"
            " | //button[@aria-label='Review']")
SUBMIT_BTN = (By.XPATH,
              "//button[@aria-label='Submit application']"
              " | //button[@aria-label='Submit Application']")

SUCCESS_TEXTS = [
    "application sent", "your application was sent",
    "application submitted", "was sent to",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("LINKEDIN/apply_jobs.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


class LinkedInApplyBot:

    def __init__(self, search_url, headless=False,
                 progress_path="LINKEDIN/linkedin_progress.json",
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
        page = 0  # LinkedIn pages step by 25 in the `start` query param
        closed = False

        # One-time gate: load page 1, let the user log in, then press Enter so
        # collection runs against the logged-in DOM.
        try:
            self.driver.get(self._page_url(self.search_url, 0))
            sleep(2)
        except NoSuchWindowException:
            log.info("Browser window closed — stopping cleanly.")
            self.go_exit()
            return
        self._pause_for_login()

        while not closed:
            page_url = self._page_url(self.search_url, page)
            log.info("=== PAGE %d ===  %s", page // 25 + 1, page_url[:70])

            try:
                self.driver.get(page_url)
                sleep(2)
                if not self._wait(SEARCH_CARD, timeout=20):
                    log.info("No cards on page %d — done.", page // 25 + 1)
                    break
                self._scroll_list()
                jobs = self._collect_jobs()
            except NoSuchWindowException:
                log.info("Browser window closed — stopping cleanly.")
                break
            if not jobs:
                log.info("Empty page — done.")
                break
            log.info("Page %d: %d jobs", page // 25 + 1, len(jobs))

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

            page += 25

        log.info("Done. applied=%d skipped=%d external=%d failed=%d",
                 applied, skipped, external, failed)
        self.go_exit()

    # ------------------------------------------------------------------
    # Per-job apply
    # ------------------------------------------------------------------

    def _apply_one(self, href, job_id):
        try:
            self.driver.get(href)
            sleep(3)

            # Wait for the job detail (any apply button) to render first, so we
            # don't mislabel a slow-loading page as external.
            self._wait(ANY_APPLY_BTN, timeout=12)

            apply_btn = None
            for _ in range(3):  # retry: the button can hydrate a beat late
                btns = self.driver.find_elements(*EASY_APPLY_BTN)
                if btns:
                    apply_btn = btns[0]
                    break
                sleep(1.5)

            if apply_btn is None:
                # An apply button exists but it's not Easy Apply → external.
                if self.driver.find_elements(*ANY_APPLY_BTN):
                    log.info("[%s] apply button is external (not Easy Apply)", job_id)
                else:
                    log.warning("[%s] no apply button found at all", job_id)
                return "external"

            self.driver.execute_script("arguments[0].scrollIntoView(true);", apply_btn)
            sleep(0.3)
            self.driver.execute_script("arguments[0].click();", apply_btn)
            sleep(3)

            # Apply flow entered either as a classic modal OR the SDUI flow
            # (URL navigates to /apply/?openSDUIApplyFlow=true).
            in_flow = ("/apply" in self.driver.current_url
                       or "openSDUIApplyFlow" in self.driver.current_url
                       or bool(self.driver.find_elements(*APPLY_MODAL)))
            if not in_flow:
                if self._success_visible():
                    return "applied"
                log.warning("[%s] apply flow did not open", job_id)
                return "failed"

            return self._wait_for_human(job_id)

        except NoSuchWindowException:
            log.warning("[%s] browser window closed — NOT marking applied", job_id)
            return "closed"
        except Exception as e:
            log.error("[%s] error: %s", job_id, e)
            return "failed"

    def _wait_for_human(self, job_id):
        """Drive the multi-step Easy Apply modal: auto-click Next / Review,
        but STOP at the final Submit so the user reviews and submits.

        - Pre-filled steps (contact info, etc.) → bot clicks Next/Review.
        - A step with empty required fields/questions blocks advancement; the
          bot keeps retrying while the user fills them in, then proceeds.
        - When 'Submit application' appears, the bot pauses and waits for the
          user to click Submit (it never submits)."""
        log.info("[%s] Easy Apply modal open — auto-advancing", job_id)
        announced_submit = False
        announced_fill = False

        for _ in range(600):   # ~10 min max
            sleep(1)
            try:
                # Done?
                if self._success_visible():
                    log.info("[%s] application sent ✓", job_id)
                    self._dismiss_modal()
                    return "applied"
                url = self.driver.current_url
                in_flow = ("/apply" in url or "openSDUIApplyFlow" in url
                           or bool(self.driver.find_elements(*APPLY_MODAL)))
                if not in_flow:
                    log.info("[%s] apply flow closed — applied ✓", job_id)
                    return "applied"

                # Final step → hand off to the human, never auto-submit.
                if self.driver.find_elements(*SUBMIT_BTN):
                    if not announced_submit:
                        log.info("[%s] review step — waiting for you to click Submit", job_id)
                        print(f"\n{'='*55}", flush=True)
                        print("📋 Review your application & click Submit in browser", flush=True)
                        print(f"{'='*55}", flush=True)
                        announced_submit = True
                    continue

                # Otherwise auto-advance.
                nexts = [b for b in self.driver.find_elements(*NEXT_BTN)
                         if b.is_displayed() and b.is_enabled()]
                if nexts:
                    self.driver.execute_script("arguments[0].click();", nexts[0])
                    announced_fill = False
                    sleep(1.5)
                else:
                    # No Next/Submit: step needs the user (questions, resume, etc.)
                    if not announced_fill:
                        log.info("[%s] step needs input — fill it; I'll continue", job_id)
                        print(f"\n{'='*55}", flush=True)
                        print("📝 Fill the required fields/questions — bot resumes automatically", flush=True)
                        print(f"{'='*55}", flush=True)
                        announced_fill = True
            except NoSuchWindowException:
                log.warning("[%s] browser window closed — NOT marking applied", job_id)
                return "closed"
            except Exception:
                continue

        log.warning("[%s] 10-min timeout — skipping", job_id)
        self._dismiss_modal()
        return "failed"

    # ------------------------------------------------------------------
    # Collect jobs from current search page
    # ------------------------------------------------------------------

    def _collect_jobs(self):
        jobs = []
        cards = self.driver.find_elements(*SEARCH_CARD)
        for card in cards:
            job_id = (card.get_attribute("data-job-id")
                      or card.get_attribute("data-occludable-job-id"))
            try:
                link = card.find_element(*CARD_TITLE_LINK)
                href = link.get_attribute("href")
                title = link.text
            except NoSuchElementException:
                continue
            if job_id and href:
                jobs.append((job_id, href.split("?")[0], title))
        return jobs

    def _scroll_list(self):
        """LinkedIn lazy-loads cards as the left list scrolls."""
        try:
            cards = self.driver.find_elements(*SEARCH_CARD)
            for c in cards:
                self.driver.execute_script("arguments[0].scrollIntoView(true);", c)
                sleep(0.2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _page_url(self, base_url, start):
        """LinkedIn paginates via the `start` query param (0, 25, 50, ...)."""
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

    def _dismiss_modal(self):
        try:
            self.driver.find_element(
                By.CSS_SELECTOR, "button[aria-label='Dismiss']"
            ).click()
            sleep(0.5)
            # Confirm "Discard" if LinkedIn prompts to save the application
            for b in self.driver.find_elements(By.TAG_NAME, "button"):
                if (b.text or "").strip().lower() == "discard":
                    b.click()
                    break
        except Exception:
            pass

    def _pause_for_login(self):
        """Wait for the user to log in before collecting (no time pressure)."""
        print(f"\n{'='*55}", flush=True)
        print("🔑 Log in to LinkedIn in the browser if needed.", flush=True)
        print("   When the job list is visible, press Enter here to start.", flush=True)
        print(f"{'='*55}", flush=True)
        try:
            input()
        except EOFError:
            log.info("No stdin — waiting 60s for manual login.")
            sleep(60)

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
        print('Usage: python3 LINKEDIN/apply_jobs.py "<search-url>" [--headless]')
        sys.exit(1)
    url = args[0]
    headless = "--headless" in sys.argv
    bot = LinkedInApplyBot(url, headless=headless)
    bot.run()
