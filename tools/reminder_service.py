import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# The background service: started at login (or by the agent), it shows reminders when
# they fall due. Run on its own, so it finds the project itself instead of relying on
# whatever folder it was started from.
base_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(base_dir))
os.chdir(base_dir)

from tools.reminder import LOG_PATH, due_reminders, mark_shown, show_reminder, take_lock

CHECK_EVERY = 5                 # seconds between looks at the database
LATE = timedelta(minutes=2)     # later than this, the computer was off or asleep: say "missed"

def log(message):
    with open(LOG_PATH, "a", encoding="utf-8") as file:
        file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")

def main():
    lock = take_lock()

    if lock is None:
        return  # another copy is already running

    log("reminder service started")

    while True:
        now = datetime.now()

        for rid, due, text in due_reminders(now):
            message = f"Missed at {due:%H:%M on %d %b}: {text}" if now - due > LATE else text

            # show first, then mark: a crash in between repeats a reminder instead of losing it
            try:
                show_reminder(message)
                log(f"shown #{rid}: {message}")
            except Exception as e:
                log(f"could not show #{rid} ({e}): {message}")

            mark_shown(rid, now)

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    main()
