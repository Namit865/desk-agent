from pathlib import Path
from datetime import datetime
import platform
import subprocess
import sys

base_dir = Path(__file__).parent.parent

image_path = str(base_dir / "assets" / "message_logo.jpeg")

def set_reminder(text,when):
    with open("data/reminders.txt", "a") as f:
        f.write(f"{when} | {text}\n")

    now = datetime.now()
    when_time = datetime.strptime(when, "%Y-%m-%d %H:%M:%S")

    if when_time > now:
        seconds = (when_time - now).total_seconds()
        schedule_reminder(text,seconds)

    return f"Reminder set for {when}: {text} successfully"

def show_reminder(text):
    system = platform.system()

    if system == "Windows":
        from win11toast import notify
        notify("Desk Agent", f"Reminder: {text}", icon=image_path)
    elif system == "Darwin":
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_text}" with title "Desk Agent"'
        subprocess.run(["osascript", "-e", script], check=False)
    else:
        print(f"Reminder: {text}")

    return f"Reminder shown: {text}"

def schedule_reminder(text,seconds):

    system = platform.system()
    if system == "Darwin":
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'delay {int(seconds)}\ndisplay notification "{safe_text}" with title "Desk Agent"'

        subprocess.Popen(["osascript", "-e", script],start_new_session=True)

    elif system == "Windows":
        safe_text = text.replace("\\", "\\\\").replace("'", "\\'")
        code = (
            "import time\n"
            f"time.sleep({seconds})\n"
            "from win11toast import notify\n"
            f"notify('Desk Agent', 'Reminder: {safe_text}')\n"
        )

        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen([sys.executable, "-c", code], creationflags=flags,close_fds=True)

    else:
        safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
        cmd = f"sleep {seconds}; notify-send 'Desk Agent' 'Reminder: {safe_text}' || echo 'Reminder: {safe_text}'"
        subprocess.Popen(cmd,shell=True,start_new_session=True)
