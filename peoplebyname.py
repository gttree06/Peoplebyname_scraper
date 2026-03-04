"""
PeopleByName.com Opt-Out Automation Script
-------------------------------------------
Prompts you for your personal information, searches peoplebyname.com for
matching records, collects the Record IDs, and automates the opt-out form
submissions (5 records per page).

Requirements:
    pip install selenium webdriver-manager

Usage:
    python peoplebyname_optout.py
"""

import time
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
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
#  SETTINGS
# ─────────────────────────────────────────────

SEARCH_URL      = "https://www.peoplebyname.com/people/{last}/{first}/"
OPTOUT_URL      = "https://www.peoplebyname.com/opt_out.php"
PAGE_DELAY      = 2      # seconds between page loads
SHOW_BROWSER    = True   # False = headless (no visible window)
MATCH_THRESHOLD = 5      # minimum score to consider a record a match


# ─────────────────────────────────────────────
#  STEP 0: Collect user info interactively
# ─────────────────────────────────────────────

def collect_user_data() -> dict:
    """Prompt the user for their personal details at runtime."""

    print("\n" + "=" * 58)
    print("   PeopleByName.com — Opt-Out Automation")
    print("=" * 58)
    print("   Enter your information below.")
    print("   This is used to find and match your records.\n")

    # Name
    while True:
        first_name = input("  First name: ").strip()
        last_name  = input("  Last name:  ").strip()
        if first_name and last_name:
            break
        print("  ⚠  First and last name are required. Please try again.\n")

    # Age
    while True:
        age_raw = input("  Age (press Enter to skip): ").strip()
        if age_raw == "":
            age = None
            break
        if age_raw.isdigit():
            age = int(age_raw)
            break
        print("  ⚠  Please enter a number or press Enter to skip.")

    # Addresses
    print("\n  Enter your addresses one at a time (current and past).")
    print("  Format example: 123 Main St, Springfield, IL")
    print("  Press Enter on a blank line when done.\n")
    addresses = []
    while True:
        addr = input(f"  Address {len(addresses) + 1}: ").strip()
        if addr == "":
            if not addresses:
                print("  ⚠  At least one address helps match records. Please enter one.\n")
                continue
            break
        addresses.append(addr)

    # Email
    while True:
        email = input("\n  Email address (for opt-out confirmation emails): ").strip()
        if "@" in email and "." in email:
            break
        print("  ⚠  Please enter a valid email address.")

    # Reason for removal
    default_reason = ("I did not consent to my personal information being published "
                      "and request its removal for privacy reasons.")
    print(f"\n  Reason for removal")
    print(f"  Default: \"{default_reason[:65]}…\"")
    custom = input("  Press Enter to use default, or type your own: ").strip()
    reason = custom if custom else default_reason

    user = {
        "first_name": first_name,
        "last_name":  last_name,
        "age":        age,
        "addresses":  addresses,
        "email":      email,
        "reason":     reason,
    }

    # Summary + confirm
    print("\n" + "-" * 58)
    print("  Review your information:")
    print(f"    Name    : {first_name} {last_name}")
    print(f"    Age     : {age if age else 'not provided'}")
    for i, a in enumerate(addresses, 1):
        print(f"    Address {i}: {a}")
    print(f"    Email   : {email}")
    print(f"    Reason  : {reason[:65]}{'…' if len(reason) > 65 else ''}")
    print("-" * 58)

    confirm = input("\n  Does this look correct? [Y/n]: ").strip().lower()
    if confirm == "n":
        print("\n  Starting over…\n")
        return collect_user_data()

    return user


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

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
    return re.sub(r"\s+", " ", text.strip().lower())


def scroll_to_load_all(driver: webdriver.Chrome):
    """
    Scroll the page in chunks to trigger lazy-loaded content,
    repeating until no new content appears.
    """
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        # Scroll down in small steps so lazy-load triggers fire
        for _ in range(8):
            driver.execute_script("window.scrollBy(0, 500);")
            time.sleep(0.25)
        time.sleep(1.0)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    # Scroll back to top so selectors work from the beginning
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)


