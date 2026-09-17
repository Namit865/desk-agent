from tools.registry import get_tool
from agent.llm import ask
import json
from datetime import datetime

def decide(user_text,already):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    res = ask("""
        You are a JSON tool router. Output ONE JSON object only. No markdown. No extra words.
        
        Tools and exact shapes:
        {"name":"save_note","text":"note content"}
        {"name":"set_reminder","text":"what to remind","when":"YYYY-MM-DD HH:MM:SS"}
        {"name":"open_file","text":"folder name to open"}
        {"name":"deep_research","text":"research question"}
        {"name":"done","text":"short confirmation"}
        {"name":"unknown","text":"why you cannot help"}
        
        Rules:
        1. Choose exactly one tool per reply.
        2. Use Current local time to turn relative times ("in 30 seconds", "in 2 minutes", "at 6pm") into absolute when. For set_reminder, when is required. Never put "in 30 seconds" only in text.
        3. If Already used tools shows the needed tool is finished, reply done and confirm what was just done for the first time ("Reminder set for 6pm", "Opened desk-agent"). Never say "already". Do not call the same tool again for the same request.
        4. Two user asks → do the next unfinished one only.
        5. deep_research at most once. If it is already in Already used tools, reply done.
        6. If you cannot follow these shapes, reply unknown.
        7. use tools only when the user wants a side effect (save, remind, open, research)
        8. If they are chatting or asking for an explaination, use done and write a clear helpful answer in text.
        9. Stay short unless they ask for details.
        10. For open_file, text is the plain name the user said ("downloads", "desk-agent"). Never invent an absolute path.
        11. If a tool result is a failure or a question, reply done and pass that message to the user. Do not call the tool again.

    User asked this question:
    """ + user_text + f"\n\nCurrent local time is: {now_str}\n" + "Already used tools: " + (already if already else "none"))

    try:
        final_res = json.loads(res)
    except json.JSONDecodeError:
        print(f"Bad Json from model: {res}")

        return {"name":"unknown","text":"I got bad reply from model. Please try again."}

    return final_res

def run(user_text):
    history = []

    for i in range(5):
        already = ", ".join(history)

        result = decide(user_text, already)

        if not isinstance(result, dict) or "name" not in result:
            return "I got an incomplete reply from model. Please try again."

        if result['name'] == "done":
            return result.get('text')

        tool = get_tool(result['name'])
        if tool is None or result['name'] == "unknown":
            return result.get('text')
        else:
            try:
                if result.get('when') is not None:
                    if result['name'] == "set_reminder":
                        func = tool['function'](result['text'],result.get('when'))
                    else:
                        func = tool['function'](result['text'])
                else:
                    func = tool['function'](result['text'])

                if result['name'] == "deep_research":
                    return func
            except Exception as e:
                return f"Error calling tool: {e}"

        history.append(result['name'] + " -> " + func)

    return func