from tools.registry import get_tool

def fake_brain(user_text):
    return {"name" : "save_note", "text" : user_text}

def run(user_text):
    result = fake_brain(user_text)

    tool = get_tool(result['name'])
    func = tool['function'](result['text'])

    return func

if __name__ == "__main__":
    print(run("buy milk"))