def build_address_tokens(addresses: list) -> list:
    """
    Break each address into meaningful tokens for matching.
    e.g. "142 Newcastle Dr Jupiter, FL 33458"
      -> ["142 newcastle dr jupiter", "fl", "33458",
          "142 newcastle", "newcastle dr", "jupiter"]
    This is more flexible than splitting only on commas.
    """
    tokens = set()
    for addr in addresses:
        n = normalize(addr)
        # Full address (no zip) as one token
        parts = n.split(",")
        for part in parts:
            part = part.strip()
            if part:
                tokens.add(part)
        # Also add individual words so partial street matches work
        words = re.findall(r"[a-z0-9]+", n)
        for word in words:
            if len(word) > 2:   # skip tiny words like "dr", "st" alone
                tokens.add(word)
        # Bigrams (pairs of consecutive words) catch "newcastle dr", "lake butler" etc.
        for i in range(len(words) - 1):
            tokens.add(f"{words[i]} {words[i+1]}")
    return list(tokens)


def score_record(card_text: str, user: dict, addr_tokens: list) -> tuple:
    """
    Score a record card against user data.
    Returns (score, addr_matched) where addr_matched is True only if at least
    one address token was found in the card. A card is only collected if BOTH
    the name matches AND addr_matched is True.

    Scoring:
      +10  full name found in card
      +5   exact age match
      +2   age +-1
      +4   full address part (street, city, or zip section) matched
      +2   individual address word or bigram matched
    """
    score = 0
    addr_matched = False
    text  = normalize(card_text)
    full_name = normalize(f"{user['first_name']} {user['last_name']}")

    if full_name in text:
        score += 10

    if user.get("age"):
        if str(user["age"]) in text:
            score += 5
        for delta in (-1, 1):
            if str(user["age"] + delta) in text:
                score += 2

    for token in addr_tokens:
        if token in text:
            addr_matched = True
            # Longer tokens (full street / city sections) score higher
            bonus = 4 if len(token) > 8 else 2
            score += bonus

    return score, addr_matched


# ─────────────────────────────────────────────
#  STEP 1: Find matching record IDs
# ─────────────────────────────────────────────

