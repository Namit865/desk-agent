import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY is missing from environment or .env file")

client = genai.Client(api_key=api_key)

def ask(prompt):
    response = client.models.generate_content(
        model = "gemini-3.6-flash",
        contents = prompt,
    )

    return response.text

if __name__ == "__main__":
    print(ask("Say hello in one short sentence."))