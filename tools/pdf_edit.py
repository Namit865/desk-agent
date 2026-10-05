import html
import io
from pathlib import Path

import pymupdf

from tools.fonts import face_file, installed_font, kind, main_script, plain_family, script_font

# Changing words in a PDF that has real text (not a scan). Only the changed words and the words
# that must slide along the line are taken out; pictures and drawings stay. The new words go back
# on the same baseline in the PDF's own font when it has every letter needed (then the change
# cannot be seen), else in a font of the same kind, size and colour.

# PDF's standard fonts, which are never embedded, and PyMuPDF's identical built-in copies
BASE14 = {"helvetica": "helv", "arial": "helv", "times": "tiro", "courier": "cour"}
BASE14_STYLES = {("helv", True, False): "hebo", ("helv", False, True): "heit", ("helv", True, True): "hebi",
                 ("tiro", True, False): "tibo", ("tiro", False, True): "tiit", ("tiro", True, True): "tibi",
                 ("cour", True, False): "cobo", ("cour", False, True): "coit", ("cour", True, True): "cobi"}

def pdf_lines(page):
    lines = []

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [span for span in line["spans"] if span["text"]]
            text = "".join(span["text"] for span in spans)

            if text.strip():
                lines.append({"spans": spans, "text": text, "box": tuple(line["bbox"])})

    return lines

def style_of(span):
    name = span["font"].split("+")[-1]
    bold = bool(span["flags"] & 16) or "bold" in name.lower()
    italic = bool(span["flags"] & 2) or "italic" in name.lower() or "oblique" in name.lower()

    return name, bold, italic

def face_bytes(face):
    # one face as a font file of its own: a .ttc holds several (a Mac's regular and bold in one file)
    path, number = face

    if Path(path).suffix.lower() not in (".ttc", ".otc"):
        return Path(path).read_bytes()

    from fontTools.ttLib import TTCollection

    buffer = io.BytesIO()
    TTCollection(path).fonts[number].save(buffer)

    return buffer.getvalue()

def squeeze(name):
    # "DejaVu Sans Book", "DejaVuSans" and "ABCDEF+DejaVuSans" are one font
    return "".join(char for char in name.split("+")[-1].lower() if char.isalnum()).removesuffix("book").removesuffix("regular")

def choose_font(doc, page, span, text):
    # (font bytes or a built-in name, pymupdf.Font to measure with, True when it is the PDF's own)
    name, bold, italic = style_of(span)
    letters = [char for char in text if not char.isspace()]
    script = main_script(text)

    # 1. the PDF's own font, when it really has the letters (a subset may lack new ones)
    for xref, _, _, basefont, *_ in page.get_fonts(full=True):
        try:
            _, ext, _, buffer = doc.extract_font(xref)
            font = pymupdf.Font(fontbuffer=buffer) if buffer and ext != "n/a" else None
        except Exception:
            font = None

        if font and squeeze(name) in (squeeze(basefont), squeeze(font.name)) and all(font.has_glyph(ord(char)) for char in letters):
            return buffer, font, True

    # 2. a standard PDF font (Helvetica, Times, Courier) is drawn by PyMuPDF's identical copy
    key = plain_family(name).replace(" ", "").replace("-", "").lower()
    builtin = next((code for stem, code in BASE14.items() if key.startswith(stem)), None)

    if builtin and not script:
        builtin = BASE14_STYLES.get((builtin, bold, italic), builtin)
        # the PDF only names a standard font; PyMuPDF's copy is the same font
        return builtin, pymupdf.Font(builtin), key.startswith(("helvetica", "times", "courier"))

    # 3. an installed font of the same kind; another alphabet gets a font that has its letters
    family = script_font(script) if script else installed_font(plain_family(name), kind(plain_family(name), span["flags"]))
    face = face_file(family, bold, italic) if family else None

    if face is None:
        return "helv", pymupdf.Font("helv"), False

    data = face_bytes(face)

    return data, pymupdf.Font(fontbuffer=data), False

