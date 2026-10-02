# Desk Agent

Cross-platform GenAI desk agent (macOS + Windows + Linux where noted): you speak or type in normal language → an LLM understands the request → it calls tools (normal Python functions) on your PC → runs a small loop until that request is done → short confirmation. It can also answer normal chat when no tool is needed.

Not a chatbot that only answers. Not training an LLM from scratch. The engine is general (LLM + tools). v1 tools are limited on purpose.

## Status

v1 core is working:

- Tools: `save_note`, `read_notes`, `set_reminder`, `list_reminders`, `open_file`, `deep_research`, `pdf_to_word`, `summarize_document`, `convert_file`, `send_whatsapp`, `save_contact`, `link_whatsapp`
- Agent loop: LLM plans JSON → registry runs tools → repeats until `done` (research returns after one call)
- Brain: **Groq first** → **Ollama** (`qwen2.5:14b`) if Groq fails; Gemini helpers remain in code but are not in the default `ask()` chain
- CLI: `python main.py` — type a request, or `exit` to quit
- Chat: if no tool is needed, router returns `done` with a normal helpful answer (not tools-only)
- Voice: type `listen` for mic mode (Groq Whisper, English-locked → same `run` loop); say `text` to return to keyboard; Piper TTS (loaded once) speaks every reply and Enter skips it mid-sentence; silence times out instead of crashing
- Research: `ddgs` search → fetch (skip failures) → conclude append → enough-check → final answer from conclusions
- Reminders: saved in a small SQLite database (`data/reminders.db`); one **background service** (`tools/reminder_service.py`) checks it every 5 seconds and shows what is due. The service starts at login (macOS launchd agent, Windows Run entry, Linux autostart), so reminders **survive restarts**; one that fell due while the computer was off shows as `Missed at 18:00 on 30 Sep: …`. One copy runs at a time (OS file lock); `data/reminders.log` records what was shown. An old `data/reminders.txt` is imported once and kept as `reminders.txt.imported`
- Open by name: say `open downloads` or `open desk-agent` — no paths. Resolves in layers: known places (`Downloads`, `Desktop`, …) → literal path → search. Search uses Spotlight on macOS and a depth-capped walk of the home folder elsewhere (also the macOS fallback when Spotlight finds nothing). Results are ranked by exact name, then shallowest, then most recent; two folders with the same name means it lists them and asks instead of guessing
- Loop hardening: bad JSON / incomplete replies / tool errors return a message instead of crashing `main.py`; a tool call missing a parameter (e.g. `when`) is refused before it runs
- PDF → Word: finds the PDF by name, OCRs scanned PDFs (`ocrmypdf` + Tesseract), then **measures** the result: words in the PDF vs words in the Word file → `99.5% text match, 4 of 555 words changed`. Never overwrites an existing `.docx`. Two layouts, picked from the page (or say which):
  - **Flowing text** (letters, reports, papers): editable text that reflows. `pdf2docx`, or **Microsoft Word itself on Windows** when Word is installed (falls back to `pdf2docx` if Word fails or keeps less text). Shadows are removed first: Word has no soft masks, so they turned into black boxes and doubled words
  - **Exact layout** (designed pages: resumes, flyers, letterheads, text on coloured panels): a picture of the page design (shadows, photos, icons as they look) behind editable text boxes placed where each line was, so nothing drifts away from its text. Letter spacing keeps every line exactly as wide as in the PDF even when a stand-in font is used, so centred lines and their shadows still line up
  - **Letters the PDF does not name**: some PDFs draw "Th" or "fi" as one joined glyph without saying which letters it stands for, so Pages showed `?e Deputy Engineer`. OCR (Tesseract) reads the whole word, the known letters give away the missing ones, and the PDF copy being converted gets the missing entry
  - **Fonts**: fonts not installed on this computer (including Office's Calibri / Cambria from the Word template) are swapped for an installed font of the same kind, so Pages stops reporting missing fonts
- Summaries: PDF / Word / text files; long files are split into parts, each part summarized, then one summary from the parts (map → reduce)
- Recall: ask "what are my notes?" or "any reminders today?" and the router answers from the saved files
- File conversion, any format to any other it can reach: each converter is one step (png → jpg, jpg → pdf, pdf → docx, mp4 → mp3 …) and a breadth-first search chains steps, so `heic → docx` runs `heic → pdf → docx` with OCR. A failing converter is set aside and the next shortest chain is tried. Missing programs are named (`needs ffmpeg (brew install ffmpeg)`)
- WhatsApp, from your own number: *"send the pdf abc to rahul on whatsapp"*. The file is found by name like every other tool, the person by name from three places: contacts you saved (`save contact rahul +91 98765 43210`), your phone's contacts that WhatsApp syncs to desk-agent once it is linked, and the Mac's Contacts app. Two modes:
  - **Ready to send** (default): opens the chat in the WhatsApp app with the file attached, and stops. You check it and press Enter, so nothing goes out that you did not see. Uses the official app only
  - **Direct** (say *"send it directly"*): desk-agent is a linked device on your WhatsApp, like WhatsApp Web, and sends by itself. The reply comes only from WhatsApp's server confirming it (`WhatsApp's server confirmed it at 14:03:09`); a timeout says the file *may or may not* have arrived instead of guessing. Stricter on purpose: the file name must match exactly and `abc.pdf` next to `abc.docx` makes it ask which one. Runs in its own process (`tools/whatsapp_link.py`), so a crash in the WhatsApp library cannot take the agent down
- WhatsApp remembers who you mean. A name that fits two people gets a numbered question, and the answer alone finishes the send: `1`, `the first one`, `dusra`, `harsh patel`, a number, or `cancel` (for 5 minutes; anything else is a new request). Each send is remembered as *words said → number* in `data/contact_history.json`; once you pick the same person twice for the same words (`harsh bhai` and `harsh` count as the same words), it stops asking and says `I picked Harsh Patel because you chose them for 'harsh bhai' before`, with the numbers to switch. A different pick makes it ask again until the new habit is picked twice. Words like *bhai, ben, didi, ji, sir, uncle* are ignored when nobody is saved with them, but words for relatives (*bhabhi, mama*) are not, because they name someone else. When one person has a landline and a mobile, the landline is dropped: WhatsApp needs a mobile number
- Next: daily use, then a router test set, conversation memory, or a non-CLI front door



## What it does (v1)

- Save notes → `data/notes.txt`
- Set reminders → `data/reminders.db` + native OS notification (fires after you quit `main.py`, and after a restart)
- Open a folder by name, not by path (Spotlight on macOS, home-folder walk on Windows/Linux)
- Deep research a topic → `data/research/` + history under `data/history/`
- Convert a PDF to an editable Word file next to it (`report.pdf` → `report.docx`)
- Summarize a PDF, Word, text or markdown file → answer in the terminal + `data/summaries/`
- Read back notes and upcoming reminders
- Convert files between 40 formats: pictures (png, jpg, webp, heic, gif, bmp, tiff, ico), pdf, documents (docx, doc, odt, rtf, md, txt, html, pages, epub), data (csv, xlsx, json, xls, ods, numbers), slides (pptx, ppt, odp, key), audio (mp3, wav, m4a, aac, flac, ogg, opus) and video (mp4, mov, mkv, webm, avi)
- Send a file or a message to someone on WhatsApp, from your own number (ready to send, or directly)
- Normal questions / chat → answered in the terminal without forcing a tool

Example: *"save a note that I need to call mom and remind me at 6pm"* → note tool + reminder tool → short confirmation.

Example: *"research a PyTorch learning roadmap from math basics"* → `deep_research` → final answer in the terminal / `final_research.txt`.

Example: *"convert my resume pdf to word"* → `pdf_to_word` → `Converted resume.pdf to ~/Downloads/resume.docx (exact layout, 2 pages, 100.0% text match, 0 of 612 words changed)`.

Example: *"convert report.pdf to word, I want to edit it"* → `pdf_to_word` with `layout: flow` → reflowing text.

Example: *"summarize the quarterly report"* → `summarize_document` → summary in the terminal.

Example: *"convert logo to jpg"* → `convert_file` → `Converted logo.png to ~/Pictures/logo.jpg (png -> jpg). Note: transparent parts filled with white, JPG has no transparency`.

Example: *"turn IMG_0042 into a word file"* → `convert_file` → `heic -> pdf -> docx`, the text in the photo read with OCR.

Example: *"send the pdf named abc to rahul on whatsapp"* → `send_whatsapp` → `Opened your WhatsApp chat with Rahul Sharma (+91 98765 43210) and attached abc.pdf. Check it, then press Enter to send.`

Example: *"send abc pdf to rahul directly"* → `send_whatsapp` with `direct` → `Sent abc.pdf to Rahul Sharma (+91 98765 43210) on WhatsApp. WhatsApp's server confirmed it at 14:03:09.`

Example: *"what is quantization?"* → chat-style `done` answer (no tool).

## How to run

```text
pip install -r requirements.txt
```

Windows-only packages (`win11toast`, `pywin32`) are marked in `requirements.txt`, so pip skips them on macOS and Linux. Reminder notifications on Mac use built-in `osascript` (no extra package).

**File conversion extras (optional):** audio and video need ffmpeg (`brew install ffmpeg` / `winget install ffmpeg`); `.pages`, `.numbers` and `.key` files, and Office files to PDF on a Mac, use Pages / Numbers / Keynote (macOS asks once whether the terminal may control them); LibreOffice (libreoffice.org) adds doc, odt, rtf, xls, ods, ppt, odp on any system; Microsoft Word is used on Windows. Without them the rest still works, and docx → pdf falls back to docx → md → html → pdf (text, headings, lists and tables; no pictures).

**Microsoft Word on Windows:** flowing PDF → Word conversions use Word's own PDF reader when Word 2016 or newer is installed. The agent opens a separate hidden Word, and sets Word's per-user `DisableConvertPdfWarning` option so Word's "Word will now convert your PDF" question cannot stall it. Word for Mac has no PDF converter and Pages cannot open PDFs, so macOS uses `pdf2docx` / exact layout; the `.docx` opens in Word or Pages.

**Scanned PDFs (OCR)** need the Tesseract program as well as the `ocrmypdf` package: `brew install tesseract` on macOS, the [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) on Windows, `apt install tesseract-ocr` on Linux. Without it, typed PDFs still convert; scanned pages stay as images and the reply says why. For other languages install their Tesseract data and change `OCR_LANGUAGE` in `tools/documents.py` (e.g. `"eng+hin"`).

**WhatsApp**

- *Ready to send* needs only the WhatsApp app, signed in (Mac App Store / Microsoft Store). On a Mac, to let desk-agent attach the file for you, allow the app you run it in (Terminal, iTerm or VS Code) once in System Settings → Privacy & Security → Accessibility; without that it opens the chat, copies the file, and you press ⌘V. macOS also asks once whether it may control System Events and Contacts. On Linux, which has no WhatsApp app, it opens WhatsApp Web and you attach the file.
- *Direct* needs, on a Mac, `brew install libmagic` (the `neonize` package reads file types with it). Then say **link whatsapp**: a code appears in the terminal (also saved as `data/whatsapp_qr.png`); scan it with WhatsApp → Settings → Linked devices → Link a device. Your phone then lists "Desk Agent" there, and desk-agent copies your contact names (`Linked to WhatsApp as +91…, with 312 contact names`). To stop, remove "Desk Agent" on the phone, or run `python tools/whatsapp_link.py unlink`.
- Know what direct means: WhatsApp offers no official way for a program to send from a personal number, so this uses an unofficial library (`neonize`, built on `whatsmeow`) that talks to WhatsApp the way WhatsApp Web does. It is against WhatsApp's terms; WhatsApp can break it with an update (`pip install -U neonize` usually fixes it) and, rarely, restrict an account that automates. Sending your own files to your own contacts now and then is the lowest-risk use; never use it for bulk messages. WhatsApp also drops a linked device the phone has not seen for 14 days: say **link whatsapp** again.
- `data/whatsapp.db` is the link itself: whoever has that file can send messages as you. It is git-ignored and stays on this computer; keep it that way.
- Phone numbers saved without a country code use your computer's region (System Settings → General → Language & Region). Set `WHATSAPP_REGION=IN` (or your country code) in `.env` to choose it yourself.

**LLM setup**

- **Groq (preferred):** set `GROQ_API_KEY` in `.env` (see `.env.example`). Never commit `.env`.
- **Local fallback:** install [Ollama](https://ollama.com), pull `qwen2.5:14b`, leave Ollama running.
- **Gemini (optional / unused in default ask):** set `GEMINI_API_KEY` only if you wire it back into `ask()` later.

```text
python main.py
```

Type a normal-language request. Type `listen` for voice mode, say `text` to switch back. Type `exit` to quit. Agent replies are printed and spoken (Piper).

## Project layout

```
desk-agent/
  main.py          # type requests here
  agent/
    llm.py         # ask(): Groq → Ollama (Gemini code present, not default)
    loop.py        # decide → tools → history → done
  tools/
    registry.py    # tool menu + lookup
    notes.py
    reminder.py    # reminders database, start-at-login entry (Mac / Windows / Linux)
    reminder_service.py # background service: shows reminders when due, catches up after a restart
    files.py       # open_file + resolve_file: name → known places / path / Spotlight search
    documents.py   # pdf_to_word (OCR, layout choice, Word on Windows, text match) and summarize_document
    pdf_clean.py   # PDF copy without shadows / see-through duplicate text, unnamed glyphs named via OCR
    fonts.py       # font names, installed-font lookup and swaps, width measuring
    convert.py     # convert_file: finds the file, searches the shortest converter chain, runs it
    converters.py  # every direct conversion (Pillow, PyMuPDF, python-docx, openpyxl, ffmpeg, office apps)
    exact_layout.py # exact layout .docx: page design picture + positioned text boxes
    contacts.py    # who "rahul" is: saved contacts, WhatsApp's synced contacts, the Mac Contacts app
    whatsapp.py    # send_whatsapp: ready to send (WhatsApp app, you press Enter) or direct; link_whatsapp
    whatsapp_link.py # direct mode's own process: linked device (neonize), link / send / unlink
    research.py    # deep_research pipeline
    voice.py       # listen_once (Groq Whisper) + speak (Piper TTS)
  assets/
    voices/        # Piper .onnx voice files (local; usually gitignored)
  data/
    notes.txt, reminders.db, reminders.log   # personal, kept out of git
    contacts.json, contact_history.json, whatsapp.db   # saved numbers, who each name meant, the WhatsApp link; kept out of git
    research/      # site_contents, conclusion, final_research (runtime)
    summaries/     # last summary per file (runtime)
    history/       # saved research answers (runtime)
```



## Tools (v1)


| Tool          | Status | What it does                                                            |
| ------------- | ------ | ----------------------------------------------------------------------- |
| save note     | done   | append text to `data/notes.txt`                                         |
| reminder      | done   | saved in SQLite; background service shows it, survives restarts         |
| open file     | done   | open a folder by name; asks when the name is ambiguous                  |
| deep research | done   | search → fetch sites → conclusions → enough? → final answer             |
| read notes    | done   | read back `data/notes.txt`, router answers from it                      |
| list reminders| done   | reminders not yet shown, sorted; overdue ones marked                     |
| pdf to word   | done   | find PDF → OCR if scanned → remove shadows → flowing or exact layout → text match |
| summarize     | done   | find file → split into parts → notes per part → one summary             |
| convert file  | done   | find file → shortest converter chain (breadth-first search) → run it    |
| send whatsapp | done   | find file + person → WhatsApp app with the file attached, or send directly as a linked device |
| save contact  | done   | name → phone number (any country's format) in `data/contacts.json`      |
| link whatsapp | done   | code to scan → desk-agent becomes a linked device → contact names synced |




## Stack

- Python
- LLM: Groq (primary) + Ollama local fallback (`qwen2.5:14b`)
- Web search: `ddgs`
- Conversion: Pillow + `pillow-heif` (pictures), `openpyxl` (Excel), `markdown`, ffmpeg (audio / video), office apps when present
- Documents: `pdf2docx` + PyMuPDF (PDF → Word), `pikepdf` (shadow cleanup), `ocrmypdf` + Tesseract (OCR), `python-docx` (Word files), Microsoft Word via `pywin32` on Windows
- WhatsApp: the official app (ready to send); `neonize` linked device (direct); `phonenumbers` for numbers
- Tools: normal Python functions via a registry
- OS: macOS + Windows + Linux (notify / open path branched by platform)

