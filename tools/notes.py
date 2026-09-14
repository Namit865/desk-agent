def save_note(text):
    with open("data/notes.txt","a") as file:
        file.write(text + "\n")
        return "Note saved successfully"