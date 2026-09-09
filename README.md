# Desk Agent

Windows GenAI desk agent: you speak or type in normal language → an LLM understands the request → it calls tools (normal Python functions) on your PC → runs a small loop until that request is done → short confirmation.

Not a chatbot that only answers. Not training an LLM from scratch. The engine is general (LLM + tools). v1 tools are limited on purpose.

## Status

- Folders in place: `agent/`, `tools/`, `data/`
- First tool works: `save_note` appends to `data/notes.txt`
- Tool registry works: look up by name → call the real function
- Fake brain path works: `agent/loop.py` → registry → save note (`217c01b`)
- Gemini door works: `agent/llm.py` `ask()` returns text
- Gemini router works: `decide()` → JSON plan → registry → `save_note`
- Harden done: unknown / missing tool returns a message (no crash)
- Reminder tool works: `set_reminder` → `data/reminders.txt` (`30b1a80`)
- `run` calls tools with the right args (note vs reminder)
- Multi-step loop works: one request → note + remind → `"done"` confirmation
- Next: `open_path` tool (open folder/file on Windows)



## What it will do (v1)

- Save notes
- Set reminders
- Open a folder or path on Windows

Example: *"save a note that I need to call mom tomorrow and remind me at 6pm"* → note tool + reminder tool → done.

## How to run

```text
pip install -r requirements.txt
```

Put your key in `.env` as `GEMINI_API_KEY=...` (never commit `.env`).

```text
python -m agent.loop
```

Edit the string in `agent/loop.py` under `if __name__ == "__main__"` to try other requests. A proper `main.py` prompt can come next.

## Project layout

```
desk-agent/
  agent/     # LLM talk + the think→call-tools→repeat loop
  tools/     # one file per tool (notes, reminders, open path, …)
  data/      # notes, reminders, and other local files the tools write
```



## Tools (v1)


| Tool      | Status | What it does                    |
| --------- | ------ | ------------------------------- |
| save note | done   | append text to `data/notes.txt` |
| reminder  | done   | store `when                     |
| open path | later  | open a folder/file on Windows   |




## Stack

- Python
- LLM: Gemini API (v1)

