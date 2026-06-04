"""
Account registry for the Naukri bots (nvites.py, search_apply.py).

Each account uses its OWN Chrome profile (separate login session, persisted under
~/bin/<profile>) and its OWN progress/answers files, so two accounts never mix
applied jobs or screening answers.

Pass --ashok or --dharmendra on the command line to pick an account.
Default = dharmendra (keeps the original filenames/profile, zero migration).

No passwords here — login is manual (open the profile once, log in, session persists).
"""

ACCOUNTS = {
    "dharmendra": {
        "profile": "chrome-data",
        "email": "mdharm4air.fm@gmail.com",
        "nvites_progress": "NAUKRI/progress.json",
        "search_progress": "NAUKRI/search_progress.json",
        "answers": "NAUKRI/answers.json",
    },
    "ashok": {
        "profile": "chrome-data-ashok",
        "email": "ashoksingh.devx@gmail.com",
        "nvites_progress": "NAUKRI/progress_ashok.json",
        "search_progress": "NAUKRI/search_progress_ashok.json",
        "answers": "NAUKRI/answers_ashok.json",
    },
}

DEFAULT = "dharmendra"


def resolve(argv):
    """Return (name, config) from --ashok/--dharmendra flags; default dharmendra."""
    for name in ACCOUNTS:
        if f"--{name}" in argv:
            return name, ACCOUNTS[name]
    return DEFAULT, ACCOUNTS[DEFAULT]
