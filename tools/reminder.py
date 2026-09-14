from pathlib import Path
from datetime import datetime
import platform
import subprocess
import time
import threading

base_dir = Path(__file__).parent.parent

image_path = str(base_dir / "assets" / "message_logo.jpeg")

def set_reminder(text,when):
    with open("data/reminders.txt", "a") as f:
        f.write(f"{when} | {text}\n")

    now = datetime.now()
    when_time = datetime.strptime(when, "%Y-%m-%d %H:%M:%S")

    if when_time > now:
        seconds = (when_time - now).total_seconds()

        def wait_and_show():
            time.sleep(seconds)
            show_reminder(text)

        threading.Thread(target=wait_and_show,daemon=True).start()

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

if __name__ == "__main__":
    print(set_reminder("Test: Remember to buy milk","2026-09-11 17:30:00"))
