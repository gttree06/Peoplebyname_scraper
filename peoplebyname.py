"""
PeopleByName.com Opt-Out Automation Script
-------------------------------------------
This script takes your personal information, searches peoplebyname.com for
matching records, collects the record IDs, and automates the opt-out form
submissions (5 records per submission page).

Requirements:
    pip install selenium webdriver-manager

Usage:
    python peoplebyname_optout.py
"""

import time
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    from webdriver_manager.chrome import ChromeDriverManager
    USE_WEBDRIVER_MANAGER = True
except ImportError:
    USE_WEBDRIVER_MANAGER = False

# ─────────────────────────────────────────────
#  USER CONFIGURATION — Fill in your details
# ─────────────────────────────────────────────
USER_DATA = {
    "first_name": "John",
    "last_name":  "Doe",
    "age":        45,          # Used to help match records; set to None to skip
    "addresses":  [            # List as many past/current addresses as you like
        "123 Main St, Springfield, IL",
        "456 Oak Ave, Chicago, IL",
    ],
    "email": "your@email.com", # Required for the opt-out form
}

# Reason text entered into the "Reason for removal" textarea
REMOVAL_REASON = "I did not consent to my personal information being published and request its removal for privacy reasons."

# How long (seconds) to pause between page loads to avoid rate-limiting
PAGE_DELAY = 2

# Set to True to see the browser window; False to run headless
SHOW_BROWSER = True

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

SEARCH_URL  = "https://www.peoplebyname.com/people/{last}/{first}/"
OPTOUT_URL  = "https://www.peoplebyname.com/opt_out.php"


def build_driver(headless: bool = False) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--window-size=1280,900")

    if USE_WEBDRIVER_MANAGER:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


def normalize(text: str) -> str:
    """Lowercase and strip whitespace for loose comparisons."""
    return re.sub(r"\s+", " ", text.strip().lower())


def score_record(card_text: str, user: dict) -> int:
    """
    Return a simple match score for a record card against the user's data.
    Higher = better match.
    """
    score = 0
    text = normalize(card_text)
    full_name = normalize(f"{user['first_name']} {user['last_name']}")

    if full_name in text:
        score += 10

    if user.get("age"):
        if str(user["age"]) in text:
            score += 5
        # Also check age ±1 for tolerance
        for delta in (-1, 1):
            if str(user["age"] + delta) in text:
                score += 2

    for addr in user.get("addresses", []):
        # Check individual tokens of the address
        for token in normalize(addr).split(","):
            token = token.strip()
            if token and token in text:
                score += 3

    return score


# ─────────────────────────────────────────────
#  STEP 1: Scrape matching record IDs
# ─────────────────────────────────────────────

def find_matching_record_ids(driver: webdriver.Chrome, user: dict) -> list[str]:
    """
    Navigate to the search results page and return record IDs whose cards
    match the user's data above the threshold.

    The page shows cards like:
        Record ID: 425234752
        Harvey Knell
        980 Singing Wood Dr
        Arcadia, CA 91006
        (949) 673-3254
        [View Complete Record]

    We read the "Record ID: XXXXXXXXX" text directly from each card.
    """
    url = SEARCH_URL.format(
        last=user["last_name"].capitalize(),
        first=user["first_name"].capitalize(),
    )
    print(f"\n[1] Searching: {url}")
    driver.get(url)
    time.sleep(PAGE_DELAY)

    matching_ids = []
    seen = set()

    # Grab the full page source and find every card block that contains a Record ID
    # We look for all elements whose text contains "Record ID:"
    # The cards appear to be <div> or <td> containers — try broad selector first
    card_candidates = driver.find_elements(
        By.XPATH,
        "//*[contains(text(), 'Record ID:')]/ancestor::*[self::div or self::td or self::li][1]"
    )

    # Fallback: if the above finds nothing, search all elements containing "Record ID:"
    if not card_candidates:
        card_candidates = driver.find_elements(
            By.XPATH, "//*[contains(text(), 'Record ID:')]"
        )

    print(f"   Found {len(card_candidates)} card element(s) on page.")

    for card in card_candidates:
        card_text = card.text

        # Extract the Record ID number from the card text
        id_match = re.search(r"Record\s+ID[:\s]+(\d+)", card_text, re.IGNORECASE)
        if not id_match:
            continue

        record_id = id_match.group(1)
        if record_id in seen:
            continue
        seen.add(record_id)

        s = score_record(card_text, user)
        print(f"   Record {record_id} | score={s} | card text: {card_text[:100].strip()!r}")

        if s >= 5:   # Threshold — lower to 3 to be more inclusive, raise to be stricter
            matching_ids.append(record_id)
            print(f"   ✔  Matched record {record_id}")

    # Diagnostic: if nothing found at all, dump href links to help debug
    if not seen:
        print("\n   ⚠  No 'Record ID:' text found in any element.")
        print("   Dumping all links on page for diagnostics:")
        all_links = driver.find_elements(By.TAG_NAME, "a")
        for lnk in all_links[:20]:
            print(f"      {lnk.get_attribute('href')} | text: {lnk.text[:60]!r}")
        driver.save_screenshot("search_page_debug.png")
        print("   Screenshot saved as search_page_debug.png")

    print(f"\n[1] Found {len(matching_ids)} matching record(s): {matching_ids}")
    return matching_ids


