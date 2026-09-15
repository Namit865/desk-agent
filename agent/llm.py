import os
from dotenv import load_dotenv
from google import genai
import requests
from google.genai.errors import APIError, ServerError

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=api_key)

def ask_cloud(prompt):
    try:
        response = client.models.generate_content(
            model = "gemini-3.8-flash",
            contents = prompt,
        )
        return response.text

    except (APIError,ServerError) as e:
        if hasattr(e,"code") and e.code in [429,404,503]:
            raise e
        
    raise RuntimeError("All models failed to generate a response.")

def ask_local(prompt):
    url = "http://localhost:11434/api/generate"

    payload = {
        "model" : "qwen2.5:14b",
        "prompt" : prompt,
        "stream" : False,
    }

    response = requests.post(url, json=payload)

    response.raise_for_status()

    data = response.json()

    return data['response']

def ask(prompt):
    try:
        response = ask_local(prompt)
        return response if response else "Local LLM failed, please start the local LLM server."
    except Exception as e:
        response = ask_cloud(prompt)
        return response


if __name__ == "__main__":
    print(ask_local("Say hello in one short sentence."))