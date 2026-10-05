from pathlib import Path
from contextlib import closing
from urllib.parse import quote
import json
import os
import re
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import webbrowser

from tools.contacts import (HONORIFICS, LEARN_AFTER, WHATSAPP_DB, Person, covers, find_people, home_region, is_self,
                            my_numbers, normalize_number, pretty, remember, save_contact, usual, words)
from tools.whatsapp_link import forget_link
from tools.files import clean_file_name, name_key, resolve_file

base_dir = Path(__file__).parent.parent

# Two ways to send, both from your own WhatsApp number:
#   ready to send (default): opens the chat in the WhatsApp app with the file attached and stops;
#       you press Enter. Nothing can go out that you did not see.
#   direct ("send it directly"): desk-agent is linked to WhatsApp like WhatsApp Web and sends by
#       itself through tools/whatsapp_link.py, reporting only what WhatsApp's server confirmed.

HELPER = base_dir / "tools" / "whatsapp_link.py"
MAX_SIZE = 2 * 1024 ** 3    # WhatsApp takes files up to 2 GB

IMAGES = [".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".tiff", ".bmp"]
AUDIO = [".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"]
VIDEOS = [".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"]
# types found without being named; words like "pages" or "key" are left out because they are also
# ordinary words in file names, and every type listed here is dropped from the name as filler
SEND_TYPES = [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".ppt", ".txt", ".rtf", ".odt", ".epub"] + IMAGES + AUDIO + VIDEOS + [".zip", ".rar", ".7z"]
# a type the user says ("the pdf abc", "word file abc") limits the search to that type
KIND_WORDS = {
    "pdf": [".pdf"], "word": [".docx", ".doc"], "docx": [".docx"], "doc": [".doc"],
    "excel": [".xlsx", ".xls", ".csv"], "xlsx": [".xlsx"], "csv": [".csv"],
    "powerpoint": [".pptx", ".ppt"], "pptx": [".pptx"], "ppt": [".ppt"],
    "pages": [".pages"], "keynote": [".key"],
    "image": IMAGES, "photo": IMAGES, "picture": IMAGES, "pic": IMAGES,
    "jpg": [".jpg", ".jpeg"], "jpeg": [".jpg", ".jpeg"], "png": [".png"], "heic": [".heic"],
    "video": VIDEOS, "mp4": [".mp4"], "mov": [".mov"],
    "audio": AUDIO, "song": AUDIO, "mp3": [".mp3"],
    "zip": [".zip"], "txt": [".txt"],
}
ANY_TYPE = SEND_TYPES + [".pages", ".numbers", ".key", ".md", ".html", ".json", ".xml"]
# words around a file name that are not part of it: "the file named abc", "abc wali pdf", "abc on whatsapp"
FILE_EXTRAS = {"named", "called", "name", "wali", "wala", "vali", "vala", "waali", "waala", "this", "that",
               "on", "via", "whatsapp", "send", "to", "ko", "pe"}
# "my latest download", "the last pdf", "newest screenshot": no name, just the newest file
NEWEST = {"latest", "last", "newest", "recent", "recently", "just", "new"}
FOLDERS = {"download": "Downloads", "downloads": "Downloads", "downloaded": "Downloads", "desktop": "Desktop", "documents": "Documents"}
SCREENSHOTS = {"screenshot", "screenshots", "ss"}

# a question ("which Harsh?") keeps the request it came from this long, so a short answer finishes it
ANSWER_WITHIN = 300     # seconds
ORDINALS = {"1": 0, "one": 0, "first": 0, "1st": 0, "pehla": 0, "pehli": 0, "pahla": 0,
            "2": 1, "two": 1, "second": 1, "2nd": 1, "dusra": 1, "doosra": 1, "dusri": 1,
            "3": 2, "three": 2, "third": 2, "3rd": 2, "teesra": 2, "tisra": 2,
            "4": 3, "four": 3, "fourth": 3, "4th": 3, "5": 4, "five": 4, "fifth": 4, "5th": 4,
            "6": 5, "six": 5, "sixth": 5, "6th": 5}
FILLER = {"the", "number", "no", "option", "send", "to", "it", "him", "her", "them", "please", "pls",
          "that", "this", "is", "wala", "vala", "waala", "in", "from", "folder", "file", "one"}
YES = {"yes", "y", "ok", "okay", "sure", "yeah", "yep", "haan", "ha", "han", "send", "send it", "go ahead", "do it"}
CANCEL = {"cancel", "no", "nope", "stop", "dont", "don t", "do not", "leave it", "never mind", "nevermind",
          "nahi", "na", "mat bhejo", "rehne do"}
DELIVERED = ("Opened", "Sent ")    # every reply that reached the chat starts with one of these

pending = None      # {"kind": "person" or "file", "options": [...], "request": {...}, "since": time}

NOT_LINKED = ("To send directly, desk-agent must first be linked to your WhatsApp: say 'link whatsapp' and scan "
              "the code with your phone (WhatsApp → Settings → Linked devices → Link a device).")

PASTE_WHEN_OPEN = '''
on run
    tell application "System Events"
        repeat 80 times
            if (name of first application process whose frontmost is true) is "WhatsApp" then exit repeat
            delay 0.25
        end repeat
        if (name of first application process whose frontmost is true) is not "WhatsApp" then return "not open"
    end tell
    delay SETTLE
    tell application "System Events"
        if (name of first application process whose frontmost is true) is not "WhatsApp" then return "not open"
        keystroke "v" using command down
    end tell
    return "pasted"
end run
'''

COPY_FILE = '''
ObjC.import("AppKit");
function run(argv) {
    const board = $.NSPasteboard.generalPasteboard;
    board.clearContents;
    return board.writeObjects($.NSArray.arrayWithObject($.NSURL.fileURLWithPath(argv[0]))) ? "copied" : "failed";
}
'''

def numbered(options):
    lines = [f"{option.name} ({pretty(option.number)})" if hasattr(option, "number") else str(option) for option in options]

    return "\n".join(f"  {n}. {line}" for n, line in enumerate(lines, 1))

def find_file(name, direct):
    # (path, None, []) or (None, message, files the message offers to pick from);
    # direct sending accepts only a file that is clearly the one meant
    said = name.strip()
    typed = Path(said).suffix.lower()

    # a full path is the file itself; a bare name is searched for, never matched against the current folder
    if Path(said).expanduser().is_absolute() and Path(said).expanduser().is_file():
        return Path(said).expanduser(), None, []

    kept = []
    extensions = []
    several = len(said.split()) > 1

    for word in said.split():
        bare = word.lower().strip(".,!?")
        kind = KIND_WORDS.get(bare)

        if kind and several:
            extensions += [ext for ext in kind if ext not in extensions]
        elif bare not in FILE_EXTRAS or not several:
            kept.append(word)

    if typed in ANY_TYPE:
        extensions = [ext for ext in ANY_TYPE if ext == typed or {ext, typed} == {".jpg", ".jpeg"}]

    bare = [word.lower().strip(".,!?") for word in kept]
    named = [word for word in bare if word not in NEWEST | SCREENSHOTS | set(FOLDERS) | {"file", "my", "the", "one", "i"}]

    # nothing said but "latest", a folder or "screenshot": the newest such file
    if not named and set(bare) & (NEWEST | SCREENSHOTS):
        return newest_file(bare, extensions)

    extensions = extensions or SEND_TYPES
    name = " ".join(kept)
    path, message = resolve_file(name, extensions)

    if path is None and (message or "").startswith("no such file"):
        return None, (f"I couldn't find a file called '{name}' in your folders. Say a word from its name "
                      "(like: send the bank statement pdf to me), or: send my latest download to me."), []

    if path is None:
        # "Found multiple files with that name..." / "did you mean:" followed by one path per line
        options = [Path(line) for line in (message or "").splitlines()[1:] if Path(line).is_file()]

        if options:
            return None, f"{message.splitlines()[0]}\n{numbered(options)}\nSay 1, 2... or where it is.", options

        return None, message, []

    if not direct:
        return path, None, []

    if name_key(path.stem) != clean_file_name(name, extensions):
        return None, f"The closest file is {path}. Say yes to send it, or tell me its full name.", [path]

    twins = [other for other in path.parent.iterdir() if other != path and other.stem == path.stem and other.suffix.lower() in extensions]

    if twins:
        options = [path] + twins
        return None, f"There are {len(options)} files named {path.stem}:\n{numbered(options)}\nWhich one? Say 1, 2... or its type.", options

    return path, None, []

def screenshot_folder():
    # macOS saves screenshots on the Desktop unless the user chose another folder
    if platform.system() == "Darwin":
        chosen = subprocess.run(["defaults", "read", "com.apple.screencapture", "location"], capture_output=True, text=True).stdout.strip()

        if chosen and Path(chosen).expanduser().is_dir():
            return Path(chosen).expanduser()

    return Path.home() / ("Pictures/Screenshots" if platform.system() == "Windows" else "Desktop")

def newest_file(said, extensions):
    screenshot = bool(set(said) & SCREENSHOTS)
    named_folder = [FOLDERS[word] for word in said if word in FOLDERS]
    folder = Path.home() / named_folder[0] if named_folder else (screenshot_folder() if screenshot else Path.home() / "Downloads")
    types = extensions or (IMAGES if screenshot else SEND_TYPES)

    try:
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in types and not f.name.startswith(".")
                 and (not screenshot or f.name.lower().startswith(("screenshot", "screen shot")))]
    except OSError:
        files = []

    if not files:
        return None, f"There is no {'screenshot' if screenshot else 'file of that kind'} in {folder}.", []

    return max(files, key=lambda f: f.stat().st_mtime), None, []

