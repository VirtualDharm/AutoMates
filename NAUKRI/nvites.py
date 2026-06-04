"""
Naukri NVites (inbox invitations) automation.

Flow:
  - Opens https://www.naukri.com/mnjuser/inbox (two-panel layout)
  - Clicks each NVite card in the left panel
  - Clicks Apply in the right panel
  - Answers recruiter screening questions via the chat-style panel
  - Scrolls to load all 130+ NVites
  - Saves progress so runs can be safely interrupted and resumed

Usage:
    python -m NAUKRI.nvites              # headful
    python -m NAUKRI.nvites --headless   # headless
    # or via run.py menu → Naukri → NVites
"""

import json
import logging
import os
import re
import string
from datetime import datetime
from os import path
from time import sleep

from selenium import webdriver
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ---------------------------------------------------------------------------
# Selectors — update here when Naukri changes its DOM
# ---------------------------------------------------------------------------
INBOX_URL = "https://www.naukri.com/mnjuser/inbox"

# Left panel: each NVite card (has `id` attr = job hash for deduplication)
NVITE_CARD = (By.CSS_SELECTOR, ".inbox-company-card")

# Right panel: Apply / Not-interested buttons
APPLY_BTN = (By.CSS_SELECTOR, ".apply-btn")

# Chat panel (appears after clicking Apply)
CHAT_PANEL = (By.CSS_SELECTOR, ".chatbot_Drawer")
CHAT_CLOSE  = (By.CSS_SELECTOR, ".crossIcon.chatBot")

# Questions shown in chat (all botItem spans; last = current question)
CHAT_QUESTIONS = (By.CSS_SELECTOR, ".botItem.chatbot_ListItem .botMsg span")

# Contenteditable text input inside chat
CHAT_INPUT = (By.CSS_SELECTOR, "div.textArea[contenteditable='true']")

# Send/Save button
CHAT_SEND = (By.CSS_SELECTOR, "div.sendMsg")

# Success indicators (inside chat or page)
SUCCESS_TEXTS = [
    "successfully applied",
    "application submitted",
    "applied successfully",
    "thank you for applying",
    "your application",
]

# ---------------------------------------------------------------------------
# Keyword map: answers.json key → label substrings that trigger it
# ---------------------------------------------------------------------------
KEYWORD_MAP = {
    "current_ctc":         ["current ctc", "current salary", "current package",
                             "present ctc", "current compensation", "current remuneration"],
    "expected_ctc":        ["expected ctc", "expected salary", "expected package",
                             "desired ctc", "expected compensation"],
    "notice_period":       ["notice period", "notice", "joining time",
                             "days to join", "available in", "serving notice"],
    "total_experience":    ["total years of experience", "total experience",
                             "overall experience", "total exp",
                             "years of total", "years of work"],
    "java_experience":     ["java"],
    "python_experience":   ["python"],
    "react_experience":    ["react", "reactjs", "react.js"],
    "node_experience":     ["node", "nodejs", "node.js"],
    "current_location":    ["current location", "present location", "current city",
                             "where are you based", "your location"],
    "willing_to_relocate": ["relocate", "relocation", "willing to relocate",
                             "open to relocate"],
    "availability":        ["availability", "available", "immediate joiner",
                             "when can you join", "earliest you can join"],
}

