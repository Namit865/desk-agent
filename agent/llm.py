import os
from dotenv import load_dotenv
from google import genai
import requests
from google.genai.errors import APIError, ServerError
from groq import Groq

load_dotenv()

groq_api_key = os.environ.get("GROQ_API_KEY")

if not groq_api_key:
    groq_client = None
else:
    groq_client = Groq(api_key = groq_api_key)

gemini_api_key = os.environ.get("GEMINI_API_KEY")

if not gemini_api_key:
    gemini_client = None
else:
    gemini_client = genai.Client(api_key=gemini_api_key)

def ask_cloud(prompt):
    try:
        response = gemini_client.models.generate_content(
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
        response = ask_groq(prompt)
        return response if response else "Groq returned empty response."
    except Exception as e:
        print(f"Groq failed, using local")
        response = ask_local(prompt)
        return response

def ask_groq(prompt):
    if groq_client is None:
        raise ValueError("Groq client is not set, please set the GROQ_API_KEY in environment.")
    
    response = groq_client.chat.completions.create(
        messages = [{"role" : "user","content" : prompt}],
        model = "openai/gpt-oss-120b"
    )

    return response.choices[0].message.content

if __name__ == "__main__":
    print(ask_groq("Say hello in one short sentence."))