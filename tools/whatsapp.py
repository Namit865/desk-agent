from pathlib import Path
from contextlib import closing
from urllib.parse import quote
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import webbrowser

from tools.contacts import WHATSAPP_DB, find_person, pretty
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

def find_file(name, direct):
    # (path, None) or (None, message); direct sending accepts only a file that is clearly the one meant
    said = name.strip()
    typed = Path(said).suffix.lower()

    # a full path is the file itself; a bare name is searched for, never matched against the current folder
    if Path(said).expanduser().is_absolute() and Path(said).expanduser().is_file():
        return Path(said).expanduser(), None

    kept = []
    extensions = []

    for word in said.split():
        kind = KIND_WORDS.get(word.lower().strip(".,!?"))

        if kind and len(said.split()) > 1:
            extensions += [ext for ext in kind if ext not in extensions]
        else:
            kept.append(word)

    if typed in ANY_TYPE:
        extensions = [ext for ext in ANY_TYPE if ext == typed or {ext, typed} == {".jpg", ".jpeg"}]

    extensions = extensions or SEND_TYPES
    name = " ".join(kept)
    path, message = resolve_file(name, extensions)

    if path is None or not direct:
        return path, message

    if name_key(path.stem) != clean_file_name(name, extensions):
        return None, f"The closest file is {path}. To send it directly, say its full name ({path.stem})."

    twins = [other for other in path.parent.iterdir() if other != path and other.stem == path.stem and other.suffix.lower() in extensions]

    if twins:
        options = ", ".join(other.name for other in [path] + twins)
        return None, f"There are {options}. Which one? Say it with its type, like {path.name}."

    return path, None

def chat_link(number, message, web=False):
    digits = number.lstrip("+")
    text = f"&text={quote(message)}" if message else ""

    return f"https://web.whatsapp.com/send?phone={digits}{text}" if web else f"whatsapp://send?phone={digits}{text}"

def send_whatsapp(file, to, message=None, direct=False):
    direct = direct is True or str(direct).strip().lower() in ("true", "yes", "1")
    message = str(message or "").strip() or None
    file = str(file or "").strip()

    if not file and not message:
        return "Tell me which file, or what message, to send."

    path = None

    if file:
        path, question = find_file(file, direct)

        if path is None:
            return question

        if path.stat().st_size > MAX_SIZE:
            return f"{path.name} is larger than 2 GB, which is the most WhatsApp takes."

    person, question = find_person(to)

    if person is None:
        return question

    if direct:
        return send_directly(person, path, message)

    return open_ready_to_send(person, path, message)

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
