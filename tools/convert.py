import shutil
import tempfile
from collections import deque
from pathlib import Path

from tools.converters import EDGES
from tools.documents import free_path
from tools.files import resolve_file

# Any format to any other: a breadth-first search over the converters finds the shortest
# chain (heic -> png -> pdf -> docx). If a step fails, that converter is set aside and the
# next shortest chain is tried.

EXTENSION_ALIASES = {"jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "htm": "html", "markdown": "md", "heif": "heic"}
NAME_ALIASES = {"text": "txt", "word": "docx", "excel": "xlsx", "powerpoint": "pptx", "keynote": "key"}
MAX_TRIES = 6
# "the volleyball image", "my resume pdf", "the photo in downloads": type words limit the search to
# that type, place words are not part of the name
KIND_WORDS = {"image": "picture", "images": "picture", "photo": "picture", "photos": "picture", "picture": "picture",
              "pic": "picture", "pics": "picture", "screenshot": "picture", "poster": "picture"}
PLACE_WORDS = {"in", "from", "on", "at", "downloads", "download", "desktop", "documents", "folder"}

class StepFailed(Exception):
    def __init__(self, edge, error):
        super().__init__(f"{edge.source} -> {edge.target}: {error}")
        self.edge = edge

def format_of(name):
    name = str(name).strip().lower().lstrip(".")
    return EXTENSION_ALIASES.get(name, NAME_ALIASES.get(name, name))

def route(source, target, allowed):
    # shortest chain of converters; among equally short ones the converter listed first wins
    queue = deque([(source, [])])
    seen = {source}

    while queue:
        current, chain = queue.popleft()

        for edge in allowed:
            if edge.source != current:
                continue

            if edge.target == target:
                return chain + [edge]

            if edge.target not in seen:
                seen.add(edge.target)
                queue.append((edge.target, chain + [edge]))

    return None

def run_chain(chain, src, tmp):
    # returns ([(file, name suffix)], notes); a suffix like "-2" marks page 2 of a pdf
    files = [(src, "")]
    notes = []

    for step, edge in enumerate(chain):
        produced = []

        for n, (file, suffix) in enumerate(files):
            dst = Path(tmp) / f"step{step}-{n}.{edge.target}"

            try:
                written, more = edge.run(file, dst)
            except Exception as e:
                raise StepFailed(edge, e) from e

            produced += [(out, suffix + out.stem[len(dst.stem):]) for out in written]
            notes += [note for note in more if note not in notes]

        files = produced

    return files, notes

def missing_tools(source, target):
    # what to install when a chain exists only through programs this computer lacks
    hints = []
    left = list(EDGES)

    for _ in range(3):
        chain = route(source, target, left)

        if chain is None:
            break

        absent = [edge for edge in chain if not edge.available()]
        hints += [edge.hint for edge in absent if edge.hint not in hints]
        left = [edge for edge in left if edge not in absent]

    return hints

def convert_file(name, to):
    target = format_of(to)
    sources = {edge.source for edge in EDGES}

    if target not in {edge.target for edge in EDGES}:
        return f"I cannot make .{target} files. I can make: {', '.join(sorted({edge.target for edge in EDGES}))}"

    # the file to convert is never the target type: "convert logo to jpg" picks logo.png over logo.jpg
    extensions = [f".{fmt}" for fmt in sorted(sources - {target})]
    extensions += [f".{alias}" for alias, fmt in EXTENSION_ALIASES.items() if fmt in sources and fmt != target]
    typed = format_of(Path(name.strip()).suffix) if Path(name.strip()).suffix else None
    said = name.split()
    kept = [word for word in said if word.lower().strip(".,!?") not in set(KIND_WORDS) | PLACE_WORDS]

    if kept and len(kept) < len(said):
        if any(KIND_WORDS.get(word.lower().strip(".,!?")) == "picture" for word in said):
            from tools.converters import IMAGES
            pictures = [ext for ext in extensions if format_of(ext) in IMAGES]
            extensions = pictures or extensions

        name = " ".join(kept)

    # a name said with its type ("logo.png") means that type only
    if typed in sources:
        extensions = [ext for ext in extensions if format_of(ext) == typed]

    path, message = resolve_file(name, extensions)

    if path is None:
        return message

    # the same name in several types: the oldest is the original the others were made from
    if typed not in sources:
        twins = [other for other in path.parent.iterdir() if other.stem == path.stem and other.suffix.lower() in extensions]
        path = min(twins + [path], key=lambda other: other.stat().st_mtime)

    source = format_of(path.suffix)
    allowed = [edge for edge in EDGES if edge.available()]
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        for _ in range(MAX_TRIES):
            chain = route(source, target, allowed)

            if chain is None:
                break

            print(f"Converting {path.name}: {' -> '.join([source] + [edge.target for edge in chain])}...")

            try:
                files, notes = run_chain(chain, path, tmp)
                break
            except StepFailed as e:
                failures.append(str(e))
                # a converter that failed on this file will fail on its other routes too
                allowed = [edge for edge in allowed if edge.run is not e.edge.run]
        else:
            chain = None

        if chain is None:
            if failures:
                return f"Could not convert {path.name} to .{target}: " + "; ".join(failures)

            hints = missing_tools(source, target)

            if hints:
                return f"Converting .{source} to .{target} needs " + " or ".join(hints)

            return f"I have no way to turn .{source} into .{target}."

        results = []

        for file, suffix in files:
            final = free_path(path.with_name(f"{path.stem}{suffix}.{target}"))
            shutil.move(str(file), str(final))
            results.append(final)

    steps = " -> ".join([source] + [edge.target for edge in chain])

    if len(results) == 1:
        reply = f"Converted {path.name} to {results[0]} ({steps})"
    else:
        reply = f"Converted {path.name} to {len(results)} files in {results[0].parent}: {', '.join(r.name for r in results[:5])}{' ...' if len(results) > 5 else ''} ({steps})"

    return reply + (". Note: " + "; ".join(notes) if notes else "")
