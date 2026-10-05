from tools.notes import save_note, read_notes
from tools.reminder import set_reminder, list_reminders
from tools.files import open_file
from tools.research import run_research
from tools.documents import pdf_to_word, summarize_document
from tools.convert import convert_file
from tools.edit_file import edit_file
from tools.contacts import save_contact
from tools.whatsapp import send_whatsapp, link_whatsapp

# parameters: keys the loop reads from the model's JSON, passed in this order
# optional: keys passed by name only when the model sent them
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
        "optional" : ["layout"],
        "function" : pdf_to_word
    },
    "summarize_document" : {
        "description" : "Summarize a pdf, docx, txt or md file",
        "parameters" : ["text"],
        "function" : summarize_document,
        "final" : True
    },
    "convert_file" : {
        "description" : "Convert a file to another format (images, pdf, office, text, data, audio, video)",
        "parameters" : ["text", "to"],
        "function" : convert_file
    },
    # final: the reply says exactly whether it was sent or only opened, so no model rewords it
    "send_whatsapp" : {
        "description" : "Send a file or a message to a person on WhatsApp",
        "parameters" : ["text", "to"],
        "optional" : ["message", "direct"],
        "function" : send_whatsapp,
        "final" : True
    },
    # final: the reply lists exactly what changed and how it reads back
    "edit_file" : {
        "description" : "Change words inside a picture or PDF and save the finished picture or PDF",
        "parameters" : ["text", "instruction"],
        "optional" : ["to"],
        "function" : edit_file,
        "final" : True
    },
    "save_contact" : {
        "description" : "Save a person's phone number",
        "parameters" : ["text", "number"],
        "function" : save_contact
    },
    "link_whatsapp" : {
        "description" : "Link desk-agent to the user's WhatsApp by scanning a code",
        "parameters" : [],
        "function" : link_whatsapp,
        "final" : True
    }
}

def get_tool(name):
    if name in tools:
        return tools[name]
    else:
        return None
