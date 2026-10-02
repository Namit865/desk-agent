import json
import logging
import mimetypes
import os
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

# The direct mode's own process: desk-agent signs in to WhatsApp as a linked device (the way
# WhatsApp Web does) and sends from your number. A crash or a hang in the WhatsApp library ends
# only this process, never the agent, which reads the answer from the result file it asked for.
#
#   python tools/whatsapp_link.py link   <request.json>   show a code to scan, then sync contacts
#   python tools/whatsapp_link.py send   <request.json>   {"to": "+91...", "file": path or null, "message": text or null}
#   python tools/whatsapp_link.py unlink [request.json]   sign desk-agent out of WhatsApp

base_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(base_dir))

from tools.contacts import DATA, WHATSAPP_DB
from tools.reminder import take_lock

LOCK_PATH = DATA / "whatsapp.lock"
LOG_PATH = DATA / "whatsapp.log"
QR_PATH = DATA / "whatsapp_qr.png"
CONNECT_WAIT = 40       # seconds to reach WhatsApp with the saved link
LINK_WAIT = 240         # seconds to scan the code on the phone
SYNC_QUIET = 8          # contacts unchanged this many seconds: the phone has sent them all
SYNC_WAIT = 90

NOT_LINKED = "desk-agent is not linked to WhatsApp. Say 'link whatsapp' and scan the code with your phone."

class Problem(Exception):
    pass

def quiet_logs():
    # the library logs to the terminal; send it to a file instead, before the library is imported,
    # so the terminal shows only the code to scan
    root = logging.getLogger()

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.WARNING)

def missing_library(error):
    if "libmagic" in str(error):
        return "Direct WhatsApp sending needs libmagic: run  brew install libmagic  (Mac) and try again."

    return f"Direct WhatsApp sending needs its library: run  pip install -r requirements.txt  ({error})."

class Session:
    # one connection to WhatsApp; connect() blocks until stop(), so it runs on its own thread
    def __init__(self, on_code):
        from neonize.client import NewClient
        from neonize.events import (ClientOutdatedEv, ConnectedEv, ConnectFailureEv, LoggedOutEv,
                                    PairStatusEv, StreamReplacedEv, TemporaryBanEv)
        from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps
        from neonize.utils.log import log as library_log

        library_log.setLevel(logging.WARNING)   # also the level the library's Go core logs at

        # the phone lists this computer as "Desk Agent" under Linked devices
        self.client = NewClient(WHATSAPP_DB.name, props=DeviceProps(os="Desk Agent", platformType=DeviceProps.DESKTOP))
        self.ready = threading.Event()      # set once connected, or once it is clear it will not be
        self.connected = False
        self.problem = None
        self.logged_out = False             # the saved link is dead: only a new code can fix it

        on = self.client.event
        on.qr(lambda _, code: on_code(self, code))
        on(ConnectedEv)(lambda *_: self.finish())
        on(PairStatusEv)(self.paired)
        on(LoggedOutEv)(self.unlinked)
        on(ConnectFailureEv)(lambda _, ev: self.finish(f"WhatsApp refused the connection: {ev.Message or ev.Reason}"))
        on(TemporaryBanEv)(lambda _, ev: self.finish(f"WhatsApp has temporarily blocked this account from linked devices (code {ev.Code}). Use ready-to-send until it lifts."))
        on(ClientOutdatedEv)(lambda *_: self.finish("WhatsApp says the library is out of date: run  pip install -U neonize"))
        on(StreamReplacedEv)(lambda *_: self.finish("another program signed in with the same link"))

        self.thread = threading.Thread(target=self.run, daemon=True)

    def paired(self, _, event):
        if event.Status != event.SUCCESS:
            self.finish(f"linking failed: {event.Error}")

    def unlinked(self, *_):
        self.logged_out = True
        self.finish("WhatsApp has unlinked desk-agent (it was removed on the phone, or not used for 14 days). Say 'link whatsapp' to link it again.")

    def run(self):
        try:
            self.client.connect()
        except Exception as e:
            self.finish(f"Could not reach WhatsApp, check the internet connection ({e})")
        else:
            self.finish("the connection closed")

    def finish(self, problem=None):
        # only the first outcome counts: a "closed" after a good connection changes nothing
        if not self.ready.is_set():
            self.connected = problem is None
            self.problem = problem
            self.ready.set()

    def start(self, wait):
        self.thread.start()
        self.ready.wait(wait)

        if not self.connected:
            self.stop()
            raise Problem(self.problem or "WhatsApp did not answer in time. Check the internet connection and try again.")

    def stop(self):
        try:
            self.client.stop()
        except Exception:
            pass

        self.thread.join(15)

def linked():
    try:
        with closing(sqlite3.connect(WHATSAPP_DB, timeout=5)) as db:
            return db.execute("SELECT count(*) FROM whatsmeow_device").fetchone()[0] > 0
    except sqlite3.Error:
        return False

def contact_count():
    try:
        with closing(sqlite3.connect(WHATSAPP_DB, timeout=5)) as db:
            return db.execute("SELECT count(*) FROM whatsmeow_contacts WHERE coalesce(full_name, '') != '' OR coalesce(first_name, '') != ''").fetchone()[0]
    except sqlite3.Error:
        return 0