def chat_link(number, message, web=False):
    digits = number.lstrip("+")
    text = f"&text={quote(message)}" if message else ""

    return f"https://web.whatsapp.com/send?phone={digits}{text}" if web else f"whatsapp://send?phone={digits}{text}"

def send_whatsapp(file, to, message=None, direct=False):
    global pending
    pending = None      # a new request drops any question still waiting

    direct = direct is True or str(direct).strip().lower() in ("true", "yes", "1")
    message = str(message or "").strip() or None
    file = str(file or "").strip()
    request = {"to": str(to), "path": None, "message": message, "direct": direct}

    if not file and not message:
        return "Tell me which file, or what message, to send."

    if file:
        path, question, options = find_file(file, direct)

        if path is None:
            return ask("file", options, request, question) if options else question

        request["path"] = path

    return to_person(request)

def to_person(request):
    path = request["path"]

    if path and path.stat().st_size > MAX_SIZE:
        return f"{path.name} is larger than 2 GB, which is the most WhatsApp takes."

    if is_self(request["to"]):
        people = my_numbers()

        if not people:
            return ask("my number", [], request, "What is your own WhatsApp number? Reply with it, like +91 98765 43210. I'll remember it.")

        return deliver(people, request)

    people, problem = find_people(request["to"])

    if not people:
        return problem

    return deliver(people, request)

