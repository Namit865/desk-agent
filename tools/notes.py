MAX_NOTES = 30

def save_note(text):
    with open("data/notes.txt","a") as file:
        file.write(text + "\n")
        return "Note saved successfully"

def read_notes():
    try:
        with open("data/notes.txt") as file:
            notes = [line.strip() for line in file if line.strip()]
    except FileNotFoundError:
        notes = []

    if not notes:
        return "No notes saved yet"

    shown = notes[-MAX_NOTES:]
    header = f"{len(notes)} notes, oldest first" if len(notes) == len(shown) else f"last {len(shown)} of {len(notes)} notes, oldest first"

    return header + ":\n" + "\n".join(f"- {note}" for note in shown)
