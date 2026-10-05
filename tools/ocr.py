import csv
import io
import os
from concurrent.futures import ThreadPoolExecutor
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

# Reading text in pictures with Tesseract, in the right alphabet.
# Tesseract reads only the alphabets of the language packs it is given: Gujarati read with the
# English pack comes out as Latin look-alikes ("વોલીબોલ" -> "dicletia"). Its own script guess is
# not reliable either (it called a Gujarati poster "Latin"), so the alphabet is found from the
# letters themselves: a quick first read with every installed Indian-language pack, then the
# Unicode block most letters fall in picks the packs for the real read.

# alphabet: (Unicode block, Tesseract packs that read it, a letter of it for font checks)
SCRIPTS = {
    "Gujarati": ((0x0A80, 0x0AFF), ["guj"], "\u0A95"),
    "Devanagari": ((0x0900, 0x097F), ["hin", "mar"], "\u0915"),
    "Bengali": ((0x0980, 0x09FF), ["ben"], "\u0995"),
    "Gurmukhi": ((0x0A00, 0x0A7F), ["pan"], "\u0A15"),
    "Oriya": ((0x0B00, 0x0B7F), ["ori"], "\u0B15"),
    "Tamil": ((0x0B80, 0x0BFF), ["tam"], "\u0B95"),
    "Telugu": ((0x0C00, 0x0C7F), ["tel"], "\u0C15"),
    "Kannada": ((0x0C80, 0x0CFF), ["kan"], "\u0C95"),
    "Malayalam": ((0x0D00, 0x0D7F), ["mal"], "\u0D15"),
    "Arabic": ((0x0600, 0x06FF), ["urd", "ara"], "\u0628"),
}
MIN_SHARE = 0.15        # an alphabet with fewer of the letters than this is noise, not text
UNSURE = 70             # Tesseract's confidence (0-100) below which a word is worth a look
NOT_READABLE = 50       # average confidence below this: the packs do not fit the text
DETECT_WIDTH = 1400     # the first read only needs to see which alphabet it is
SURE = 75               # a word another reading found is kept only when this sure
READ_TIMEOUT = 20       # seconds: Tesseract's sparse mode can grind for minutes on a smooth gradient
LAYOUT_SURE = 50        # the layout-aware reading's words are kept from this sure up
CANDIDATE = 60          # less sure than this, a reading's word is not even a candidate
INSTALL_PACKS = {
    "Darwin": "brew install tesseract-lang",
    "Windows": "run the Tesseract installer again and tick the languages under 'Additional language data'",
    "Linux": "sudo apt install tesseract-ocr-guj tesseract-ocr-hin (or your language)",
}

def tesseract_path():
    # apps started from the Dock or an IDE often lack Homebrew's folder in PATH
    found = shutil.which("tesseract")

    if found:
        return found

    for candidate in ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract",
                      r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                      str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Tesseract-OCR/tesseract.exe")]:
        if Path(candidate).is_file():
            return candidate

    return None

def installed_languages():
    tesseract = tesseract_path()

    if tesseract is None:
        return []

    listed = subprocess.run([tesseract, "--list-langs"], capture_output=True, text=True)

    return [line.strip() for line in (listed.stdout + listed.stderr).splitlines()[1:] if line.strip() and " " not in line.strip()]

def install_hint():
    return INSTALL_PACKS.get(platform.system(), INSTALL_PACKS["Linux"])

def packs_note():
    # with only English installed, Gujarati or Hindi words mixed into English lines would come out
    # as Latin look-alikes and nothing can tell: say so every time until the packs are installed
    if any(pack not in ("eng", "osd", "equ", "snum") for pack in installed_languages()):
        return None

    return f"this computer reads only English text in pictures; for Gujarati, Hindi and others: {install_hint()}"

def script_of(char):
    code = ord(char)

    for name, ((low, high), _, _) in SCRIPTS.items():
        if low <= code <= high:
            return name

    return "Latin" if char.isascii() and char.isalpha() else None