def deliver(people, request):
    to = request["to"]
    learned = len(people) > 1

    if learned:
        person = usual(to, people)

        if person is None:
            question = (f"More than one person fits '{to}':\n{numbered(people)}\nWhich one? Say 1, 2... or the name. "
                        f"Pick the same person {LEARN_AFTER} times for '{to}' and I will stop asking.")
            return ask("person", people, request, question)
    else:
        person = people[0]

    if request["direct"]:
        reply = send_directly(person, request["path"], request["message"])
    else:
        reply = open_ready_to_send(person, request["path"], request["message"])

    if not reply.startswith(DELIVERED):
        return reply

    if person.source != "number" and not is_self(to):
        remember(to, person)

    if learned:
        # picked from memory: say so, and let one short answer switch to someone else
        ask("person", people, request, None)
        pending["done"] = person.number
        reply += f" I picked {person.name} because you chose them for '{to}' before. Wrong person? Say the right number:\n{numbered(people)}"

    return reply

def ask(kind, options, request, question):
    # keep the request with its question, so the answer alone finishes it
    global pending
    pending = {"kind": kind, "options": options, "request": request, "since": time.monotonic()}

    return question

def chosen(text, options, kind):
    # the option a short answer points at: "2", "the first one", "dusra", "harsh patel", "+91 98...",
    # "the pdf", "the one in downloads", "yes" (when there is one option); None if it is not an answer
    said = words(text)
    ranks = [ORDINALS[word] for word in said if word in ORDINALS and word != "one"] or [ORDINALS[word] for word in said if word == "one"]
    rest = [word for word in said if word not in ORDINALS and word not in FILLER]

    if ranks and not rest and len(set(ranks)) == 1:
        return options[ranks[0]] if ranks[0] < len(options) else "out of range"

    if len(options) == 1 and " ".join(said) in YES:
        return options[0]

    if not rest:
        return None

    if kind == "person":
        # only a reply that is just a number is read as one ("save contact harsh +91..." is a new request)
        number = normalize_number(text, home_region()) if all(word.isdigit() for word in rest) else None
        fits = [person for person in options if person.number == number] if number else []

        for form in (rest, [word for word in rest if word not in HONORIFICS]):
            fits = fits or ([person for person in options if covers(" ".join(form), person.name)] if form else [])
    else:
        kinds = [ext for word in rest for ext in KIND_WORDS.get(word, [])]
        fits = [path for path in options if path.suffix.lower() in kinds] if kinds else [path for path in options if covers(" ".join(rest), str(path))]

    return fits[0] if len(fits) == 1 else None