def find_matching_record_ids(driver: webdriver.Chrome, user: dict) -> list:
    """
    Search the results page and return IDs of ALL cards that match the user.

    Each card on peoplebyname shows:
        Record ID: 425234752
        Stephanie Sobeck
        142 Newcastle Dr
        Jupiter, FL 33458
    We read "Record ID: XXXXXXXXX" text directly from the DOM.
    The page is fully scrolled first so all lazy-loaded cards are present.
    """
    url = SEARCH_URL.format(
        last=user["last_name"].capitalize(),
        first=user["first_name"].capitalize(),
    )
    print(f"\n[1] Loading search results: {url}")
    driver.get(url)
    time.sleep(PAGE_DELAY)

    # Scroll the entire page so all cards load into the DOM
    print("   Scrolling page to load all records…")
    scroll_to_load_all(driver)

    # Pre-build address tokens once for efficiency
    addr_tokens = build_address_tokens(user.get("addresses", []))
    print(f"   Address tokens for matching: {addr_tokens}")

    matching_ids = []
    seen = set()

    # Find all elements whose text contains "Record ID:" then walk up to card container
    # From devtools: Record ID is in <div class="background_chk1"> inside a <li>
    # The full card (<li>) contains: Record ID div, name <b>, address text, phone
    # Strategy: find all <li> elements that contain a "background_chk1" div
    card_lis = driver.find_elements(
        By.XPATH, "//li[.//div[contains(@class,'background_chk')]]"
    )

    # Fallback: find by Record ID text and walk up to <li>
    if not card_lis:
        card_lis = driver.find_elements(
            By.XPATH, "//*[contains(text(),'Record ID:')]/ancestor::li[1]"
        )

    # Last fallback: find the label divs and use JS to get parent li text
    use_js_fallback = not card_lis

    if use_js_fallback:
        card_candidates = driver.find_elements(
            By.XPATH, "//*[contains(text(), 'Record ID:')]"
        )
    else:
        card_candidates = card_lis

    print(f"   Found {len(card_candidates)} card(s) on page.")

    for card_el in card_candidates:
        if use_js_fallback:
            # Walk up to <li> via JS
            card_text = driver.execute_script("""
                var el = arguments[0];
                for (var i = 0; i < 10; i++) {
                    if (!el.parentElement) break;
                    el = el.parentElement;
                    if (el.tagName === 'LI') return (el.innerText || '').trim();
                }
                return (el.innerText || '').trim();
            """, card_el)
        else:
            card_text = card_el.text or card_el.get_attribute("innerText") or ""

        id_match = re.search(r"Record\s+ID[:\s]+(\d+)", card_text, re.IGNORECASE)
        if not id_match:
            continue

        record_id = id_match.group(1)
        if record_id in seen:
            continue
        seen.add(record_id)

        s, addr_matched = score_record(card_text, user, addr_tokens)
        preview = card_text.replace("\n", " ")[:100]
        print(f"   ID {record_id} | score={s} | addr={addr_matched} | {preview!r}")

        if s >= MATCH_THRESHOLD and addr_matched:
            matching_ids.append(record_id)
            print(f"   Matched!")
        elif s >= MATCH_THRESHOLD and not addr_matched:
            print(f"   Skipped (name matched but no address match)")

    # Diagnostic dump if nothing found at all
    if not seen:
        print("\n   ⚠  No 'Record ID:' text found — page structure may have changed.")
        print("   Saving debug screenshot as search_debug.png")
        driver.save_screenshot("search_debug.png")
        print("   Sample links found on page:")
        for lnk in driver.find_elements(By.TAG_NAME, "a")[:20]:
            print(f"      {lnk.get_attribute('href')} | {lnk.text[:50]!r}")

    print(f"\n   Result: {len(matching_ids)} matching record(s) → {matching_ids}")
    return matching_ids


# ─────────────────────────────────────────────
#  STEP 2: Opt-out submissions (5 per page)
# ─────────────────────────────────────────────

def wait_for_cloudflare(driver: webdriver.Chrome, batch_num: int, total: int):
    """Pause until the user ticks the Cloudflare checkbox."""
    print(f"\n   ── CLOUDFLARE CHECK  (batch {batch_num}/{total}) ──")
    print("   All fields have been filled in the browser.")
    print("   Please click 'Verify you are human' in the browser window.")
    print("   The script will continue automatically once verified…\n")

    deadline = time.time() + 120  # 2-minute timeout
    solved   = False

    while time.time() < deadline:
        # Check for Turnstile hidden token (modern Cloudflare)
        try:
            token = driver.find_element(
                By.CSS_SELECTOR, "input[name='cf-turnstile-response']"
            ).get_attribute("value") or ""
            if token.strip():
                print("   ✔  Cloudflare verified automatically!")
                solved = True
                break
        except NoSuchElementException:
            pass

        # Check for older iframe checkbox style
        try:
            frames = driver.find_elements(
                By.CSS_SELECTOR,
                "iframe[src*='cloudflare'], iframe[src*='turnstile']"
            )
            if frames:
                driver.switch_to.frame(frames[0])
                checked = driver.find_elements(
                    By.CSS_SELECTOR, "input[type='checkbox']:checked"
                )
                driver.switch_to.default_content()
                if checked:
                    print("   ✔  Cloudflare checkbox checked!")
                    solved = True
                    break
        except Exception:
            driver.switch_to.default_content()

        time.sleep(1)

    if not solved:
        input("   Could not auto-detect completion. Press Enter once you've ticked the box: ")