home_directory = path.expanduser("~")
local_bin_directory = home_directory + "/bin/"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("NAUKRI/nvites.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


class NaukriNVitesBot:

    def __init__(self, headless=False,
                 answers_path="NAUKRI/answers.json",
                 progress_path="NAUKRI/progress.json",
                 profile_dir="chrome-data"):
        self.progress_path = progress_path
        self.answers_path = answers_path
        self.answers = self._load_json(answers_path, default={})
        self.progress = self._load_progress()
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
        self.wait = WebDriverWait(self.driver, 15)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self):
        log.info("Opening Naukri NVites inbox: %s", INBOX_URL)
        self.driver.get(INBOX_URL)
        sleep(3)

        applied = skipped = failed = 0
        seen_ids: set = set()
        scroll_depth = 0  # how many times we've scrolled to reach the frontier

        while True:
            self._ensure_on_inbox()

            # Restore scroll depth quickly after navigating back
            if scroll_depth > 0:
                self._restore_scroll(scroll_depth)

            cards = self.driver.find_elements(*NVITE_CARD)
            if not cards:
                log.info("No NVite cards visible. Done.")
                break

            found_work = False
            for card in cards:
                job_id = self._job_id(card)
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                if job_id in self.progress:
                    skipped += 1
                    continue

                log.info("[%s] processing...", job_id)
                ok = self._process_card(card, job_id)
                if ok:
                    self._save_progress(job_id)
                    applied += 1
                    log.info("[%s] applied ✓", job_id)
                else:
                    failed += 1
                    log.warning("[%s] failed", job_id)

                found_work = True
                break

            if found_work:
                continue

            # All visible cards known → scroll once deeper
            if self._scroll_load_more(len(seen_ids)):
                scroll_depth += 1
                continue

            break

        log.info("Done. applied=%d skipped=%d failed=%d", applied, skipped, failed)
        self.go_exit()

    # ------------------------------------------------------------------
    # Per-card flow
    # ------------------------------------------------------------------

    def _process_card(self, card, job_id):
        try:
            # Click card — no sleep, Apply button wait fires the moment it's ready
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
            self.driver.execute_script("arguments[0].click();", card)

            # Wait for Apply button (poll every 100 ms — reacts instantly when ready)
            apply_btn = self._fast_wait(APPLY_BTN, timeout=4)
            if apply_btn is None:
                log.warning("[%s] no Apply button", job_id)
                return False
            self.driver.execute_script("arguments[0].click();", apply_btn)

            # Minimal yield — let browser start navigation/chat render
            sleep(0.3)

            # Case 1: instant apply (navigated away, no chat)
            if not self._on_inbox():
                return True

            # Case 2: chat opened — detect the moment it appears
            chat = self._fast_wait(CHAT_PANEL, timeout=3)
            if chat is None:
                return self._check_applied_state()

            return self._answer_chat(job_id)

        except StaleElementReferenceException:
            log.warning("[%s] stale element", job_id)
            return False
        except NoSuchWindowException:
            log.info("[%s] window closed mid-apply — treating as applied", job_id)
            return True
        except Exception as e:
            log.error("[%s] error: %s", job_id, e)
            return False

    def _answer_chat(self, job_id):
        """
        Chat is open — user handles all questions and clicks Save.
        Bot just watches the URL for saveApply (success) or chat closure.
        Times out after 10 minutes.
        """
        log.info("[%s] chat open — fill questions and click Save in browser", job_id)
        print(f"\n{'='*55}", flush=True)
        print(f"📋 Job chat open — answer questions & click Save in browser", flush=True)
        print(f"{'='*55}", flush=True)

        for _ in range(600):   # 10 min max
            sleep(1)

            try:
                url = self.driver.current_url
            except Exception:
                log.info("[%s] window closed — applied ✓", job_id)
                return True

            if INBOX_URL not in url:
                log.info("[%s] saveApply URL — applied ✓", job_id)
                return True

            try:
                self.driver.find_element(*CHAT_PANEL)
            except (NoSuchElementException, Exception):
                log.info("[%s] chat gone — applied ✓", job_id)
                return True

            if self._chat_success():
                self._close_chat()
                return True

        log.warning("[%s] 10-min timeout — skipping", job_id)
        self._close_chat()
        return False

    def _type_in_chat(self, text):
        for attempt in range(3):
            try:
                inp = self.driver.find_element(*CHAT_INPUT)
                inp.click()
                sleep(0.4)
                # Clear: Cmd+A (Mac select-all) then Delete
                ActionChains(self.driver)\
                    .key_down(Keys.COMMAND).send_keys('a').key_up(Keys.COMMAND)\
                    .send_keys(Keys.DELETE)\
                    .perform()
                sleep(0.2)
                if text:
                    inp.send_keys(text)
                sleep(0.5)
                return True
            except Exception as e:
                log.debug("type_in_chat attempt %d: %s", attempt, e)
                sleep(1)
        return False

    def _chat_success(self):
        try:
            q_els = self.driver.find_elements(*CHAT_QUESTIONS)
            all_text = " ".join(el.text.lower() for el in q_els)
            return any(s in all_text for s in SUCCESS_TEXTS)
        except Exception:
            return False

    def _check_applied_state(self):
        """Fallback: returns True if Apply button is gone or shows 'Applied'."""
        try:
            btn = self.driver.find_element(*APPLY_BTN)
            return "applied" in btn.text.lower()
        except NoSuchElementException:
            return True

    def _close_chat(self):
        try:
            self.driver.find_element(*CHAT_CLOSE).click()
            sleep(1)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Scroll / load-more
    # ------------------------------------------------------------------

    def _restore_scroll(self, depth):
        """Rapidly fire 'depth' scroll events, waiting only until each batch of cards loads."""
        for step in range(depth):
            before = len(self.driver.find_elements(*NVITE_CARD))
            self.driver.execute_script("""
                var el = document.querySelector('section.cards');
                if (el) {
                    el.scrollTop = el.scrollHeight;
                    el.dispatchEvent(new Event('scroll', {bubbles: true}));
                }
                window.scrollBy(0, 99999);
                window.dispatchEvent(new Event('scroll', {bubbles: true}));
            """)
            # Poll every 100 ms until new cards appear (max 3 s per step)
            for _ in range(30):
                sleep(0.1)
                if len(self.driver.find_elements(*NVITE_CARD)) > before:
                    break

    def _scroll_load_more(self, processed_count):
        """Scrolls the card list and fires scroll events to trigger Naukri's infinite scroll."""
        before = len(self.driver.find_elements(*NVITE_CARD))

        # section.cards is the real scrollable container (scrollHeight ~5000px)
        self.driver.execute_script("""
            var el = document.querySelector('section.cards');
            if (el) {
                el.scrollTop = el.scrollHeight;
                el.dispatchEvent(new Event('scroll', {bubbles: true}));
            }
            window.scrollBy(0, 99999);
            window.dispatchEvent(new Event('scroll', {bubbles: true}));
        """)

        # Also scroll last card into view as native trigger
        cards = self.driver.find_elements(*NVITE_CARD)
        if cards:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'end'});", cards[-1]
            )

        sleep(3)
        after = len(self.driver.find_elements(*NVITE_CARD))
        if after > before:
            log.info("Loaded more cards: %d → %d (so far: %d)", before, after, processed_count)
            return True
        log.info("No more cards after %d processed.", processed_count)
        return False

    # ------------------------------------------------------------------
    # Answer matching + interactive fallback
    # ------------------------------------------------------------------

    def _ask_user(self, question):
        """
        Write unknown question to pending_question.json and block until
        Claude (or the user) writes the answer into answers.json and
        deletes the pending file.  Timeout = 10 minutes.
        """
        pending_path = "NAUKRI/pending_question.json"
        payload = {"question": question, "status": "waiting"}
        with open(pending_path, "w") as f:
            json.dump(payload, f, indent=2)

        log.info("⏸  Unknown question — waiting for answer in Claude terminal:")
        log.info("   '%s'", question)
        print("\n" + "=" * 60, flush=True)
        print(f"NEW QUESTION: {question}", flush=True)
        print("Answer it in the Claude terminal → it will update answers.json", flush=True)
        print("=" * 60, flush=True)

        # Poll every 3 s for up to 10 minutes
        for _ in range(200):
            sleep(3)
            if not os.path.exists(pending_path):
                # File deleted = Claude wrote the answer to answers.json
                self.answers = self._load_json(self.answers_path, default={})
                # Try KEYWORD_MAP match first
                answer = self._match_answer(question)
                if answer:
                    log.info("Got answer (keyword match): '%s'", answer)
                    return answer
                # Fallback: match any answers.json key whose words appear in the question
                normalized_q = self._normalize(question)
                for key, val in self.answers.items():
                    key_words = [w for w in key.replace("_", " ").split() if len(w) > 3]
                    if key_words and any(w in normalized_q for w in key_words):
                        log.info("Got answer (key-name match on '%s'): '%s'", key, val)
                        return str(val)
                log.warning("pending_question.json deleted but no match found — skipping")
                return ""

        # Timeout after 10 min — remove stale file, skip question
        log.warning("Timeout waiting for answer to '%s' — skipping", question)
        try:
            os.remove(pending_path)
        except OSError:
            pass
        return ""

    def _save_answers(self):
        try:
            with open(self.answers_path, "w") as f:
                json.dump(self.answers, f, indent=2)
        except Exception as e:
            log.error("Could not save answers: %s", e)

    def _match_answer(self, question_text):
        normalized = self._normalize(question_text)
        for key, keywords in KEYWORD_MAP.items():
            for kw in keywords:
                if kw in normalized:
                    val = self.answers.get(key, "")
                    if val:
                        return str(val)
        return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_inbox(self):
        return INBOX_URL in self.driver.current_url

    def _ensure_on_inbox(self):
        if not self._on_inbox():
            log.info("Navigating back to inbox...")
            self.driver.get(INBOX_URL)
            # Wait for card list to be present — no fixed sleep
            try:
                WebDriverWait(self.driver, 6, poll_frequency=0.1).until(
                    EC.presence_of_element_located(NVITE_CARD)
                )
            except TimeoutException:
                sleep(1)  # fallback only

    def _find_card_by_id(self, job_id):
        try:
            return self.driver.find_element(By.XPATH, f"//*[@id='{job_id}']")
        except NoSuchElementException:
            return None

    def _job_id(self, card):
        # Prefer the id attribute (hash string Naukri assigns)
        val = card.get_attribute("id")
        if val and val.strip():
            return val.strip()
        # Fallback: extract numeric ID from any child href
        try:
            for a in card.find_elements(By.TAG_NAME, "a"):
                href = a.get_attribute("href") or ""
                m = re.search(r"[-/](\d{6,})", href)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return None

    def _find_visible(self, locator, timeout=10):
        try:
            return WebDriverWait(self.driver, timeout).until(
                EC.visibility_of_element_located(locator)
            )
        except TimeoutException:
            return None

    def _fast_wait(self, locator, timeout=4):
        """Like _find_visible but polls every 100 ms — reacts the instant element appears."""
        try:
            return WebDriverWait(self.driver, timeout, poll_frequency=0.1).until(
                EC.visibility_of_element_located(locator)
            )
        except TimeoutException:
            return None

    def _screenshot(self, tag):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = f"screenshots/{ts}_{tag}.png"
        try:
            self.driver.save_screenshot(fp)
            log.info("Screenshot: %s", fp)
        except Exception as e:
            log.warning("Screenshot failed: %s", e)

    def _normalize(self, text):
        t = text.lower().strip().translate(str.maketrans("", "", string.punctuation))
        return " ".join(t.split())

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def _load_progress(self):
        return set(self._load_json(self.progress_path, default=[]))

    def _save_progress(self, job_id):
        self.progress.add(job_id)
        try:
            with open(self.progress_path, "w") as f:
                json.dump(sorted(self.progress), f, indent=2)
        except Exception as e:
            log.error("Could not save progress: %s", e)

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
    import sys
    from NAUKRI import accounts

    headless = "--headless" in sys.argv
    name, cfg = accounts.resolve(sys.argv)
    log.info("Account: %s (%s)", name, cfg["email"])
    bot = NaukriNVitesBot(
        headless=headless,
        answers_path=cfg["answers"],
        progress_path=cfg["nvites_progress"],
        profile_dir=cfg["profile"],
    )
    bot.run()
