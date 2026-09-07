from tools.notes import save_note

tools = {
    "save_note" : {
        "description" : "Save a note to the notes database",
        "parameters" : "text",
        "function" : save_note,
    }
}

def get_tool(name):
    if name in tools:
        return tools[name]
    else:
        return None

if __name__ == "__main__":
    tool = get_tool("save_note")
    
    result = tool['function']("registry test note")
    print(result)
    
    tool2 = get_tool("nope")
    print(tool2)