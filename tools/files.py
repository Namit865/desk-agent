import os
from pathlib import Path

def open_path(path):
    os.startfile(path)
    folder_name = Path(path).name
    return f"opened {folder_name}"

if __name__ == "__main__":
    print(open_path("C:\\Users\\Asus\\Python\\desk-agent"))