def read_words(image, languages, psm=3, settings=(), timeout=None, single_thread=False):
    # every word Tesseract finds: text, box (left, top, right, bottom) in the image's pixels,
    # confidence, and which line it belongs to
    tesseract = tesseract_path()

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "page.png"
        image.save(path)
        # readings that run side by side each get one thread, as Tesseract's docs advise
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"} if single_thread else None
        done = subprocess.run([tesseract, str(path), "stdout", "-l", languages, "--psm", str(psm), "--dpi", "200", "-c", "tessedit_create_tsv=1", *settings],
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env)

    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else f"tesseract exit code {done.returncode}")

    words = []

    for row in csv.DictReader(io.StringIO(done.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()

        if row.get("level") != "5" or not text:
            continue

        left, top, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
        words.append({"text": text, "box": (left, top, left + width, top + height), "conf": float(row["conf"]),
                      "line": (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))})

    return words

def choose_languages(image):
    # ("guj+eng", None), or (None, why the text cannot be read and what to install)
    chosen = os.environ.get("OCR_LANGUAGE", "").strip()

    if chosen:
        return chosen, None

    if tesseract_path() is None:
        return None, "Tesseract is not installed (Mac: brew install tesseract, Windows: the UB Mannheim installer)"

    installed = installed_languages()
    packs = [next(pack for pack in packs if pack in installed) for _, packs, _ in SCRIPTS.values() if any(pack in installed for pack in packs)]
    small = image if image.width <= DETECT_WIDTH else image.resize((DETECT_WIDTH, round(image.height * DETECT_WIDTH / image.width)))
    words = read_words(small, "+".join(["eng"] + packs) if "eng" in installed else "+".join(packs or installed[:1]))

    counts = {}

    for word in words:
        for char in word["text"]:
            script = script_of(char)

            if script:
                counts[script] = counts.get(script, 0) + 1

    letters = sum(counts.values())

    if not letters:
        return "+".join(["eng"] + packs[:1]) if "eng" in installed else installed[0], None

    found = sorted((script for script, count in counts.items() if count / letters >= MIN_SHARE), key=lambda s: -counts[s])
    languages = []

    for script in found:
        if script != "Latin":
            languages += [pack for pack in SCRIPTS[script][1] if pack in installed][:1]

    languages += ["eng"] if "eng" in installed else []

    # only English could look, and it read nonsense: the text is in an alphabet with no pack here
    sure = [word["conf"] for word in words if word["conf"] >= 0]
    average = sum(sure) / len(sure) if sure else 0

    if not packs and average < NOT_READABLE:
        return None, (f"the text does not read as English (only {average:.0f}% sure), and this computer has no "
                      f"Tesseract packs for other alphabets. For Gujarati, Hindi and others: {install_hint()}")

    return "+".join(dict.fromkeys(languages)), None

def overlap(a, b):
    # share of the smaller box that the two boxes have in common
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])

    if width <= 0 or height <= 0:
        return 0.0

    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))

    return width * height / smaller if smaller > 0 else 0.0

def noise(word):
    # "|", "———", "રરર", "1111": what a drawing, a net or an edge reads as
    text = word["text"]

    if not any(char.isalnum() for char in text):
        return word["conf"] < 90
    if len(text) >= 2 and len(set(text)) == 1:
        return word["conf"] < 90

    return False