def wait_for_contacts():
    # after linking, the phone sends the contact names in the background; wait until they stop coming
    started = changed = time.monotonic()
    count = contact_count()

    while time.monotonic() - started < SYNC_WAIT:
        time.sleep(1)
        now = contact_count()

        if now != count:
            count, changed = now, time.monotonic()
        elif count and time.monotonic() - changed >= SYNC_QUIET:
            break

    return count

def show_code(session, code):
    import segno

    qr = segno.make_qr(code)
    qr.save(str(QR_PATH), scale=8, border=4)
    print("\nOn your phone: WhatsApp → Settings → Linked devices → Link a device, then scan this code:\n")
    qr.terminal(compact=True)
    print(f"\nThe code changes every 20 seconds; scan the newest one. Hard to scan? Open {QR_PATH}. Ctrl+C cancels.", flush=True)

def refuse_code(session, code):
    # a code is only offered when there is no saved link
    session.logged_out = True
    session.finish(NOT_LINKED)

def link(request):
    already = linked()
    session = Session(refuse_code if already else show_code)
    print("Connecting to WhatsApp...", flush=True)

    try:
        session.start(CONNECT_WAIT if already else LINK_WAIT)
    except Problem:
        if already and session.logged_out:
            # the agent deletes the dead link once this process has let go of it, then asks again
            return {"ok": False, "relink": True, "error": session.problem}
        raise

    try:
        if not already:
            print("\nLinked. Copying your contact names from the phone (up to a minute)...", flush=True)

        count = wait_for_contacts() if not already else contact_count()

        try:
            me = "+" + session.client.get_me().JID.User
        except Exception:
            me = "your number"
    finally:
        session.stop()
        QR_PATH.unlink(missing_ok=True)

    start = "Already linked" if already else "Linked"
    return {"ok": True, "message": f"{start} to WhatsApp as {me}, with {count} contact names. Say 'send <file> to <person> directly' to send without opening WhatsApp."}

def recipient(client, number):
    # WhatsApp's own id for the number, after checking the number has WhatsApp at all
    from neonize.utils.jid import build_jid

    answered = False

    # with and without the +: "no WhatsApp" is believed only when neither form finds the number
    for query in (number, number.lstrip("+")):
        try:
            found = client.is_on_whatsapp(query)
        except Exception:
            continue

        answered = answered or bool(found)

        for answer in found:
            if answer.IsIn:
                return answer.JID

    if answered:
        raise Problem(f"{number} does not have WhatsApp.")

    # the check itself failed: the plain number id still reaches the person if they have WhatsApp
    return build_jid(number.lstrip("+"))

def send(request):
    if not linked():
        raise Problem(NOT_LINKED)

    path = Path(request["file"]) if request.get("file") else None
    message = request.get("message") or None
    session = Session(refuse_code)
    session.start(CONNECT_WAIT)

    try:
        to = recipient(session.client, request["to"])

        try:
            if path:
                kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                session.client.send_document(to, path.read_bytes(), caption=message, filename=path.name, mimetype=kind)
            else:
                session.client.send_message(to, message)
        except Exception as e:
            raise Problem(f"WhatsApp did not confirm it ({e}). Look in the chat before sending again.") from e
    finally:
        session.stop()

    # the send call returns only once WhatsApp's server has accepted the message
    return {"ok": True, "time": datetime.now().strftime("%H:%M:%S")}

def unlink(request):
    removed = "WhatsApp was not linked."

    if linked():
        session = Session(refuse_code)

        try:
            session.start(CONNECT_WAIT)
            session.client.logout()
            removed = "Unlinked: desk-agent is no longer a linked device on your WhatsApp."
        except Exception:
            removed = "Deleted the link from this computer. Also remove 'Desk Agent' on your phone: WhatsApp → Settings → Linked devices."
        finally:
            session.stop()

    forget_link()

    return {"ok": True, "message": removed}

def forget_link():
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{WHATSAPP_DB}{suffix}").unlink(missing_ok=True)

COMMANDS = {"link": link, "send": send, "unlink": unlink}

def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    request_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    request = json.loads(request_path.read_text(encoding="utf-8")) if request_path else {}

    DATA.mkdir(exist_ok=True)
    os.chdir(DATA)      # the library names its database relative to the working folder
    lock = take_lock(LOCK_PATH)

    if command not in COMMANDS:
        result = {"ok": False, "error": f"unknown command {command!r}; use link, send or unlink"}
    elif lock is None:
        result = {"ok": False, "error": "WhatsApp is busy with another desk-agent request. Try again in a minute."}
    else:
        quiet_logs()

        try:
            result = COMMANDS[command](request)
        except Problem as e:
            result = {"ok": False, "error": str(e)}
        except ImportError as e:
            result = {"ok": False, "error": missing_library(e)}
        except Exception as e:
            logging.exception("whatsapp %s failed", command)
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if request.get("result"):
        # write, then rename: the agent never reads half an answer
        answer = Path(request["result"])
        answer.with_suffix(".tmp").write_text(json.dumps(result), encoding="utf-8")
        os.replace(answer.with_suffix(".tmp"), answer)
    else:
        print(result.get("message") or result.get("error"))

    sys.stdout.flush()
    # the library's threads may outlive the connection; the answer is written, so leave now
    os._exit(0 if result.get("ok") else 1)

if __name__ == "__main__":
    main()
