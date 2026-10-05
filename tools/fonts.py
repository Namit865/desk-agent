import os
import platform
import re
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# A Word file only names its fonts; Pages or Word draws it with what this computer has.
# Fonts that are not installed get swapped for an installed one of the same kind, so
# Pages stops reporting missing fonts and picks something that looks close.

COMMON_FAMILIES = {"TimesNewRoman": "Times New Roman", "Times": "Times New Roman", "CourierNew": "Courier New", "Courier": "Courier New", "Arial": "Arial", "Helvetica": "Helvetica"}
# same letter widths first, so lines keep their length
LOOKALIKES = {
    "Calibri": ["Carlito"], "Cambria": ["Caladea"],
    "Arial": ["Helvetica", "Liberation Sans", "Arimo"], "Helvetica": ["Arial", "Liberation Sans"],
    "Times New Roman": ["Times", "Liberation Serif", "Tinos"], "Times": ["Times New Roman", "Liberation Serif"],
    "Courier New": ["Courier", "Liberation Mono", "Cousine"], "Courier": ["Courier New", "Liberation Mono"],
}
FALLBACKS = {
    "serif": ["Times New Roman", "Times", "Georgia", "Liberation Serif", "DejaVu Serif", "Noto Serif"],
    "sans": ["Arial", "Helvetica", "Helvetica Neue", "Liberation Sans", "DejaVu Sans", "Noto Sans"],
    "mono": ["Courier New", "Courier", "Menlo", "Liberation Mono", "DejaVu Sans Mono"],
}
SERIF_WORDS = re.compile(r"serif|times|roman|georgia|garamond|cambria|book|palatino|baskerville|caslon|didot|bodoni|charis|minion|century", re.I)
MONO_WORDS = re.compile(r"mono|courier|consol|menlo|code", re.I)

# fonts that have each alphabet's letters, best first: Mac, Windows, then free fonts. A font is
# used only if its character map really has the letters: a font without them shows boxes or
# another font's look-alikes, which is how Gujarati turned into "weird letters"
SCRIPT_FONTS = {
    "Gujarati": ["Gujarati Sangam MN", "Gujarati MT", "Noto Sans Gujarati", "Nirmala UI", "Shruti", "Lohit Gujarati"],
    "Devanagari": ["Kohinoor Devanagari", "Devanagari Sangam MN", "Devanagari MT", "Noto Sans Devanagari", "Nirmala UI", "Mangal", "Lohit Devanagari"],
    "Bengali": ["Bangla Sangam MN", "Kohinoor Bangla", "Noto Sans Bengali", "Nirmala UI", "Vrinda"],
    "Gurmukhi": ["Gurmukhi Sangam MN", "Gurmukhi MN", "Noto Sans Gurmukhi", "Nirmala UI", "Raavi"],
    "Oriya": ["Oriya Sangam MN", "Noto Sans Oriya", "Nirmala UI", "Kalinga"],
    "Tamil": ["Tamil Sangam MN", "Noto Sans Tamil", "Nirmala UI", "Latha"],
    "Telugu": ["Telugu Sangam MN", "Kohinoor Telugu", "Noto Sans Telugu", "Nirmala UI", "Gautami"],
    "Kannada": ["Kannada Sangam MN", "Noto Sans Kannada", "Nirmala UI", "Tunga"],
    "Malayalam": ["Malayalam Sangam MN", "Noto Sans Malayalam", "Nirmala UI", "Kartika"],
    "Arabic": ["Geeza Pro", "Noto Naskh Arabic", "Segoe UI", "Arial"],
}

MAX_SPACING = 0.25          # letters never move further apart than a quarter of the font size

_installed = None
_faces = {}
_metrics = {}
_letters = {}

def plain_family(family):
    # bold and italic are switched on in the run, "Charis SIL Bold" is no family Word knows
    return re.sub(r"(\s+(Regular|Bold|Italic|Oblique))+$", "", family, flags=re.I) or family

def font_family(fonts, name):
    # pdf2docx reads the family from the embedded font file; fonts that are not embedded fall back to their name
    found = fonts.get(name)

    if found is not None:
        family = found.name
    else:
        base = re.sub(r"(PSMT|MT|PS)$", "", re.split(r"[-,]", name.split("+")[-1])[0])
        family = COMMON_FAMILIES.get(base, base)

    return plain_family(family)

def kind(family, flags=0):
    # span flags: 4 = serifed, 8 = monospaced
    if flags & 8 or MONO_WORDS.search(family):
        return "mono"

    if flags & 4 or (SERIF_WORDS.search(family) and "sans" not in family.lower()):
        return "serif"

    return "sans"

def font_dirs():
    home = Path.home()
    system = platform.system()

    if system == "Darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library/Fonts"]

    if system == "Windows":
        return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]

    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts", home / ".local/share/fonts"]

def installed_families():
    # lower-case family names of every font on this computer, read once per run;
    # also notes where each family's regular, bold and italic files are
    global _installed

    if _installed is None:
        from fontTools.ttLib import TTCollection, TTFont

        _installed = set()

        for folder in font_dirs():
            for path in folder.rglob("*") if folder.is_dir() else []:
                suffix = path.suffix.lower()

                try:
                    if suffix in (".ttf", ".otf"):
                        faces = [TTFont(path, lazy=True)]
                    elif suffix in (".ttc", ".otc"):
                        faces = TTCollection(path, lazy=True).fonts
                    else:
                        continue

                    for number, face in enumerate(faces):
                        names = {record.nameID: record.toUnicode() for record in face["name"].names if record.nameID in (1, 2, 16, 17)}
                        families = {names[i].lower() for i in (1, 16) if i in names}
                        style = names.get(17, names.get(2, "")).lower()
                        _installed.update(families)

                        for family in families:
                            _faces.setdefault((family, "bold" in style, "italic" in style or "oblique" in style), (path, number))
                except Exception:
                    continue

    return _installed

