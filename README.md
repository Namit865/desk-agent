# Desk Agent

Windows GenAI desk agent: you speak or type in normal language → an LLM understands the request → it calls tools (normal Python functions) on your PC → runs a small loop until that request is done → short confirmation.

Not a chatbot that only answers. Not training an LLM from scratch. The engine is general (LLM + tools). v1 tools are limited on purpose.

## Status

v1 core is working:

- Tools: `save_note`, `set_reminder`, `open_path`, `deep_research`
- Agent loop: Gemini/Ollama plans JSON → registry runs tools → repeats until `done` (research returns after one call)
- Brain: try **Gemini** first (several model IDs, retry on quota/errors); on failure fall back to **Ollama** (`llama3.2` local)
- CLI: `python main.py` — type a request, or `exit` to quit
- Research: `ddgs` search → fetch (skip failures) → conclude append → enough-check → final answer from conclusions
- Next: better Windows reminders; harden bad JSON / mid-loop errors

## What it does (v1)

- Save notes → `data/notes.txt`
- Set reminders → `data/reminders.txt`
- Open a folder or file on Windows
- Deep research a topic → `data/research/` + history under `data/history/`

Example: *"save a note that I need to call mom and remind me at 6pm"* → note tool + reminder tool → short confirmation.

Example: *"research a PyTorch learning roadmap from math basics"* → `deep_research` → final answer in the terminal / `final_research.txt`.

## How to run

```text
pip install -r requirements.txt
```

**LLM setup**

- **Cloud (preferred):** set Windows env var `GEMINI_API_KEY` (or put it in `.env` — see `.env.example`). Never commit `.env`.
- **Local fallback:** install [Ollama](https://ollama.com), pull a model (`ollama pull llama3.2`), leave Ollama running. Used automatically if Gemini fails.

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
    reminder.py
    files.py       # open_path
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
| reminder | done | store `when \| text` in `data/reminders.txt` |
| open path | done | open a folder/file on Windows |
| deep research | done | search → fetch sites → conclusions → enough? → final answer |

## Stack

- Python
- LLM: Gemini API (primary, multi-model retry) + Ollama local (fallback)
- Web search: `ddgs`
- Tools: normal Python functions via a registry
