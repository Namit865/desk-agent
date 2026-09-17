from tools.notes import save_note
from tools.reminder import set_reminder
from tools.files import open_file
from tools.research import run_research

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
    "open_file" : {
        "description" : "Open a file or folder in the dedicated file explorer",
        "parameters" : "text",
        "function" : open_file
    },
    # "open_path" : {
    #     "description" : "Open a path in the dedicated file explorer",
    #     "parameters" : "text",
    #     "function" : open_path,
    # },
    "deep_research" : {
        "description" : "Deep research on the given question",
        "parameters" : "text",
        "function" : run_research
    }
}

def get_tool(name):
    if name in tools:
        return tools[name]
    else:
        return None