from tools.notes import save_note
from tools.reminder import set_reminder
from tools.files import open_path

tools = {
    "save_note" : {
        "description" : "Save a note to the notes database",
        "parameters" : "text",
        "function" : save_note,
    },
    "set_reminder" : {
        "description" : "Set a reminder to the user",
        "parameters" : "text, when",
        "function" : set_reminder
    },
    "open_path" : {
        "description" : "Open a path in the file explorer",
        "parameters" : "text",
        "function" : open_path
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