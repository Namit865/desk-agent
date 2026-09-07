# Desk Agent

Windows GenAI desk agent: you speak or type in normal language → an LLM understands the request → it calls tools (normal Python functions) on your PC → runs a small loop until that request is done → short confirmation.

Not a chatbot that only answers. Not training an LLM from scratch. The engine is general (LLM + tools). v1 tools are limited on purpose.

## Status

- Folders in place: `agent/`, `tools/`, `data/`
- First tool works: `save_note` appends to `data/notes.txt`
- Tool registry works: look up by name → call the real function
- Fake brain path works: `agent/loop.py` → registry → save note (still no LLM)
- Next: choose LLM (API or Ollama) and add a thin `agent/llm.py` adapter

## What it will do (v1)

- Save notes
- Set reminders
- Open a folder or path on Windows

Example: *"save a note that I need to call mom tomorrow and remind me at 6pm"* → note tool + reminder tool → done.

## How to run

Not runnable end-to-end yet. This section grows when the agent loop works.

## Project layout

```
desk-agent/
  agent/     # LLM talk + the think→call-tools→repeat loop
  tools/     # one file per tool (notes, reminders, open path, …)
  data/      # notes, reminders, and other local files the tools write
```

## Tools (v1)

| Tool | Status | What it does |
|------|--------|--------------|
| save note | done | append text to `data/notes.txt` |
| reminder | later | store a reminder with a time |
| open path | later | open a folder/file on Windows |

## Stack (decide soon)

- Python
- LLM via API (OpenAI/Gemini) or local Ollama — chosen after structure exists