def answer_pending(text):
    # called before the router: a short answer to the last question finishes that request;
    # anything else is a new request, and the question is dropped
    global pending
    waiting, pending = pending, None

    if waiting is None or time.monotonic() - waiting["since"] > ANSWER_WITHIN:
        return None

    if " ".join(words(text)) in CANCEL:
        return "Okay, I won't send it."

    if waiting["kind"] == "my number":
        # the user's own number, asked once: saved as "me", then the send goes on
        number = normalize_number(text, home_region()) if re.fullmatch(r"\s*\+?[\d\s().-]{6,}\s*", text) else None

        if number is None:
            if any(ch.isdigit() for ch in text):
                pending = waiting
                return "That doesn't look like a phone number. Say it with the country code, like +91 98765 43210."

            return None

        save_contact("me", number)
        return deliver([Person("yourself", number, "saved")], dict(waiting["request"]))

    choice = chosen(text, waiting["options"], waiting["kind"])

    if choice == "out of range":
        pending = waiting
        return f"There are only {len(waiting['options'])} choices: say a number from 1 to {len(waiting['options'])}."

    if choice is None:
        return None

    request = dict(waiting["request"])

    if waiting.get("done") and waiting["done"] == getattr(choice, "number", None):
        return f"{choice.name} is the one I already used."

    if waiting["kind"] == "file":
        request["path"] = choice
        return to_person(request)

    if is_self(request["to"]):
        save_contact("me", choice.number)     # which of your numbers is your WhatsApp: asked only once

    return deliver([choice], request)

# ---- ready to send: the WhatsApp app does the sending when you press Enter ----

def open_ready_to_send(person, path, message):
    who = f"{person.name} ({pretty(person.number)})" if person.source != "number" else person.name
    system = platform.system()

    if system == "Darwin":
        return ready_on_mac(who, person.number, path, message)

    if system == "Windows":
        return ready_on_windows(who, person.number, path, message)

    # no WhatsApp app for Linux: WhatsApp Web opens the chat, the file is attached by hand
    webbrowser.open(chat_link(person.number, message, web=True))

    return f"Opened your chat with {who} in WhatsApp Web." + (f" Attach {path} with the paperclip, then press Enter." if path else " Press Enter to send.")

def ready_on_mac(who, number, path, message):
    was_running = subprocess.run(["pgrep", "-x", "WhatsApp"], capture_output=True).returncode == 0

    if path:
        copied = subprocess.run(["osascript", "-l", "JavaScript", "-e", COPY_FILE, str(path)], capture_output=True, text=True, timeout=15)

        if copied.stdout.strip() != "copied":
            return f"I could not copy {path.name} to attach it ({copied.stderr.strip() or 'clipboard refused'})."

    opened = subprocess.run(["open", chat_link(number, message)], capture_output=True, text=True)

    if opened.returncode != 0:
        return "The WhatsApp app is not installed on this Mac. Install it from the App Store (or whatsapp.com/download) and sign in once."

    if not path:
        return f"Opened your WhatsApp chat with {who} with the message typed in. Check it, then press Enter to send."

    # a freshly started WhatsApp needs longer before the chat can take a paste
    script = PASTE_WHEN_OPEN.replace("SETTLE", "1.5" if was_running else "4")

    try:
        pasted = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=40)
        outcome = pasted.stdout.strip() if pasted.returncode == 0 else pasted.stderr
    except subprocess.TimeoutExpired:
        outcome = "not open"

    if outcome == "pasted":
        return f"Opened your WhatsApp chat with {who} and attached {path.name}. Check it, then press Enter to send."

    reply = f"Opened your WhatsApp chat with {who}. {path.name} is copied: press ⌘V to attach it, then Enter to send."

    if outcome != "not open":
        reply += (" To let me attach files for you, allow the app you run me in (Terminal, iTerm or VS Code) in "
                  "System Settings → Privacy & Security → Accessibility.")

    return reply

