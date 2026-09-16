from agent.loop import run
from tools.voice import listen_once, speak

mode = "text"

while True:
    if mode == "text":
        print("type a request, or 'exit' to quit")
        user_text = input("You: ")

        if user_text.lower() == "exit":
            break

        if user_text.lower() == "":
            continue
        
        if user_text.lower() == "listen":
            mode = "voice"
            print("Voice mode on. Say text to switch back.")
            continue

    else:
        print("Listening... (say 'text' to switch back)")
        user_text = listen_once()

        if not user_text.strip():
            print("Didn't catch that, listening again.")
            continue

        print("You said: ",user_text)

        spoken = user_text.strip().lower().strip(".,!?")

        if len(spoken.split()) <= 3 and ("text" in spoken or "keyboard" in spoken):
            mode = "text"
            print("Text mode on.")
            continue

    result = run(user_text)

    speak(result)

    print("Agent: ",result)