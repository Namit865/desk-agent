import os
from dotenv import load_dotenv
from google import genai
import requests

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY is missing from environment or .env file")

client = genai.Client(api_key=api_key)

def ask_cloud(prompt):
    response = client.models.generate_content(
        model = "gemini-3.1-flash-lite",
        contents = prompt,
    )

    return response.text

def ask_local(prompt):
    url = "http://localhost:11434/api/generate"

    payload = {
        "model" : "llama3.2",
        "prompt" : prompt,
        "stream" : False,
    }

    response = requests.post(url, json=payload)

    response.raise_for_status()

    data = response.json()

    return data['response']

def ask(prompt):
    try:
        return ask_cloud(prompt)
    except Exception as e:
        print("Cloud failed, using local:",e)
        return ask_local(prompt) if ask_local else "Local LLM failed, please start the local LLM server."

if __name__ == "__main__":
    print(ask_local("Say hello in one short sentence."))