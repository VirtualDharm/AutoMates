"""
Shared Selenium driver factory for the apply bots.

Why this exists: job portals (Indeed especially) sit behind Cloudflare, which
hard-flags the "Chrome for Testing" binary that Selenium Manager auto-downloads
and detects the chromedriver/CDP automation itself. The reliable fix is:

  1. Drive a REAL consumer browser (Google Chrome or Brave), not Chrome-for-Testing.
  2. Use undetected-chromedriver, which patches the driver to strip the `cdc_`
     / CDP tells Cloudflare looks for.

If undetected-chromedriver isn't installed, we fall back to a plain Selenium
Chrome driver pointed at the real browser binary (works for non-Cloudflare
sites like Naukri).

Setup (one time):
    pip install undetected-chromedriver setuptools certifi
"""

import os
import re
import subprocess
from os import path

# Real consumer browsers, in preference order. First existing wins.
REAL_BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

home_directory = path.expanduser("~")
local_bin_directory = home_directory + "/bin/"


def _ensure_ca_bundle():
    """macOS framework Python often lacks CA certs, breaking the UC driver
    download with CERTIFICATE_VERIFY_FAILED. Point at certifi's bundle."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    except Exception:
        pass


def _find_browser():
    for b in REAL_BROWSERS:
        if path.exists(b):
            return b
    return None


def _browser_major_version(binary):
    """Return the Chromium major version of the given browser binary, or None."""
    try:
        out = subprocess.check_output([binary, "--version"], text=True, timeout=10)
        m = re.search(r"(\d+)\.\d+\.\d+", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def make_driver(headless=False, profile_dir="brave-data", log=None):
    """Create a stealth Selenium driver. Tries undetected-chromedriver +
    a real browser binary; falls back to plain Selenium Chrome.

    profile_dir lives under ~/bin/ and persists login/cookies across runs.
    Use a browser-specific dir (e.g. 'brave-data') — a profile created by one
    Chromium build will crash a different build.
    """
    _ensure_ca_bundle()
    user_data_dir = f"{local_bin_directory}{profile_dir}"
    binary = _find_browser()

    def _say(msg):
        (log.info if log else print)(msg)

    # ---- Preferred: undetected-chromedriver -------------------------------
    try:
        import undetected_chromedriver as uc

        opts = uc.ChromeOptions()
        opts.add_argument("--disable-blink-features=AutomationControlled")
        if headless:
            opts.add_argument("--headless=new")
            opts.add_argument("--window-size=1920,1080")

        kwargs = {"options": opts, "user_data_dir": user_data_dir}
        if binary:
            kwargs["browser_executable_path"] = binary
            ver = _browser_major_version(binary)
            if ver:
                kwargs["version_main"] = ver
            _say(f"undetected-chromedriver + {binary} (v{ver})")
        else:
            _say("undetected-chromedriver (no real browser found; using default)")
        return uc.Chrome(**kwargs)
    except ImportError:
        _say("undetected-chromedriver not installed — falling back to plain Selenium. "
             "Cloudflare-protected sites (Indeed) may block. "
             "Install with: pip install undetected-chromedriver setuptools certifi")
    except Exception as e:
        _say(f"undetected-chromedriver failed ({e}); falling back to plain Selenium.")

    # ---- Fallback: plain Selenium Chrome ----------------------------------
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.add_argument(f"--user-data-dir={user_data_dir}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    if binary:
        opts.binary_location = binary
        _say(f"plain Selenium + {binary}")
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(), options=opts)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
    except Exception:
        pass
    return driver
