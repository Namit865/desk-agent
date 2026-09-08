from tools.registry import get_tool
from agent.llm import ask
import json

def decide(user_text):
    res = ask("""
    you are an router and provide only {"name" : "tool_name", "text" : "tool_text"} format response.

    currently available tools:
    - save_note

    if you don't know what to do, return {"name" : "unknown" , "text" : "why you can't help"}

    User asked this question:
    """ + user_text)

    final_res = json.loads(res)
    return final_res

def run(user_text):
    result = decide(user_text)

    tool = get_tool(result['name'])
    if tool is None or result['name'] == "unknown":
        return result.get('text')
    else:
        func = tool['function'](result['text'])

    return func

if __name__ == "__main__":
    print(run("what is current weather in gujarat?"))