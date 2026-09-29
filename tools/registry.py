from tools.notes import save_note, read_notes
from tools.reminder import set_reminder, list_reminders
from tools.files import open_file
from tools.research import run_research
from tools.documents import pdf_to_word, summarize_document

# parameters: keys the loop reads from the model's JSON, passed in this order
# final: the tool's result is the answer, returned to the user without another router pass
tools = {
    "save_note" : {
        "description" : "Save a note to the notes database",
        "parameters" : ["text"],
        "function" : save_note,
    },
    "read_notes" : {
        "description" : "Read back the saved notes",
        "parameters" : [],
        "function" : read_notes,
    },
    "set_reminder" : {
        "description" : "Set a reminder to the user",
        "parameters" : ["text", "when"],
        "function" : set_reminder
    },
    "list_reminders" : {
        "description" : "List reminders that have not fired yet",
        "parameters" : [],
        "function" : list_reminders
    },
    "open_file" : {
        "description" : "Open a file or folder in the dedicated file explorer",
        "parameters" : ["text"],
        "function" : open_file
    },
    # "open_path" : {
    #     "description" : "Open a path in the dedicated file explorer",
    #     "parameters" : "text",
    #     "function" : open_path,
    # },
    "deep_research" : {
        "description" : "Deep research on the given question",
        "parameters" : ["text"],
        "function" : run_research,
        "final" : True
    },
    "pdf_to_word" : {
        "description" : "Convert a PDF to an editable Word file next to it, with OCR for scans",
        "parameters" : ["text"],
        "function" : pdf_to_word
    },
    "summarize_document" : {
        "description" : "Summarize a pdf, docx, txt or md file",
        "parameters" : ["text"],
        "function" : summarize_document,
        "final" : True
    }
}

def get_tool(name):
    if name in tools:
        return tools[name]
    else:
        return None
