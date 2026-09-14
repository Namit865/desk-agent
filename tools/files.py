import os
from pathlib import Path
import platform
import subprocess

def open_path(path):
    system = platform.system()

    if system == "Windows":
        os.startfile(path)

    elif system == "Darwin":
        subprocess.run(["open", path], check=False)

    else:
        subprocess.run(["xdg-open", path], check=False)

    folder_name = Path(path).name
    return f"opened {folder_name}"

if __name__ == "__main__":
    print(open_path("/Users/namit/Python/desk-agent"))