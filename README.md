# Desk Agent

Cross-platform GenAI desk agent (macOS + Windows + Linux where noted): you speak or type in normal language → an LLM understands the request → it calls tools (normal Python functions) on your PC → runs a small loop until that request is done → short confirmation. It can also answer normal chat when no tool is needed.

Not a chatbot that only answers. Not training an LLM from scratch. The engine is general (LLM + tools). v1 tools are limited on purpose.

## Status

v1 core is working:

- Tools: `save_note`, `read_notes`, `set_reminder`, `list_reminders`, `open_file`, `deep_research`, `pdf_to_word`, `summarize_document`
- Agent loop: LLM plans JSON → registry runs tools → repeats until `done` (research returns after one call)
- Brain: **Groq first** → **Ollama** (`qwen2.5:14b`) if Groq fails; Gemini helpers remain in code but are not in the default `ask()` chain
- CLI: `python main.py` — type a request, or `exit` to quit
- Chat: if no tool is needed, router returns `done` with a normal helpful answer (not tools-only)
- Voice: type `listen` for mic mode (Groq Whisper, English-locked → same `run` loop); say `text` to return to keyboard; Piper TTS (loaded once) speaks every reply and Enter skips it mid-sentence; silence times out instead of crashing
- Research: `ddgs` search → fetch (skip failures) → conclude append → enough-check → final answer from conclusions
- Reminders: save to disk + schedule a **detached OS process** that notifies later (macOS `osascript` delay, Windows detached Python + `win11toast`, Linux `notify-send`) — survives quitting the agent
- Open by name: say `open downloads` or `open desk-agent` — no paths. Resolves in layers: known places (`Downloads`, `Desktop`, …) → literal path → search. Search uses Spotlight on macOS and a depth-capped walk of the home folder elsewhere (also the macOS fallback when Spotlight finds nothing). Results are ranked by exact name, then shallowest, then most recent; two folders with the same name means it lists them and asks instead of guessing
- Loop hardening: bad JSON / incomplete replies / tool errors return a message instead of crashing `main.py`; a tool call missing a parameter (e.g. `when`) is refused before it runs
- PDF → Word: finds the PDF by name, converts with `pdf2docx`, OCRs scanned PDFs (`ocrmypdf` + Tesseract), then **measures** the result: words in the PDF vs words in the Word file → `99.5% text match, 4 of 555 words changed`. Never overwrites an existing `.docx`
- Summaries: PDF / Word / text files; long files are split into parts, each part summarized, then one summary from the parts (map → reduce)
- Recall: ask "what are my notes?" or "any reminders today?" and the router answers from the saved files
- Next: daily use, then reboot-safe reminders or a non-CLI front door



## What it does (v1)

- Save notes → `data/notes.txt`
- Set reminders → `data/reminders.txt` + native OS notification (still fires after you quit `main.py`)
- Open a folder by name, not by path (Spotlight on macOS, home-folder walk on Windows/Linux)
- Deep research a topic → `data/research/` + history under `data/history/`
- Convert a PDF to an editable Word file next to it (`report.pdf` → `report.docx`)
- Summarize a PDF, Word, text or markdown file → answer in the terminal + `data/summaries/`
- Read back notes and upcoming reminders
- Normal questions / chat → answered in the terminal without forcing a tool

Example: *"save a note that I need to call mom and remind me at 6pm"* → note tool + reminder tool → short confirmation.

Example: *"research a PyTorch learning roadmap from math basics"* → `deep_research` → final answer in the terminal / `final_research.txt`.

Example: *"convert my resume pdf to word"* → `pdf_to_word` → `Converted resume.pdf to ~/Downloads/resume.docx (2 pages, 100.0% text match, 0 of 612 words changed)`.

Example: *"summarize the quarterly report"* → `summarize_document` → summary in the terminal.

Example: *"what is quantization?"* → chat-style `done` answer (no tool).

## How to run

```text
pip install -r requirements.txt
```

On **macOS**, `win11toast` is Windows-only — if install fails, skip it or install the other packages individually. Reminder notifications on Mac use built-in `osascript` (no extra package).

**Scanned PDFs (OCR)** need the Tesseract program as well as the `ocrmypdf` package: `brew install tesseract` on macOS, the [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) on Windows, `apt install tesseract-ocr` on Linux. Without it, typed PDFs still convert; scanned pages stay as images and the reply says why. For other languages install their Tesseract data and change `OCR_LANGUAGE` in `tools/documents.py` (e.g. `"eng+hin"`).

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
    reminder.py    # schedule detached OS notify (Mac / Windows / Linux)
    files.py       # open_file + resolve_file: name → known places / path / Spotlight search
    documents.py   # pdf_to_word (OCR + text match) and summarize_document
    research.py    # deep_research pipeline
    voice.py       # listen_once (Groq Whisper) + speak (Piper TTS)
  assets/
    voices/        # Piper .onnx voice files (local; usually gitignored)
  data/
    notes.txt, reminders.txt
    research/      # site_contents, conclusion, final_research (runtime)
    summaries/     # last summary per file (runtime)
    history/       # saved research answers (runtime)
```



## Tools (v1)


| Tool          | Status | What it does                                                            |
| ------------- | ------ | ----------------------------------------------------------------------- |
| save note     | done   | append text to `data/notes.txt`                                         |
| reminder      | done   | store `when \| text`; detached OS process notifies later (survives quit) |
| open file     | done   | open a folder by name; asks when the name is ambiguous                  |
| deep research | done   | search → fetch sites → conclusions → enough? → final answer             |
| read notes    | done   | read back `data/notes.txt`, router answers from it                      |
| list reminders| done   | upcoming reminders from `data/reminders.txt`, sorted, duplicates dropped |
| pdf to word   | done   | find PDF → OCR if scanned → `pdf2docx` → text match score               |
| summarize     | done   | find file → split into parts → notes per part → one summary             |




## Stack

- Python
- LLM: Groq (primary) + Ollama local fallback (`qwen2.5:14b`)
- Web search: `ddgs`
- Documents: `pdf2docx` + PyMuPDF (PDF → Word), `ocrmypdf` + Tesseract (OCR), `python-docx` (read Word)
- Tools: normal Python functions via a registry
- OS: macOS + Windows + Linux (notify / open path branched by platform)

