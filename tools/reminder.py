def set_reminder(text,when):
    with open("data/reminders.txt", "a") as f:
        f.write(f"{when} | {text}\n")
        
    return f"Reminder set for {when}: {text} successfully"

if __name__ == "__main__":
    print(set_reminder("buy_milk","2026-09-08 10:00:00"))