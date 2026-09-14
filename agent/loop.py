from tools.registry import get_tool
from agent.llm import ask
import json
from datetime import datetime

def decide(user_text,already,now_str):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    res = ask("""
    you are an router and provide only {"name" : "tool_name", "text" : "tool_text"} format response.

    Tools: save_note, set_reminder, open_path, deep_research

    Reply with only one JSON object each time (one tool)
    
    Formats:
    note: {"name":"save_note","text":"..."}
    reminder: {"name":"set_reminder","text":"...","when":"YYYY-MM-DD HH:MM:SS"}
    path: {"name" : "open_path", "text" : "C:/full/path/here"}
    deep_research: {"name" : "deep_research", "text" : "the research question"}

    finished: {"name":"done","text":"short confirmation"}
    stuck: {"name":"unknown","text":"why you can't help"}
    
    If the user asked for two things, do one now; the next round will do the other
    deep_research runs the FULL research pipeline by itself.
    Call deep_research at most ONCE per user request.
    
    For set_reminder, "when" MUST be absolute: YYYY-MM-DD HH:MM:SS
    If user says "in 20 seconds" or "at 6pm", convert using current local time.

    If history already shows deep_research, reply with done and put the research result (or a short confirmation) in text.
    Do not call deep_research again for the same request.

    User asked this question:
    """ + user_text + "already present:" + f"Current local time is: {now_str}" + already)

    final_res = json.loads(res)
    print(final_res)
    return final_res

def run(user_text):
    history = []

    for i in range(5):
        already = ", ".join(history)

        result = decide(user_text, already,now_str)

        if result['name'] == "done":
            return result.get('text')

        tool = get_tool(result['name'])
        if tool is None or result['name'] == "unknown":
            return result.get('text')
        else:
            if result.get('when') is not None:
                if result['name'] == "set_reminder":
                    func = tool['function'](result['text'],result.get('when'))
                else:
                    func = tool['function'](result['text'])
            else:
                func = tool['function'](result['text'])

            if result['name'] == "deep_research":
                return func
        
        history.append(result['name'] + " -> " + func)

    return func