def write(page, source, font, x, baseline, text, span):
    color = pymupdf.sRGB_to_pdf(span["color"])

    if isinstance(source, bytes) and main_script(text):
        # letters that join and change shape (Gujarati, Hindi...) need shaping, which the HTML
        # layout does; insert_text would draw them unjoined
        css = (f"@font-face {{font-family: chosen; src: url(chosen.ttf);}} "
               f"* {{font-family: chosen; font-size: {span['size']}px; margin: 0; padding: 0; line-height: normal;"
               f" color: rgb({round(color[0] * 255)}, {round(color[1] * 255)}, {round(color[2] * 255)});}}")
        archive = pymupdf.Archive()
        archive.add((source, "chosen.ttf"))
        top = baseline - font.ascender * span["size"]
        page.insert_htmlbox(pymupdf.Rect(x, top, page.rect.width, baseline + span["size"]), html.escape(text), css=css, archive=archive)
        return

    if isinstance(source, bytes):
        alias = f"F{abs(hash(source)) % 10**8}"
        page.insert_font(fontname=alias, fontbuffer=source)
    else:
        alias = source

    page.insert_text((x, baseline), text, fontname=alias, fontsize=span["size"], color=color)

def anchor(page, line, lines):
    # how a line holds its place: centred headings keep their middle, right-aligned columns
    # (amounts, dates) keep their right edge, everything else keeps its left edge
    x0, _, x1, _ = line["box"]
    width = page.rect.width
    lefts = sum(1 for other in lines if other is not line and abs(other["box"][0] - x0) < 1)
    rights = sum(1 for other in lines if other is not line and abs(other["box"][2] - x1) < 1)

    if abs((x0 + x1) / 2 - width / 2) < 0.01 * width and lefts < 2 and rights < 2:
        return "centre"

    # numbers of one width share both edges, so the edge more lines share wins
    return "right" if rights >= 2 and rights > lefts else "left"

def edit_pdf_line(doc, page, line, lines, changes):
    # changes: [(find, replace)], each found in line["text"]; returns (new text, PDF's own font used)
    spans, text = line["spans"], line["text"]
    starts, at = [], 0

    for span in spans:
        starts.append(at)
        at += len(span["text"])

    hits = [(text.find(find), text.find(find) + len(find), replace) for find, replace in changes]
    touched = [n for n, span in enumerate(spans) if any(starts[n] < end and starts[n] + len(span["text"]) > start for start, end, _ in hits)]
    first, last = touched[0], touched[-1]
    seg_start, seg_end = starts[first], starts[last] + len(spans[last]["text"])
    segment = text[seg_start:seg_end]

    for start, end, replace in sorted(hits, reverse=True):
        segment = segment[:start - seg_start] + replace + segment[end - seg_start:]

    style = spans[first]
    source, font, own = choose_font(doc, page, style, segment)
    # old and new measured in one font, so a stand-in font's own width is not mistaken for a change
    delta = font.text_length(segment, fontsize=style["size"]) - font.text_length(text[seg_start:seg_end], fontsize=style["size"])

    side = anchor(page, line, lines)
    before = -delta / 2 if side == "centre" else -delta if side == "right" else 0
    after = delta / 2 if side == "centre" else 0 if side == "right" else delta
    prefix = spans[:first] if before else []
    suffix = spans[last + 1:] if after else []

    for span in spans[first:last + 1] + prefix + suffix:
        # only the middle of the line's height: a neighbouring line's letters are never caught
        x0, y0, x1, y1 = span["bbox"]
        inset = 0.3 * (y1 - y0)
        page.add_redact_annot(pymupdf.Rect(x0 + 0.3, y0 + inset, x1 - 0.3, y1 - inset))

    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)

    for span, shift in [(span, before) for span in prefix] + [(span, after) for span in suffix]:
        span_source, span_font, _ = choose_font(doc, page, span, span["text"])
        write(page, span_source, span_font, span["origin"][0] + shift, span["origin"][1], span["text"], span)

    if segment:
        write(page, source, font, style["origin"][0] + before, style["origin"][1], segment, style)

    return text[:seg_start] + segment + text[seg_end:], own
