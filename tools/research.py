import re
import requests
from agent.llm import ask
from ddgs import DDGS

def fetch_url(url):
    response = requests.get(url,timeout=20,headers = {"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    text = re.sub(r"<script.*?</script>", " ", response.text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ",text).strip()

    with open("data/research/site_contents.txt", encoding="utf-8", mode="w") as f:
        f.write(text)

    return f"Saved site contents {len(text)} chars"

def summarize_site_contents(question):

    with open("data/research/site_contents.txt",encoding="utf-8",mode="r") as f:
        site_content = f.read()

    if not site_content:
        return "No site contents found"

    prompt = f"""
    You are a helpful assistant that summarizes the contents of a website.
    Please summarize the contents of the website in a few paragraphs in a concise and informative manner which can be used to answer research question which user asked.
    user asked: {question}
    The website contents are as follows:
    {site_content}

    Return the summary purely in text, no markdown formatting.
    """
    
    response = ask(prompt)

    return response

def append_conclusion(summary):
    with open("data/research/conclusion.txt",encoding="utf-8",mode="a") as f:
        f.write(summary + "\n")
    
    return f"Appended conclusion {len(summary)} chars"

def final_research(question):
    with open("data/research/conclusion.txt",encoding="utf-8",mode="r") as f:
        conclusion = f.read()

    response = ask(f"""
    You are a helpful assistant that answers research questions based on the conclusion of the website.
    user has asked for deep research for the following question: {question}

    entire conclusion of the website is as follows:
    {conclusion}

    return the answer to the user's question based on the conclusion of the website.
    provide the user's question + answer.
    """)

    with open("data/history/saved_research.txt",encoding="utf-8",mode="a") as f:
        f.write(response + "\n\n")

    with open("data/research/final_research.txt",encoding="utf-8",mode="w") as f:
        f.write(response + "\n\n")

    return "Research completed and saved to final_research.txt"

def clear_research_files():

    with open("data/research/conclusion.txt",encoding="utf-8",mode="w") as f:
        f.write("")
    
    return "Research files cleared"

def find_sources(query,max_results=5):
    results = DDGS().text(query,max_results=max_results)
    links = []

    for item in results:
        url = item.get('href')

        if url:
            links.append(url)
    
    return links

def run_research(question):
    clear_research_files()

    links = find_sources(question)

    for link in links:
        try:
            fetch_url(link)
        except Exception as e:
            print(f"Error fetching URL {link}: {e}")
            continue

        summary = summarize_site_contents(question)
        append_conclusion(summary)

    return final_research(question)