def text_width(family, bold, italic, text, size):
    # width in points of text set in an installed family, None when it cannot be measured
    from fontTools.ttLib import TTFont

    installed_families()
    face = _faces.get((family.lower(), bold, italic)) or _faces.get((family.lower(), False, False))

    if face is None:
        return None

    if face not in _metrics:
        path, number = face
        font = TTFont(path, fontNumber=number, lazy=True)
        _metrics[face] = (font.getBestCmap() or {}, font["hmtx"], font["head"].unitsPerEm)

    cmap, hmtx, units = _metrics[face]

    return sum(hmtx[cmap[ord(c)]][0] if ord(c) in cmap else units / 2 for c in text) * size / units

def spacing_to_fit(family, bold, italic, text, size, width):
    # points of extra space per letter so the text is as wide as it was in the PDF
    natural = text_width(family, bold, italic, text, size)

    if natural is None or not text:
        return 0.0

    spacing = (width - natural) / len(text)

    return max(-MAX_SPACING * size, min(MAX_SPACING * size, spacing))

def installed_font(family, font_kind):
    installed = installed_families()

    # nothing found means we cannot tell, so keep the name
    if not installed or family.lower() in installed:
        return family

    for choice in LOOKALIKES.get(family, []) + FALLBACKS[font_kind]:
        if choice.lower() in installed:
            return choice

    return family

def face_file(family, bold=False, italic=False):
    # (font file, face number) of an installed family, the regular face when the style is missing
    installed_families()

    return _faces.get((family.lower(), bold, italic)) or _faces.get((family.lower(), False, False))

def has_letter(face, letter):
    from fontTools.ttLib import TTFont

    if face not in _letters:
        path, number = face
        _letters[face] = set((TTFont(path, fontNumber=number, lazy=True).getBestCmap() or {}).keys())

    return ord(letter) in _letters[face]

def script_font(script):
    # an installed family that has this alphabet's letters, or None
    from tools.ocr import SCRIPTS

    letter = SCRIPTS[script][2]

    for family in SCRIPT_FONTS.get(script, []):
        face = face_file(family)

        if face and has_letter(face, letter):
            return family

    return None

def main_script(text):
    # the non-Latin alphabet of a piece of text, if it has one
    from tools.ocr import script_of

    counts = {}

    for char in text:
        script = script_of(char)

        if script and script != "Latin":
            counts[script] = counts.get(script, 0) + 1

    return max(counts, key=counts.get) if counts else None

def fit_script_fonts(doc):
    # every run with Gujarati, Hindi... letters gets a font that has them: Word draws those letters
    # with the complex-script font (w:cs) and its bold (w:bCs), so both are set; Latin letters keep
    # their font unless the run has none
    used = set()

    for run in doc.element.iter(qn("w:r")):
        text = "".join(t.text or "" for t in run.iter(qn("w:t")))
        script = main_script(text)
        family = script_font(script) if script else None

        if not family:
            continue

        props = run.get_or_add_rPr()
        fonts = props.get_or_add_rFonts()
        latin = any(char.isascii() and char.isalpha() for char in text)

        for key in ("w:cs",) + (() if latin else ("w:ascii", "w:hAnsi", "w:eastAsia")):
            fonts.set(qn(key), family)

        if props.find(qn("w:b")) is not None and props.find(qn("w:bCs")) is None:
            props.find(qn("w:b")).addnext(OxmlElement("w:bCs"))

        used.add(family)

    return used

def pdf_font_kinds(pdf_path):
    # {family: "serif" | "sans" | "mono"} for the fonts the PDF uses
    from pdf2docx.font.Fonts import Fonts

    kinds = {}

    with pymupdf.open(pdf_path) as doc:
        fonts = Fonts.extract(doc)

        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        family = font_family(fonts, span["font"])
                        kinds.setdefault(family, kind(family, span["flags"]))

    return kinds

def fit_fonts(docx_path, kinds):
    doc = Document(docx_path)
    used = set()

    for root in (doc.element, doc.styles.element):
        for fonts in root.iter(qn("w:rFonts")):
            for key in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
                if fonts.get(qn(key)):
                    family = plain_family(fonts.get(qn(key)))
                    family = installed_font(family, kinds.get(family, kind(family)))
                    fonts.set(qn(key), family)
                    used.add(family)

    used |= fit_script_fonts(doc)

    # the Word template's theme and font list name Office fonts (Calibri, Cambria, MS Mincho)
    # that a Mac without Office lacks, even when no text uses them
    sans, serif = installed_font("Calibri", "sans"), installed_font("Cambria", "serif")

    for part in doc.part.package.iter_parts():
        name = str(part.partname)

        if name.startswith("/word/theme/"):
            xml = part.blob.decode("utf-8")
            xml = xml.replace('<a:latin typeface="Calibri"', f'<a:latin typeface="{escape(sans)}"')
            xml = xml.replace('<a:latin typeface="Cambria"', f'<a:latin typeface="{escape(serif)}"')
            xml = re.sub(r'<a:font script="[^"]*" typeface="[^"]*"/>', "", xml)
            part._blob = xml.encode("utf-8")

        elif name == "/word/fontTable.xml":
            names = "".join(f'<w:font w:name="{escape(family, {chr(34): "&quot;"})}"/>' for family in sorted(used | {sans, serif}))
            part._blob = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                          f'<w:fonts xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">{names}</w:fonts>').encode("utf-8")

    doc.save(docx_path)