# ─────────────────────────────────────────────
#  STEP 2: Submit opt-out in batches of 5
# ─────────────────────────────────────────────

def wait_for_cloudflare(driver: webdriver.Chrome, batch_num: int, total_batches: int):
    """
    Pause and prompt the user to manually tick the Cloudflare checkbox.
    Then wait until it's been checked before continuing.
    """
    print(f"\n   ⚠  CLOUDFLARE CHECKPOINT — Batch {batch_num}/{total_batches}")
    print("   The browser is open and all fields have been filled in.")
    print("   Please click the 'Verify you are human' checkbox in the browser now.")
    print("   The script will automatically continue once it detects the checkbox is complete.")

    # Poll until Cloudflare iframe reports success OR user presses Enter as fallback
    # Cloudflare Turnstile adds a hidden input with a token value when solved
    deadline = time.time() + 120  # 2 minute timeout
    solved = False
    while time.time() < deadline:
        try:
            # Turnstile injects a hidden input named "cf-turnstile-response" with a token
            token_input = driver.find_element(By.CSS_SELECTOR, "input[name='cf-turnstile-response']")
            token_value = token_input.get_attribute("value") or ""
            if token_value.strip():
                print("   ✔  Cloudflare verified! Continuing...")
                solved = True
                break
        except NoSuchElementException:
            pass

        # Also check for the older checkbox style (cf-chl-widget)
        try:
            frames = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='cloudflare'], iframe[src*='turnstile']")
            if frames:
                driver.switch_to.frame(frames[0])
                checked = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked")
                driver.switch_to.default_content()
                if checked:
                    print("   ✔  Cloudflare checkbox checked! Continuing...")
                    solved = True
                    break
        except Exception:
            driver.switch_to.default_content()

        time.sleep(1)

    if not solved:
        # Fallback: let user press Enter manually
        input("   Could not auto-detect Cloudflare completion. Press Enter once you've ticked the box: ")


