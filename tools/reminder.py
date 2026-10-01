from pathlib import Path
from contextlib import closing
from datetime import datetime, timedelta
import os
import platform
import plistlib
import shutil
import sqlite3
import subprocess
import sys

base_dir = Path(__file__).parent.parent

image_path = str(base_dir / "assets" / "message_logo.jpeg")

# Reminders live in a small database and one background service shows them when they are
# due. The service starts at login, so a reminder still shows after a restart, and one that
# fell due while the computer was off shows as missed when it is back on.

DATA = base_dir / "data"
DB_PATH = DATA / "reminders.db"
OLD_LIST = DATA / "reminders.txt"
LOCK_PATH = DATA / "reminder_service.lock"
LOG_PATH = DATA / "reminders.log"
SERVICE = base_dir / "tools" / "reminder_service.py"
LABEL = "com.deskagent.reminders"
FORMAT = "%Y-%m-%d %H:%M:%S"
GRACE = timedelta(minutes=1)    # a "when" this close to now still counts as now

def connect():
    DATA.mkdir(exist_ok=True)
    # timeout: the agent and the service may write at the same moment; wait instead of failing
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY, due TEXT NOT NULL, text TEXT NOT NULL, shown TEXT)")
    import_old_list(db)
    return db

def import_old_list(db):
    # reminders saved before the database: past ones were already scheduled the old way
    if not OLD_LIST.exists():
        return

    with db:
        # one process imports; the other waits here, then finds the file gone
        db.execute("BEGIN IMMEDIATE")

        if not OLD_LIST.exists():
            return

        now = datetime.now()
        rows = set()  # a set drops lines saved twice

        for line in OLD_LIST.read_text(encoding="utf-8", errors="replace").splitlines():
            when, sep, text = line.strip().partition(" | ")

            try:
                due = datetime.strptime(when, FORMAT)
            except ValueError:
                continue

            if sep:
                rows.add((when, text, when if due < now else None))

        db.executemany("INSERT INTO reminders (due, text, shown) VALUES (?, ?, ?)", sorted(rows))
        OLD_LIST.rename(OLD_LIST.with_name("reminders.txt.imported"))

def set_reminder(text,when):
    due = datetime.strptime(when, FORMAT)

    if due < datetime.now() - GRACE:
        return f"{when} has already passed, tell me a time in the future"

    with closing(connect()) as db, db:
        db.execute("INSERT INTO reminders (due, text) VALUES (?, ?)", (due.strftime(FORMAT), text))

    keep_service_running()

    return f"Reminder set for {when}: {text} successfully"

def pending():
    with closing(connect()) as db:
        rows = db.execute("SELECT id, due, text FROM reminders WHERE shown IS NULL ORDER BY due").fetchall()

    return [(rid, datetime.strptime(due, FORMAT), text) for rid, due, text in rows]

def list_reminders():
    upcoming = pending()

    if not upcoming:
        return "No upcoming reminders"

    now = datetime.now()

    return "Upcoming reminders:\n" + "\n".join(f"- {due:%Y-%m-%d %H:%M} | {text}" + (" (overdue)" if due < now else "") for _, due, text in upcoming)

def due_reminders(now):
    return [row for row in pending() if row[1] <= now]

def mark_shown(rid, when):
    with closing(connect()) as db, db:
        db.execute("UPDATE reminders SET shown = ? WHERE id = ?", (when.strftime(FORMAT), rid))

def show_reminder(text):
    system = platform.system()

    if system == "Windows":
        from win11toast import notify
        notify("Desk Agent", f"Reminder: {text}", icon=image_path)
    elif system == "Darwin":
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_text}" with title "Desk Agent"'
        subprocess.run(["osascript", "-e", script], check=False)
    elif shutil.which("notify-send"):
        # an argument list, no shell: quotes in the text cannot break the command
        subprocess.run(["notify-send", "Desk Agent", f"Reminder: {text}"], check=False)
    else:
        print(f"Reminder: {text}")

    return f"Reminder shown: {text}"

def take_lock():
    # an open file the OS locks for this process; the OS lets go when the process ends, even on a crash
    DATA.mkdir(exist_ok=True)
    handle = open(LOCK_PATH, "a+")

    try:
        if platform.system() == "Windows":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None

    return handle

def service_running():
    handle = take_lock()

    if handle is None:
        return True

    handle.close()
    return False

def windowless_python():
    # pythonw.exe runs without opening a console window on Windows
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw) if pythonw.exists() else sys.executable

def start_service():
    if service_running():
        return

    log = open(LOG_PATH, "a")

    if platform.system() == "Windows":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen([windowless_python(), str(SERVICE)], cwd=base_dir, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags, close_fds=True)
    else:
        # its own session: closing the terminal does not take it down
        subprocess.Popen([sys.executable, str(SERVICE)], cwd=base_dir, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)

    log.close()

def start_at_login():
    system = platform.system()

    if system == "Darwin":
        # a launchd agent: macOS starts it at every login, and again if it crashes
        plist = plistlib.dumps({
            "Label": LABEL,
            "ProgramArguments": [sys.executable, str(SERVICE)],
            "WorkingDirectory": str(base_dir),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "StandardOutPath": str(LOG_PATH),
            "StandardErrorPath": str(LOG_PATH),
        })
        path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

        if path.exists() and path.read_bytes() == plist:
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plist)
        # load it into this login too; bootout first in case an older version is loaded
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)

    elif system == "Windows":
        import winreg

        command = f'"{windowless_python()}" "{SERVICE}"'

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.SetValueEx(key, "DeskAgentReminders", 0, winreg.REG_SZ, command)

    else:
        path = Path.home() / ".config" / "autostart" / "desk-agent-reminders.desktop"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[Desktop Entry]\nType=Application\nName=Desk Agent reminders\nExec="{sys.executable}" "{SERVICE}"\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n')

def keep_service_running():
    try:
        start_at_login()
    except Exception as e:
        print(f"Could not set reminders to start at login: {e}")

    start_service()
