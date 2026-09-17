import os
from pathlib import Path
import platform
import subprocess


# def open_path(path):
#     system = platform.system()

#     if system == "Windows":
#         os.startfile(path)

#     elif system == "Darwin":
#         subprocess.run(["open", path], check=False)

#     else:
#         subprocess.run(["xdg-open", path], check=False)

#     folder_name = Path(path).name
#     return f"opened {folder_name}"

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
    direct = Path(name).expanduser()

    if direct.exists():
        open_in_explorer(direct)
        return f"opened {direct}"

    cleaned = clean_path(name)
    places = known_places()

    if cleaned in places:
        path = places[cleaned]

        if not path.exists():
            return f"no such file or directory: {path}"
        
        open_in_explorer(path)
        return f"opened {path}"

    return ambiguous_answer(cleaned)


def candidate_finder(name):
    system = platform.system()

    home = str(Path.home())

    folder_command = ["mdfind", "-onlyin", home, "-name", name]
    
    if system == "Darwin":
        try:
            result = subprocess.run(folder_command,capture_output=True,text=True,timeout = 5).stdout.splitlines()
        except subprocess.TimeoutExpired:
            result = []
            print("timeout expired")

        paths = []

        for line in result:
            path = Path(line)

            if path.is_dir():
                paths.append(path)

            if len(paths) >= 30:
                break

        return paths

    return []

def candidate_ranker(name,paths):
    name = name.lower()

    def sort_key(path):
        exact = 0 if path.name.lower() == name else 1
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

if __name__ == "__main__":
    question = "open pictures"

    result = open_file(question)
    print(result)