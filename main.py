from agent.loop import run

while True:
    print("type a request, or 'exit' to quit")
    user_text = input("You: ")

    if user_text.lower() == "exit":
        break

    if user_text.lower() == "":
        continue

    result = run(user_text)

    print("Agent: ",result)