import re
import requests
from agent.llm import ask
from ddgs import DDGS
from pathlib import Path

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

    return f"Conclusion appended {len(summary)} chars"

def is_enough(question):
    with open("data/research/conclusion.txt",encoding="utf-8",mode="r") as f:
        conclusion = f.read()

    response = ask(f"""
    You are a helpful assistant that summarizes the contents of a website.
    look at the question: {question},
    and the current conclusion is: {conclusion}

    you are asked to understand the question and the current conclusion is considered as deep research enough or not.
    if the conclusion is not deep research enough, say just word "need more" which means we need more research else return word "enough" only.
    
    """)

    cleaned = response.strip().lower()

    if "need more" in cleaned:
        return "need more"

    if "enough" in cleaned:
        return "enough"
    
    return "Research is enough"

def final_research(question):
    history_path = Path("data/history/saved_research.txt")
    history_path.parent.mkdir(parents=True,exist_ok=True)

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

    with history_path.open(encoding="utf-8",mode="a") as f:
        f.write(response + "\n\n")

    with open("data/research/final_research.txt",encoding="utf-8",mode="w") as f:
        f.write(response + "\n\n")

    return response

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

        deep_enough = is_enough(question)
        print(deep_enough)

        if deep_enough == "enough":
            break

    return final_research(question)