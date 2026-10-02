from pathlib import Path
from collections import namedtuple
from contextlib import closing
import json
import locale
import os
import platform
import re
import sqlite3
import subprocess

import phonenumbers

base_dir = Path(__file__).parent.parent

# Who "xyz" is, from three places, in this order:
#   1. names you saved with "save contact xyz +91 98765 43210" (data/contacts.json)
#   2. your phone's contacts, which WhatsApp syncs to the linked desk-agent (data/whatsapp.db)
#   3. the Mac's Contacts app, asked only when 1 and 2 know nobody by that name
# A name that fits more than one number is never guessed: the user is asked which one.

DATA = base_dir / "data"
CONTACTS_PATH = DATA / "contacts.json"
WHATSAPP_DB = DATA / "whatsapp.db"     # the linked device's session, written by tools/whatsapp_link.py

Person = namedtuple("Person", "name number source")   # number is international: +919876543210

MAC_CONTACTS = '''
on run argv
    -- read outside the tell block: inside it, "item 1 of argv" would be asked of each person
    set wanted to item 1 of argv
    set answer to ""
    tell application "Contacts"
        repeat with thePerson in (every person whose name contains wanted)
            repeat with theNumber in (phones of thePerson)
                set answer to answer & (name of thePerson) & tab & (value of theNumber) & linefeed
            end repeat
        end repeat
    end tell
    return answer
end run
'''

def home_region():
    # numbers saved without a country code ("098765 43210") belong to this country
    region = os.environ.get("WHATSAPP_REGION", "").strip().upper()

    if region:
        return region

    try:
        system = platform.system()

        if system == "Darwin":
            value = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=5).stdout
        elif system == "Windows":
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85)
            value = buffer.value
        else:
            value = locale.getlocale()[0] or os.environ.get("LANG", "")
    except Exception:
        return None

    # "en_IN", "en-IN", or "en_US@rg=inzzzz" when the region is set apart from the language
    match = re.search(r"rg=([a-z]{2})", value, re.I) or re.search(r"[_-]([A-Z]{2})\b", value)

    return match.group(1).upper() if match else None

def normalize_number(raw, region=None):
    # "+91 98765 43210", "0091 98765 43210", "098765 43210" -> "+919876543210"; None if not a phone number
    text = str(raw).strip()

    if text.startswith("00"):
        text = "+" + text[2:]

    try:
        number = phonenumbers.parse(text, None if text.startswith("+") else region)
    except phonenumbers.NumberParseException:
        return None

    # a number said with its + may be newer than the library's list of valid ranges
    if not (phonenumbers.is_valid_number(number) or (text.startswith("+") and phonenumbers.is_possible_number(number))):
        return None

    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)

def pretty(number):
    return phonenumbers.format_number(phonenumbers.parse(number), phonenumbers.PhoneNumberFormat.INTERNATIONAL)

def load_book():
    try:
        book = json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}

    return book if isinstance(book, dict) else {}

def save_contact(name, number):
    name = " ".join(str(name).split())

    if not name:
        return "Tell me the person's name."

    e164 = normalize_number(number, home_region())

    if e164 is None:
        return f"{number} is not a phone number I can use. Say it with the country code, like +91 98765 43210."

    book = {key: value for key, value in load_book().items() if key.casefold() != name.casefold()}
    book[name] = e164

    # write a new file, then swap it in: a crash midway cannot leave half a contact list
    DATA.mkdir(exist_ok=True)
    temporary = CONTACTS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(book, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, CONTACTS_PATH)

    return f"Saved {name}: {pretty(e164)}"

def saved_people():
    people = []

    for name, number in load_book().items():
        number = normalize_number(number)

        if number:
            people.append(Person(name, number, "saved"))

    return people

def whatsapp_people():
    # names from your phone's address book; people known only by the name they gave themselves are left out
    if not WHATSAPP_DB.exists():
        return []

    try:
        with closing(sqlite3.connect(WHATSAPP_DB, timeout=5)) as db:
            rows = db.execute("SELECT their_jid, full_name, first_name FROM whatsmeow_contacts").fetchall()

            try:
                # newer chats name people by a private id (…@lid); this table gives their number
                lids = {lid.split("@")[0]: pn.split("@")[0] for lid, pn in db.execute("SELECT lid, pn FROM whatsmeow_lid_map")}
            except sqlite3.Error:
                lids = {}
    except sqlite3.Error:
        return []

    people = []

    for jid, full_name, first_name in rows:
        name = (full_name or first_name or "").strip()
        user, _, server = str(jid).partition("@")
        user = user.split(":")[0]

        if server == "lid":
            user = lids.get(user, "")
        elif server != "s.whatsapp.net":
            continue

        number = normalize_number("+" + user) if name and user else None

        if number:
            people.append(Person(name, number, "WhatsApp"))

    return people

def mac_people(query):
    # (people, problem): problem says why the Contacts app could not be read
    first_word = words(query)[0]

    try:
        # the first time, macOS asks whether this app may use Contacts: give the user time to answer
        found = subprocess.run(["osascript", "-e", MAC_CONTACTS, first_word], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return [], "the Contacts app did not answer"

    if found.returncode != 0:
        return [], "the Contacts app is not allowed (System Settings → Privacy & Security → Automation → Contacts)"

    region = home_region()
    people = []

    for line in found.stdout.splitlines():
        name, _, raw = line.partition("\t")
        number = normalize_number(raw, region)

        if number:
            people.append(Person(name.strip(), number, "Contacts"))

    return people, None

def words(text):
    return re.findall(r"\w+", str(text).casefold())

def covers(query, name):
    # "rahul", "rah sha" and "sharma" all fit "Rahul Sharma": each word said starts a word of the name
    said, name_words = words(query), words(name)

    return all(any(word.startswith(part) for word in name_words) for part in said)

def pick(query, people):
    # (person, None) for one clear match, (None, question) for several, (None, None) for nobody
    exact = [person for person in people if words(person.name) == words(query)]
    matches = exact or [person for person in people if covers(query, person.name)]

    by_number = {}

    for person in matches:
        by_number.setdefault(person.number, person)   # the same number from two places is one person

    if len(by_number) == 1:
        return next(iter(by_number.values())), None

    if len(by_number) > 1:
        options = ", ".join(f"{person.name} ({pretty(person.number)})" for person in list(by_number.values())[:6])
        return None, f"More than one person fits '{query}': {options}. Say the full name or the number."

    return None, None

def find_person(query):
    # (Person, None) or (None, message for the user)
    query = " ".join(str(query).split())

    if re.fullmatch(r"\+?[\d\s().-]{6,}", query):
        number = normalize_number(query, home_region())

        if number is None:
            return None, f"{query} is not a phone number I can use. Say it with the country code, like +91 98765 43210."

        return Person(pretty(number), number, "number"), None

    if not words(query):
        return None, "Tell me who to send it to."

    person, question = pick(query, saved_people() + whatsapp_people())

    if person or question:
        return person, question

    problem = None

    if platform.system() == "Darwin":
        people, problem = mac_people(query)
        person, question = pick(query, people)

        if person or question:
            return person, question

    reply = f"I don't have a number for {query}. Tell me once: save contact {query} +91 98765 43210 (with their real number)."

    return None, reply + (f" (I could not look in your Mac's contacts: {problem}.)" if problem else "")
