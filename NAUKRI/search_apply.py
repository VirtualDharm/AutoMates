"""
Naukri SEARCH-results auto-apply.

Give it a Naukri job-search URL. It walks every result card across all pages,
opens each job, clicks Apply, and waits while YOU fill the recruiter chat
questions and click Save (bot never clicks Save). Then moves to the next job.

  python3 NAUKRI/search_apply.py "<search-url>"
  python3 NAUKRI/search_apply.py "<search-url>" --headless

Skips:
  - jobs already applied (NAUKRI/search_progress.json)
  - "Apply on company site" jobs (external redirect — can't automate)
"""

import json
import logging
import os
import re
import string
import sys
from time import sleep
from os import path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    NoSuchWindowException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ---------------------------------------------------------------------------
# Selectors — update when Naukri changes its DOM
# ---------------------------------------------------------------------------
SEARCH_CARD = (By.CSS_SELECTOR, ".srp-jobtuple-wrapper")
CARD_TITLE_LINK = (By.CSS_SELECTOR, "a.title")

APPLY_BTN = (By.ID, "apply-button")
COMPANY_SITE_BTN = (By.CSS_SELECTOR, "#company-site-button, [class*='company-site']")

CHAT_PANEL = (By.CSS_SELECTOR, ".chatbot_Drawer")
CHAT_CLOSE = (By.CSS_SELECTOR, ".crossIcon.chatBot")
CHAT_QUESTIONS = (By.CSS_SELECTOR, ".botItem.chatbot_ListItem .botMsg span")

SUCCESS_TEXTS = [
    "successfully applied", "application submitted",
    "applied successfully", "thank you for applying",
]