def read_everything(image, languages):
    # one picture read several ways, since no single way reads every poster: the layout-aware way
    # suits pages of text, the sparse way finds words scattered over a photo, a smoothed copy loses
    # photo grain, and single colour channels separate coloured letters from a background of the
    # same brightness. Words a reading is sure of are kept; where readings overlap, the surer wins.
    import numpy as np
    import cv2
    from PIL import Image

    def run(readings):
        def one(reading):
            picture, psm, _ = reading

            try:
                return read_words(picture, languages, psm, timeout=READ_TIMEOUT, single_thread=True)
            except subprocess.TimeoutExpired:
                return []   # this way of reading got stuck; the others still count

        with ThreadPoolExecutor(max_workers=3) as pool:
            return list(zip(readings, pool.map(one, readings)))

    results = run([(image, 3, LAYOUT_SURE), (image, 11, SURE)])
    layout_words = results[0][1]
    missed = [word for word in results[1][1] if word["conf"] >= SURE and not noise(word)
              and not any(overlap(word["box"], other["box"]) > 0.5 for other in layout_words)]

    # the sparse reading found sure words the layout reading did not: a busy picture, read it harder
    if missed:
        pixels = np.array(image)
        smooth = Image.fromarray(cv2.bilateralFilter(pixels, 9, 60, 60))
        channels = [Image.fromarray(np.ascontiguousarray(pixels[:, :, c])) for c in range(3)]
        results += run([(smooth, 11, SURE)] + [(channel, 11, SURE) for channel in channels])

    candidates = [{**word, "anchored": least == LAYOUT_SURE and word["conf"] >= LAYOUT_SURE}
                  for (_, _, least), words in results for word in words
                  if word["conf"] >= min(least, CANDIDATE) and not noise(word)]

    return [word for group in overlapping(candidates) for word in settle(group, image, languages)]

def overlapping(words):
    # words from different readings that sit on the same spot
    groups = []

    for word in words:
        joined = [group for group in groups if any(overlap(word["box"], other["box"]) > 0.5 for other in group)]
        groups = [group for group in groups if group not in joined] + [sum(joined, []) + [word]]

    return groups

def ink(image, box):
    # letter pixels in a box: pixels far in colour from what surrounds the box
    import numpy as np

    pixels = np.asarray(image).astype(int)
    height, width = pixels.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
    ox0, oy0, ox1, oy1 = max(0, x0 - 6), max(0, y0 - 6), min(width, x1 + 6), min(height, y1 + 6)

    if x1 <= x0 or y1 <= y0:
        return 0

    outer = pixels[oy0:oy1, ox0:ox1].reshape(-1, 3)
    inside = np.zeros((oy1 - oy0, ox1 - ox0), bool)
    inside[y0 - oy0:y1 - oy0, x0 - ox0:x1 - ox0] = True
    ring = outer[~inside.reshape(-1)]
    background = np.median(ring if len(ring) else outer, axis=0)

    return int((np.abs(pixels[y0:y1, x0:x1] - background).max(axis=2) > 60).sum())

def settle(group, image, languages):
    # one spot, several readings: noise if none is sure; the surest if they agree; else the spot
    # is read again on its own, and failing that the reading that covers the most letter pixels
    # wins (an overconfident reading can skip letters: "1ઇનલ" for "ફાઇનલ" at 91% sure)
    if not any(word["conf"] >= SURE or word["anchored"] for word in group):
        return []

    if len({word["text"] for word in group}) == 1:
        return [max(group, key=lambda word: word["conf"])]

    x0, y0 = min(w["box"][0] for w in group), min(w["box"][1] for w in group)
    x1, y1 = max(w["box"][2] for w in group), max(w["box"][3] for w in group)
    pad = max(8, round(0.12 * (y1 - y0)))
    left, top = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    crop = image.crop((left, top, min(image.width, int(x1 + pad)), min(image.height, int(y1 + pad))))

    try:
        again = [word for word in read_words(crop, languages, 7, timeout=READ_TIMEOUT) if not noise(word)]
    except (subprocess.TimeoutExpired, RuntimeError):
        again = []

    if again and min(word["conf"] for word in again) >= CANDIDATE:
        return [{**word, "box": (word["box"][0] + left, word["box"][1] + top, word["box"][2] + left, word["box"][3] + top)} for word in again]

    return [max(group, key=lambda word: (ink(image, word["box"]), word["conf"]))]
