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
        {"name":"read_notes","text":""}
        {"name":"set_reminder","text":"what to remind","when":"YYYY-MM-DD HH:MM:SS"}
        {"name":"list_reminders","text":""}
        {"name":"open_file","text":"folder name to open"}
        {"name":"pdf_to_word","text":"pdf file name to convert","layout":"auto"}
        {"name":"summarize_document","text":"file name to summarize (pdf, docx, txt, md)"}
        {"name":"convert_file","text":"file name to convert","to":"format to make, like jpg, png, pdf, docx, txt, xlsx, mp3"}
        {"name":"send_whatsapp","text":"file name to send, or empty for a message only","to":"person name or phone number","message":"words to send, or empty","direct":false}
        {"name":"save_contact","text":"person name","number":"phone number"}
        {"name":"link_whatsapp","text":""}
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
        7. use tools only when the user wants a side effect (save, remind, open, research, convert, summarize, send on WhatsApp) or asks about their saved notes or reminders
        8. If they are chatting or asking for an explaination, use done and write a clear helpful answer in text.
        9. Stay short unless they ask for details.
        10. For open_file, pdf_to_word, summarize_document and convert_file, text is the plain name the user said ("downloads", "desk-agent", "resume", "report.pdf"). Never invent an absolute path.
        11. If a tool result is a failure or a question, reply done and pass that message to the user. Do not call the tool again.
        12. After read_notes or list_reminders, reply done and answer the user's question from that result (all of them, or only the ones they asked about).
        13. After pdf_to_word, reply done with where the Word file was saved, the layout, the text match, and any note from the result.
        14. For pdf_to_word, layout is "exact" when they want it to look exactly like the PDF (keep the design), "flow" when they want normal reflowing text to edit, else "auto".
        15. To turn any other file into another format use convert_file (png to jpg, jpg to pdf, docx to pdf, heic to jpg, xlsx to csv, mp4 to mp3). A PDF to Word goes to pdf_to_word.
        16. After convert_file, reply done with where the new file was saved and any note from the result.
        17. For send_whatsapp, text is only the file name ("send the pdf named abc to rahul" -> text "abc pdf"), to is the person exactly as the user said it, message is only words the user wants sent to them.
        18. direct is true only when the user says to send it directly, automatically or without opening WhatsApp. Otherwise direct is false.
        19. "save rahul's number +91 98765 43210" -> save_contact. "link my whatsapp" -> link_whatsapp.

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
            missing = [param for param in tool['parameters'] if result.get(param) is None]

            if missing:
                return f"The model left out {', '.join(missing)} for {result['name']}. Please try again."

            try:
                options = {param: result[param] for param in tool.get('optional', []) if result.get(param)}
                func = tool['function'](*[result[param] for param in tool['parameters']], **options)

                if tool.get('final'):
                    return func
            except Exception as e:
                return f"Error calling tool: {e}"

        history.append(result['name'] + " -> " + func)

    return func