home_directory = path.expanduser("~")
local_bin_directory = home_directory + "/bin/"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("NAUKRI/search_apply.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


class NaukriSearchBot:

    def __init__(self, search_url, headless=False,
                 progress_path="NAUKRI/search_progress.json",
                 profile_dir="chrome-data"):
        self.search_url = search_url
        self.progress_path = progress_path
        self.progress = set(self._load_json(progress_path, default=[]))
        os.makedirs("screenshots", exist_ok=True)

        opts = Options()
        opts.add_argument(f"--user-data-dir={local_bin_directory}{profile_dir}")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        if headless:
            opts.add_argument("--headless=new")
            opts.add_argument("--window-size=1920,1080")

        self.driver = webdriver.Chrome(service=Service(), options=opts)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self):
        applied = skipped = failed = external = 0
        page = 1
        closed = False

        while not closed:
            page_url = self._page_url(self.search_url, page)
            log.info("=== PAGE %d ===  %s", page, page_url[:70])

            try:
                self.driver.get(page_url)

                if not self._wait(SEARCH_CARD, timeout=20):
                    log.info("No cards on page %d — done.", page)
                    break

                # Collect (job_id, href) for every card on this page
                jobs = self._collect_jobs()
            except NoSuchWindowException:
                log.info("Browser window closed — stopping cleanly.")
                break
            if not jobs:
                log.info("Empty page %d — done.", page)
                break
            log.info("Page %d: %d jobs", page, len(jobs))

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

            page += 1

        log.info("Done. applied=%d skipped=%d external=%d failed=%d",
                 applied, skipped, external, failed)
        self.go_exit()

    # ------------------------------------------------------------------
    # Per-job apply
    # ------------------------------------------------------------------

    def _apply_one(self, href, job_id):
        try:
            self.driver.get(href)
            sleep(1.5)

            # External-apply jobs have a company-site button, no chatbot
            if self.driver.find_elements(*COMPANY_SITE_BTN):
                return "external"

            apply_btn = self._fast_wait(APPLY_BTN, timeout=6)
            if apply_btn is None:
                # maybe already applied or company-site only
                if self.driver.find_elements(*COMPANY_SITE_BTN):
                    return "external"
                log.warning("[%s] no Apply button", job_id)
                return "failed"

            detail_url = self.driver.current_url
            self.driver.execute_script("arguments[0].click();", apply_btn)
            sleep(2)

            # Case 1: navigated to saveApply (no questions)
            if self.driver.current_url != detail_url and "saveApply" in self.driver.current_url:
                return "applied"

            # Case 2: chatbot opened
            chat = self._fast_wait(CHAT_PANEL, timeout=4)
            if chat is None:
                # no chat, URL unchanged → maybe instant apply or external
                if "saveApply" in self.driver.current_url:
                    return "applied"
                return "applied"  # assume on-platform apply registered

            return self._answer_chat(job_id, detail_url)

        except NoSuchWindowException:
            log.warning("[%s] browser window closed — NOT marking applied", job_id)
            return "closed"
        except Exception as e:
            log.error("[%s] error: %s", job_id, e)
            return "failed"

    def _answer_chat(self, job_id, detail_url):
        """Chat open — user fills questions + clicks Save. Bot watches only."""
        log.info("[%s] chat open — fill questions & click Save in browser", job_id)
        print(f"\n{'='*55}", flush=True)
        print("📋 Job chat open — answer questions & click Save in browser", flush=True)
        print(f"{'='*55}", flush=True)

        for _ in range(600):   # 10 min max
            sleep(1)

            try:
                url = self.driver.current_url
            except Exception:
                log.warning("[%s] browser window closed — NOT marking applied", job_id)
                return "closed"

            if "saveApply" in url:
                log.info("[%s] saveApply URL — applied ✓", job_id)
                return "applied"

            try:
                self.driver.find_element(*CHAT_PANEL)
            except Exception:
                log.info("[%s] chat gone — applied ✓", job_id)
                return "applied"

            if self._chat_success():
                self._close_chat()
                return "applied"

        log.warning("[%s] 10-min timeout — skipping", job_id)
        self._close_chat()
        return "failed"

    # ------------------------------------------------------------------
    # Collect jobs from current search page
    # ------------------------------------------------------------------

    def _collect_jobs(self):
        jobs = []
        cards = self.driver.find_elements(*SEARCH_CARD)
        for card in cards:
            job_id = card.get_attribute("data-job-id")
            try:
                link = card.find_element(*CARD_TITLE_LINK)
                href = link.get_attribute("href")
                title = link.text
            except NoSuchElementException:
                continue
            if job_id and href:
                jobs.append((job_id, href, title))
        return jobs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _page_url(self, base_url, page):
        """Page N lives in the PATH as `<slug>-N` (page 1 = no suffix).
        All filter query params (ctcFilter, wfhType, jobAge, ...) are kept as-is."""
        if page <= 1:
            return base_url
        parsed = urlparse(base_url)
        # strip any existing -<n> suffix on the last path segment, then append -page
        path = re.sub(r"-\d+$", "", parsed.path)
        return urlunparse(parsed._replace(path=f"{path}-{page}"))

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

    def _chat_success(self):
        try:
            els = self.driver.find_elements(*CHAT_QUESTIONS)
            txt = " ".join(e.text.lower() for e in els)
            return any(s in txt for s in SUCCESS_TEXTS)
        except Exception:
            return False

    def _close_chat(self):
        try:
            self.driver.find_element(*CHAT_CLOSE).click()
            sleep(0.5)
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
    try:
        from NAUKRI import accounts
    except ImportError:
        import accounts

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print('Usage: python3 NAUKRI/search_apply.py "<search-url>" [--headless] [--ashok|--dharmendra]')
        sys.exit(1)
    url = args[0]
    headless = "--headless" in sys.argv
    name, cfg = accounts.resolve(sys.argv)
    log.info("Account: %s (%s)", name, cfg["email"])
    bot = NaukriSearchBot(
        url,
        headless=headless,
        progress_path=cfg["search_progress"],
        profile_dir=cfg["profile"],
    )
    bot.run()
