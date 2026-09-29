import os
from pathlib import Path
import platform
import re
import subprocess

MAX_RESULTS = 30
MAX_DEPTH = 6
SKIP_DIRS = {"Library","node_modules","__pycache__","venv","site-packages","Applications"}

def known_places():
    home = Path.home()
    system = platform.system()

    places = {
        "downloads" : home / "Downloads",
        "documents" : home / "Documents",
        "desktop" : home / "Desktop",
        "music" : home / "Music",
        "pictures" : home / "Pictures",
        "home" : home,
    }

    if system == "Darwin":
        places["movies"] = home / "Movies"
        places["applications"] = Path("/Applications")
    elif system == "Windows":
        places["videos"] = home / "Videos"

    return places

def clean_path(name):
    cleaned =  name.strip().lower().strip(".,!?")
    filler = ("folder","directory","location","path","my","the","open")

    return " ".join(word for word in cleaned.split() if word not in filler)

def open_in_explorer(path):
    system = platform.system()

    if system == "Windows":
        os.startfile(path)
    elif system == "Darwin":
        subprocess.run(['open',str(path)],check=False)
    else:
        subprocess.run(['xdg-open',str(path)],check=False)

def open_file(name):
    cleaned = clean_path(name)
    places = known_places()

    if cleaned in places:
        path = places[cleaned]

        if not path.exists():
            return f"no such file or directory: {path}"

        open_in_explorer(path)
        return f"opened {path}"

    direct = Path(name).expanduser()

    if direct.exists():
        open_in_explorer(direct)
        return f"opened {direct}"

    return ambiguous_answer(cleaned)


def keep_directories(lines):
    paths = []

    for line in lines:
        path = Path(line)

        if path.is_dir():
            paths.append(path)

        if len(paths) >= MAX_RESULTS:
            break

    return paths

def spotlight_lines(name):
    home = str(Path.home())
    command = ["mdfind", "-onlyin", home, "-name", name]

    try:
        return subprocess.run(command,capture_output=True,text=True,timeout = 5).stdout.splitlines()
    except subprocess.TimeoutExpired:
        return []

def spotlight_search(name):
    return keep_directories(spotlight_lines(name))

def walk_home():
    home = Path.home()

    for root, dirs, files in os.walk(home):
        depth = len(Path(root).relative_to(home).parts)

        # dirs[:] = ... prunes in place, which is what makes os.walk skip them
        if depth >= MAX_DEPTH:
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        yield root, dirs, files

def walk_search(name):
    name = name.lower()
    paths = []

    for root, dirs, _ in walk_home():
        for d in dirs:
            if name in d.lower():
                paths.append(Path(root) / d)

                if len(paths) >= MAX_RESULTS:
                    return paths

    return paths

def candidate_finder(name):
    if platform.system() == "Darwin":
        paths = spotlight_search(name)

        if paths:
            return paths

    return walk_search(name)

def candidate_ranker(name,paths,use_stem=False):
    name = name.lower()

    def sort_key(path):
        # files compare by stem so "resume" exactly matches resume.pdf
        label = name_key(path.stem) if use_stem else path.name.lower()
        exact = 0 if label == name else 1
        depth = len(path.parts)

        try:
            recent = -path.stat().st_mtime
        except OSError:
            recent = 0

        return (exact,depth,recent)

    return sorted(paths,key=sort_key)

def ambiguous_answer(name):
    paths = candidate_finder(name)

    if not paths:
        return f"no such file or directory: {name}"
    
    ranking = candidate_ranker(name,paths)

    if len(ranking) == 1:
        open_in_explorer(ranking[0])
        return f"opened {ranking[0]}"

    exact = [path for path in ranking if path.name.lower() == name.lower()]

    if len(exact) == 1:
        open_in_explorer(exact[0])
        return f"opened {exact[0]}"

    if len(exact) > 1:
        return f"Found multiple exact matches:\n" + '\n'.join(str(path) for path in exact)
    
    return f"Found no exact match, did you mean: \n" + '\n'.join(str(path) for path in ranking)

def name_key(text):
    # "long_notes", "long-notes" and "long notes" are the same name when spoken
    return " ".join(re.split(r"[\s_\-]+", text.lower())).strip()

def clean_file_name(name,extensions):
    cleaned = name.strip().lower().strip(".,!?")

    for ext in extensions:
        if cleaned.endswith(ext):
            cleaned = cleaned[:-len(ext)]
            break

    # "resume pdf" / "my report document" -> "resume" / "report"
    filler = {"file","the","my","document"} | {ext.lstrip(".") for ext in extensions}

    return name_key(" ".join(word for word in cleaned.split() if word not in filler))

def keep_files(lines,extensions):
    paths = []

    for line in lines:
        path = Path(line)

        if path.is_file() and path.suffix.lower() in extensions:
            paths.append(path)

        if len(paths) >= MAX_RESULTS:
            break

    return paths

def walk_file_search(name,extensions):
    paths = []

    for root, _, files in walk_home():
        for f in files:
            path = Path(root) / f

            if name in name_key(path.stem) and path.suffix.lower() in extensions:
                paths.append(path)

                if len(paths) >= MAX_RESULTS:
                    return paths

    return paths

def prefer_extension(paths,extensions):
    # report.pdf next to its converted report.docx is one document: keep the extension listed first
    best = {}

    for path in paths:
        key = path.with_suffix("")

        if key not in best or extensions.index(path.suffix.lower()) < extensions.index(best[key].suffix.lower()):
            best[key] = path

    return [path for path in paths if best[path.with_suffix("")] == path]

def resolve_file(name,extensions):
    # returns (path, None) when one file is clearly meant, else (None, message for the user)
    direct = Path(name.strip()).expanduser()

    if direct.is_file() and direct.suffix.lower() in extensions:
        return direct, None

    stem = clean_file_name(name,extensions)

    if not stem:
        return None, "Tell me the file name."

    paths = []

    if platform.system() == "Darwin":
        paths = keep_files(spotlight_lines(stem),extensions)

    if not paths:
        paths = walk_file_search(stem,extensions)

    if not paths:
        return None, f"no such file: {stem} ({', '.join(extensions)})"

    ranking = prefer_extension(candidate_ranker(stem,paths,use_stem=True),extensions)

    if len(ranking) == 1:
        return ranking[0], None

    exact = [path for path in ranking if name_key(path.stem) == stem]

    if len(exact) == 1:
        return exact[0], None

    if len(exact) > 1:
        return None, "Found multiple files with that name, which one?\n" + '\n'.join(str(path) for path in exact[:5])

    return None, "Found no exact match, did you mean:\n" + '\n'.join(str(path) for path in ranking[:5])

if __name__ == "__main__":
    question = "open pictures"

    result = open_file(question)
    print(result)