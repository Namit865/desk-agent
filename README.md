# Desk Agent

Cross-platform GenAI desk agent (macOS + Windows): you speak or type in normal language → an LLM understands the request → it calls tools (normal Python functions) on your PC → runs a small loop until that request is done → short confirmation.

Not a chatbot that only answers. Not training an LLM from scratch. The engine is general (LLM + tools). v1 tools are limited on purpose.

## Status

v1 core is working:

- Tools: `save_note`, `set_reminder`, `open_path`, `deep_research`
- Agent loop: local LLM plans JSON → registry runs tools → repeats until `done` (research returns after one call)
- Brain: **Ollama local-first** (`qwen2.5:14b`); optional **Gemini** cloud only if local fails
- CLI: `python main.py` — type a request, or `exit` to quit
- Research: `ddgs` search → fetch (skip failures) → conclude append → enough-check → final answer from conclusions
- Reminders: save to disk + schedule a **detached OS process** that notifies later (macOS `osascript` delay, Windows detached Python + `win11toast`, Linux `notify-send`) — survives quitting the agent
- Open path: Windows `os.startfile` / macOS `open` (same tool, OS branch)
- Loop hardening: bad JSON / incomplete replies / tool errors return a message instead of crashing `main.py`
- Next: optional portfolio extras (demo script, more tools)

## What it does (v1)

- Save notes → `data/notes.txt`
- Set reminders → `data/reminders.txt` + native OS notification (still fires after you quit `main.py`)
- Open a folder or file (Finder on Mac, Explorer on Windows)
- Deep research a topic → `data/research/` + history under `data/history/`

Example: *"save a note that I need to call mom and remind me at 6pm"* → note tool + reminder tool → short confirmation.

Example: *"research a PyTorch learning roadmap from math basics"* → `deep_research` → final answer in the terminal / `final_research.txt`.

## How to run

```text
pip install -r requirements.txt
```

On **macOS**, `win11toast` is Windows-only — if install fails, skip it or install the other packages individually. Reminder notifications on Mac use built-in `osascript` (no extra package).

**LLM setup**

- **Local (preferred):** install [Ollama](https://ollama.com), pull `qwen2.5:14b` (`ollama pull qwen2.5:14b`), leave Ollama running (`ollama serve` or the Ollama app).
- **Cloud (optional backup):** set `GEMINI_API_KEY` in your environment or in `.env` (see `.env.example`) if you want Gemini when local fails. Never commit `.env`. Small local models (2B/3B) are poor at JSON tool routing; 8B/14B recommended.

```text
python main.py
```

Type a normal-language request. Type `exit` to quit.

## Project layout

```
desk-agent/
  main.py          # type requests here
  agent/
    llm.py         # ask(): Gemini multi-model retry, then Ollama fallback
    loop.py        # decide → tools → history → done
  tools/
    registry.py    # tool menu + lookup
    notes.py
    reminder.py    # schedule detached OS notify (Mac / Windows / Linux)
    files.py       # open_path (Mac + Windows)
    research.py    # deep_research pipeline
  data/
    notes.txt, reminders.txt
    research/      # site_contents, conclusion, final_research (runtime)
    history/       # saved research answers (runtime)
```

## Tools (v1)

| Tool | Status | What it does |
|------|--------|--------------|
| save note | done | append text to `data/notes.txt` |
| reminder | done | store `when \| text`; detached OS process notifies later (survives quit) |
| open path | done | open a folder/file (Mac Finder / Windows Explorer) |
| deep research | done | search → fetch sites → conclusions → enough? → final answer |

## Stack

- Python
- LLM: Ollama local-first (`qwen2.5:14b`) + optional Gemini backup
- Web search: `ddgs`
- Tools: normal Python functions via a registry
- OS: macOS + Windows (notify / open path branched by platform)