def submit_optout_batch(driver: webdriver.Chrome, record_ids: list, user: dict):
    """
    Submit the opt-out form in batches of 5.

    Form fields (confirmed from page screenshot):
        First name | Last name | Email address
        Record ID x5 (placeholder: "Example: 123456789" / "Optional")
        Reason for removal (textarea)
        Cloudflare checkbox  <- manual step
        "Request Removal" button
    """
    batches = [record_ids[i:i + 5] for i in range(0, len(record_ids), 5)]
    print(f"\n[2] {len(record_ids)} record(s) → {len(batches)} batch(es) of up to 5.")

    for num, batch in enumerate(batches, start=1):
        print(f"\n   ══ Batch {num}/{len(batches)}: {batch}")
        driver.get(OPTOUT_URL)
        time.sleep(PAGE_DELAY)

        wait = WebDriverWait(driver, 15)

        # First name
        try:
            f = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[placeholder='First Name']")
            ))
            f.clear(); f.send_keys(user["first_name"])
        except TimeoutException:
            print("   ⚠  First Name field not found.")

        # Last name
        try:
            f = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Last Name']")
            f.clear(); f.send_keys(user["last_name"])
        except NoSuchElementException:
            print("   ⚠  Last Name field not found.")

        # Email
        try:
            f = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Email']")
            f.clear(); f.send_keys(user["email"])
        except NoSuchElementException:
            print("   ⚠  Email field not found.")

        # Record ID fields
        id_fields = driver.find_elements(
            By.CSS_SELECTOR,
            "input[placeholder='Example: 123456789'], input[placeholder='Optional']"
        )

        # Fallback selector
        if not id_fields:
            all_inputs = driver.find_elements(
                By.CSS_SELECTOR, "input[type='text'], input:not([type])"
            )
            id_fields = [
                f for f in all_inputs
                if f.get_attribute("placeholder") not in ("First Name", "Last Name", "Email")
                and f.get_attribute("type") not in ("hidden", "submit", "checkbox")
            ]

        if not id_fields:
            print("   ⚠  Record ID fields not found — saving debug screenshot.")
            driver.save_screenshot("optout_debug.png")
        else:
            for i, rid in enumerate(batch):
                if i < len(id_fields):
                    id_fields[i].clear()
                    id_fields[i].send_keys(rid)
                    print(f"   → Field {i+1}: {rid}")
                else:
                    print(f"   ⚠  No field available for ID {rid}")

        # Reason for removal
        try:
            ta = driver.find_element(By.CSS_SELECTOR, "textarea")
            ta.clear(); ta.send_keys(user["reason"])
        except NoSuchElementException:
            print("   ⚠  Reason textarea not found.")

        # Cloudflare — requires manual human verification
        wait_for_cloudflare(driver, num, len(batches))

        # Submit
        try:
            btn = driver.find_element(
                By.XPATH,
                "//input[@value='Request Removal'] | "
                "//button[contains(text(),'Request Removal')] | "
                "//input[@type='submit']"
            )
            btn.click()
            print(f"   ✔  Batch {num} submitted.")
            time.sleep(PAGE_DELAY + 2)
        except NoSuchElementException:
            print("   ⚠  Submit button not found.")

    print(f"\n[2] All {len(batches)} batch(es) complete.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    # Collect info interactively from the user
    user = collect_user_data()

    print("\n  Opening browser…")
    driver = build_driver(headless=not SHOW_BROWSER)

    try:
        # Search for matching records
        record_ids = find_matching_record_ids(driver, user)

        if not record_ids:
            print("\n  No matching records found. Nothing to opt out of.")
            return

        # Confirm before submitting
        print(f"\n  Ready to submit opt-out for {len(record_ids)} record(s).")
        go = input("  Proceed? [Y/n]: ").strip().lower()
        if go == "n":
            print("  Aborted.")
            return

        # Submit in batches of 5
        submit_optout_batch(driver, record_ids, user)

        print("\n  ✅  Done! Check your email for confirmation links from PeopleByName.")

    finally:
        input("\n  Press Enter to close the browser… ")
        driver.quit()


if __name__ == "__main__":
    main()