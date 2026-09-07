def save_note(text):
    with open("data/notes.txt","a") as file:
        file.write(text + "\n")
        return "Note saved successfully"

if __name__ == "__main__":
    save_note("This is a test note 1")
    save_note("This is a test note 2")
    save_note("This is a test note 3")