def windows_front_title():
    import ctypes
    user32 = ctypes.windll.user32
    window = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(window)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(window, title, length + 1)

    return title.value

def windows_paste():
    import ctypes
    user32 = ctypes.windll.user32

    # Ctrl down, V down, V up, Ctrl up
    for key, flags in ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)):
        user32.keybd_event(key, 0, flags, 0)

def ready_on_windows(who, number, path, message):
    if path:
        # the path goes in through the environment, so no character in it can change the command
        copied = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -LiteralPath $env:DESK_AGENT_FILE"],
                                env={**os.environ, "DESK_AGENT_FILE": str(path)}, capture_output=True, text=True, timeout=30)

        if copied.returncode != 0:
            return f"I could not copy {path.name} to attach it ({copied.stderr.strip()})."

    try:
        os.startfile(chat_link(number, message))
    except OSError:
        return "The WhatsApp app is not installed. Install it from the Microsoft Store and sign in once."

    if not path:
        return f"Opened your WhatsApp chat with {who} with the message typed in. Check it, then press Enter to send."

    for _ in range(80):
        if windows_front_title().startswith("WhatsApp"):
            break

        time.sleep(0.25)

    time.sleep(2.5)

    if windows_front_title().startswith("WhatsApp"):
        windows_paste()
        return f"Opened your WhatsApp chat with {who} and attached {path.name}. Check it, then press Enter to send."

    return f"Opened your WhatsApp chat with {who}. {path.name} is copied: press Ctrl+V to attach it, then Enter to send."

# ---- direct: the linked device sends, in its own process ----

def linked():
    if not WHATSAPP_DB.exists():
        return False

    try:
        with closing(sqlite3.connect(WHATSAPP_DB, timeout=5)) as db:
            return db.execute("SELECT count(*) FROM whatsmeow_device").fetchone()[0] > 0
    except sqlite3.Error:
        return False

def run_helper(command, request, timeout):
    # (result dict, None) or (None, why there is no result); the helper answers in a file, so
    # nothing it or the WhatsApp library prints can be mistaken for the answer
    with tempfile.TemporaryDirectory() as tmp:
        request_path = Path(tmp) / "request.json"
        result_path = Path(tmp) / "result.json"
        request_path.write_text(json.dumps({**request, "result": str(result_path)}), encoding="utf-8")
        quiet = {"capture_output": True, "text": True} if command != "link" else {}

        try:
            done = subprocess.run([sys.executable, str(HELPER), command, str(request_path)], cwd=base_dir, timeout=timeout, **quiet)
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except KeyboardInterrupt:
            return None, "cancelled"

        try:
            return json.loads(result_path.read_text(encoding="utf-8")), None
        except (OSError, ValueError):
            last = (done.stderr or "").strip().splitlines()[-1:] if command != "link" else []
            return None, last[0] if last else f"the helper stopped (exit code {done.returncode})"

def send_directly(person, path, message):
    if not linked():
        return NOT_LINKED

    who = f"{person.name} ({pretty(person.number)})" if person.source != "number" else person.name
    what = path.name if path else "the message"
    size = path.stat().st_size if path else 0
    # time to connect, then at least 100 KB a second for the upload
    timeout = min(1800, 90 + size // (100 * 1024))
    request = {"to": person.number, "file": str(path) if path else None, "message": message}
    result, failure = run_helper("send", request, timeout)

    if result is None:
        if failure == "timeout":
            return f"WhatsApp did not finish within {timeout // 60 or 1} min. {what} may or may not have arrived: look in your chat with {who} before sending again."

        return f"Nothing was confirmed as sent to {who} ({failure}). Look in the chat before trying again."

    if not result.get("ok"):
        return result.get("error", "WhatsApp gave no reason.")

    return f"Sent {what} to {who} on WhatsApp. WhatsApp's server confirmed it at {result['time']}."

def link_whatsapp():
    result, failure = run_helper("link", {}, timeout=300)

    if result and result.get("relink"):
        # WhatsApp dropped the old link; it is useless, so start over with a new code
        forget_link()
        result, failure = run_helper("link", {}, timeout=300)

    if result is None:
        return "Linking was cancelled." if failure == "cancelled" else f"Linking did not finish ({failure}). Say 'link whatsapp' to try again."

    return result.get("message") if result.get("ok") else result.get("error", "Linking failed.")
