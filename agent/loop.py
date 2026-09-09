from tools.registry import get_tool
from agent.llm import ask
import json

def decide(user_text,already):
    res = ask("""
    you are an router and provide only {"name" : "tool_name", "text" : "tool_text"} format response.

    Tools: save_note, set_reminder, open_path

    Reply with only one JSON object each time (one tool)
    
    Formats:
    note: {"name":"save_note","text":"..."}
    reminder: {"name":"set_reminder","text":"...","when":"..."}
    path: {"name" : "open_path", "text" : "C:/full/path/here"}
    finished: {"name":"done","text":"short confirmation"}
    stuck: {"name":"unknown","text":"why you can't help"}
    
    If the user asked for two things, do one now; the next round will do the other

    User asked this question:
    """ + user_text + "already present:" + already)

    final_res = json.loads(res)
    print(final_res)
    return final_res

def run(user_text):
    history = []

    for i in range(5):
        already = ", ".join(history)

        result = decide(user_text, already)

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
        
        history.append(result['name'] + " -> " + func)

    return func

if __name__ == "__main__":
    print(run("open the folder C:/Users/Asus/Python/desk-agent"))