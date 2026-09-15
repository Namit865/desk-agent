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
        print("You said: ",user_text)

        if user_text.lower() == "text":
            mode = "text"
            print("Text mode on.")
            continue

    result = run(user_text)

    speak(result)

    print("Agent: ",result)