def submit_optout_batch(driver: webdriver.Chrome, record_ids: list[str], user: dict):
    """
    Open the opt-out page and fill in up to 5 record IDs, then submit.
    Repeats for additional batches.

    Form fields (from page inspection):
        - First name
        - Last name
        - Email address
        - Record ID x5  (placeholder: "Example: 123456789" / "Optional")
        - Reason for removal (textarea)
        - Cloudflare "Verify you are human" checkbox  ← manual step
        - "Request Removal" submit button
    """
    batch_size = 5
    total = len(record_ids)
    batches = [record_ids[i:i + batch_size] for i in range(0, total, batch_size)]

    print(f"\n[2] Submitting {total} record(s) in {len(batches)} batch(es) of up to {batch_size}.")

    for batch_num, batch in enumerate(batches, start=1):
        print(f"\n   ── Batch {batch_num}/{len(batches)}: {batch}")
        driver.get(OPTOUT_URL)
        time.sleep(PAGE_DELAY)

        wait = WebDriverWait(driver, 15)

        # ── First name ────────────────────────────────────────────────────
        try:
            field = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[placeholder='First Name']")
            ))
            field.clear()
            field.send_keys(user["first_name"])
        except TimeoutException:
            print("   ⚠  Could not find First Name field.")

        # ── Last name ─────────────────────────────────────────────────────
        try:
            field = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Last Name']")
            field.clear()
            field.send_keys(user["last_name"])
        except NoSuchElementException:
            print("   ⚠  Could not find Last Name field.")

        # ── Email ─────────────────────────────────────────────────────────
        try:
            field = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Email']")
            field.clear()
            field.send_keys(user["email"])
        except NoSuchElementException:
            print("   ⚠  Could not find Email field.")

        # ── Record ID fields ──────────────────────────────────────────────
        # First field has placeholder "Example: 123456789", rest have "Optional"
        id_fields = driver.find_elements(
            By.CSS_SELECTOR,
            "input[placeholder='Example: 123456789'], input[placeholder='Optional']"
        )

        if not id_fields:
            # Fallback: grab all text inputs and skip name/email
            all_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input:not([type])")
            id_fields = [
                f for f in all_inputs
                if f.get_attribute("placeholder") not in ("First Name", "Last Name", "Email")
                and f.get_attribute("type") not in ("hidden", "submit", "checkbox")
            ]

        if not id_fields:
            print("   ⚠  No Record ID fields found — saving debug screenshot.")
            driver.save_screenshot("optout_page_debug.png")
        else:
            for i, rid in enumerate(batch):
                if i < len(id_fields):
                    id_fields[i].clear()
                    id_fields[i].send_keys(rid)
                    print(f"   → Field {i+1}: {rid}")
                else:
                    print(f"   ⚠  No field for record {rid} (only {len(id_fields)} ID fields found)")

        # ── Reason for removal ────────────────────────────────────────────
        try:
            textarea = driver.find_element(By.CSS_SELECTOR, "textarea")
            textarea.clear()
            textarea.send_keys(REMOVAL_REASON)
        except NoSuchElementException:
            print("   ⚠  Could not find Reason textarea.")

        # ── Cloudflare — manual human verification ────────────────────────
        wait_for_cloudflare(driver, batch_num, len(batches))

        # ── Submit ────────────────────────────────────────────────────────
        try:
            submit_btn = driver.find_element(
                By.XPATH,
                "//input[@value='Request Removal'] | //button[contains(text(),'Request Removal')] | //input[@type='submit']"
            )
            submit_btn.click()
            print(f"   ✔  Batch {batch_num} submitted. Waiting for confirmation…")
            time.sleep(PAGE_DELAY + 2)
        except NoSuchElementException:
            print("   ⚠  Could not find the 'Request Removal' button.")

    print(f"\n[2] All {len(batches)} batch(es) complete.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  PeopleByName.com Opt-Out Automation")
    print("=" * 55)
    print(f"  Name   : {USER_DATA['first_name']} {USER_DATA['last_name']}")
    print(f"  Age    : {USER_DATA['age']}")
    print(f"  Addrs  : {', '.join(USER_DATA['addresses'])}")
    print("=" * 55)

    driver = build_driver(headless=not SHOW_BROWSER)

    try:
        record_ids = find_matching_record_ids(driver, USER_DATA)

        if not record_ids:
            print("\nNo matching records found. Nothing to opt out of.")
            return

        print(f"\nReady to opt out {len(record_ids)} record(s).")
        proceed = input("Proceed with opt-out submissions? [y/N]: ").strip().lower()
        if proceed != "y":
            print("Aborted.")
            return

        submit_optout_batch(driver, record_ids, USER_DATA)

        print("\n✅  Done! Check your email for any confirmation links from PeopleByName.")
    finally:
        input("\nPress Enter to close the browser…")
        driver.quit()


if __name__ == "__main__":
    main()