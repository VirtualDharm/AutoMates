import setup
from time import sleep
from BUMBLE import swipe
from LINKEDIN import recommended_page
from LINKEDIN import search_jobs
from LINKEDIN import connect_people
from LINKEDIN import profile_stalker
from NAUKRI import recommended_jobs as n_rec
from NAUKRI import nvites as n_nvites
from NAUKRI import accounts as n_accounts
from LINKEDIN import connect_recruiter
from LINKEDIN import apply_jobs as li_apply
from WELLFOUND import apply_jobs as wf_apply
from websites.indeed import apply_jobs as in_apply


def pick_account():
	print("Which Naukri account? (1=Dharmendra, 2=Ashok)")
	choice = int(input())
	name = "ashok" if choice == 2 else "dharmendra"
	return name, n_accounts.ACCOUNTS[name]

# first time setup
# creating a UI in python using pyqt6
# https://www.youtube.com/watch?v=Vde5SH8e1OQ&list=PLzMcBGfZo4-lB8MZfHPLTEHO9zJDDLpYj

print("1. Let me login to the website.\n2. I am already logged in previously using this script.")
FIRST_SETUP = int(input())
if FIRST_SETUP == 1:
	# letting the user to login
	_, login_cfg = pick_account()
	print("Chrome window will open to let you login to the accounts. \nYou have two minutes to login to your account :)")
	sleep(10)
	setup.loginWindow(profile_dir=login_cfg["profile"])
else:
	print("skipping setup...")

# ask the Service to automate

print("""
Which service to automate ?
1. WellFound
2. Linkedin
3. Naukri
4. Bumble
5. Indeed
0. Exit
	""")
SERVICE = int(input())

# ask input if required
if SERVICE == 1:
	print("WellFound Selected")
	print("Apply to jobs from a WellFound jobs URL.")
	url = input("\nEnter WellFound jobs URL\n")
	print("Run headless? (1=Yes, 2=No)")
	headless = int(input()) == 1
	wf = wf_apply.WellFoundApplyBot(url, headless=headless)
	wf.run()
elif SERVICE == 2:
	print("Linkedin Selected")
	print("""
Choose below ?
1. Apply Linkedin Recommended Jobs.
2. Search based on Position and Location.
3. Connect to Company People (paypal, nvidia, tesla, etc.)
4. Stalk Profiles (opens a link from profile you give, then keeps on.)
5. Connect to recruiter who is hiring certain position.
6. Apply to jobs from a search URL (Easy Apply, resumable).
	""")
	LINKEDIN_SERVICE = int(input())
	if LINKEDIN_SERVICE == 1:
		print("Applying Linkedin Recommended Page..")
		rp = recommended_page.LinkedinBot()
		rp.click_easy_jobs()
	elif LINKEDIN_SERVICE == 2:
		print("Searching on Linkedin.")
		position = input("\nEnter post name\n")
		location = input("\nEnter job location\n")
		l = search_jobs.LinkedinBot()
		l.do_search(position, location)
		l.click_easy_jobs()
	elif LINKEDIN_SERVICE == 3:
		print("Connecting People on Linkedin.")
		co_name = input("\n Enter the company name in lowercase.\n")
		cp = connect_people.LinkedinBot(co_name)
		cp.connect()
	elif LINKEDIN_SERVICE == 4:
		print("Stalking People on Linkedin.")
		start_profile = input("\n Enter the Profile to start from.\n")
		ps = profile_stalker.LinkedinBot(start_profile)
		ps.stalk_on()
	elif LINKEDIN_SERVICE == 5:
		job_profile = input("\n enter position eg: 'python hiring'\n")
		print('Make sure to write about you in constants.py under variable LINKEDIN_CANDIDATE_INFO under 300 words')
		rp = connect_recruiter.LinkedinBot(job_profile=job_profile)
		rp.apply_job()
	elif LINKEDIN_SERVICE == 6:
		print("Applying to LinkedIn jobs from search URL.")
		url = input("\nEnter LinkedIn jobs search URL\n")
		print("Run headless? (1=Yes, 2=No)")
		headless = int(input()) == 1
		la = li_apply.LinkedInApplyBot(url, headless=headless)
		la.run()
elif SERVICE == 3:
	print("Naukri Selected")
	print("""
Choose below ?
1. Apply Naukri Recommended Jobs.
2. Apply NVites from Inbox (supports resume, screening questions).
	""")
	NAUKRI_SERVICE = int(input())
	if NAUKRI_SERVICE == 1:
		print("Applying Naukri Recommended Jobs.")
		n = n_rec.NaukriBot()
		n.click_job()
	elif NAUKRI_SERVICE == 2:
		print("NVites selected.")
		acct_name, acct_cfg = pick_account()
		print("Run headless? (1=Yes, 2=No)")
		HEADLESS = int(input()) == 1
		print(f"Edit {acct_cfg['answers']} before running to set your CTC, notice period, etc.")
		nv = n_nvites.NaukriNVitesBot(
			headless=HEADLESS,
			answers_path=acct_cfg["answers"],
			progress_path=acct_cfg["nvites_progress"],
			profile_dir=acct_cfg["profile"],
		)
		nv.run()
elif SERVICE == 4:
	print("Bumble Selected")
	print("Swiping right on all profiles :P")
	b = swipe.BumbleBot()
	b.right_swipe()
elif SERVICE == 5:
	print("Indeed Selected")
	print("Apply to jobs from an Indeed search URL.")
	url = input("\nEnter Indeed search URL\n")
	print("Run headless? (1=Yes, 2=No)")
	headless = int(input()) == 1
	ia = in_apply.IndeedApplyBot(url, headless=headless)
	ia.run()
else:
	exit()



# if error, ask for going back to previous menu.

