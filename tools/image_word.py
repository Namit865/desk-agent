from io import BytesIO
from statistics import median

import numpy as np
import pymupdf
from docx import Document
from docx.enum.section import WD_SECTION
from docx.oxml import parse_xml
from docx.shared import Pt
from PIL import Image, ImageDraw, ImageFont, ImageOps, features

from tools.exact_layout import anchor_xml, clean_text, picture_graphic, textbox_graphic
from tools.fonts import face_file, fit_fonts, installed_font, script_font
from tools.ocr import UNSURE, choose_languages, packs_note, read_everything, read_words, script_of

# A picture becomes a Word page you can edit where the words stand:
#   read    Tesseract in the picture's own alphabet (tools/ocr.py), word by word with position
#   erase   the letters are painted over from the pixels around them (OpenCV inpainting), so an
#           edited word does not show the old one underneath
#   place   each line becomes a text box where it stood: font with the line's letters, size fitted
#           to the line's width and height, the line's colour, bold when its strokes are thick
#   report  words Tesseract was unsure of, to check by eye

PAGE_LONG_SIDE = 842        # points: the page is A4-sized, shaped like the picture
SMALL_TEXT = 30             # median word height in pixels below which the picture is read enlarged 2x
INK = 60                    # colour distance (0-255) from the background around a word that makes a pixel a letter
FAINT = 15                  # ...and next to a letter, this much makes a pixel part of its soft edge
BOLD_STROKE = 0.115         # stroke width / line height above this reads as bold
FIT_LIMIT = 1.25            # width and height sizes further apart than this: the reading lost or gained letters

def load_picture(src):
    with Image.open(src) as image:
        image = ImageOps.exif_transpose(image)      # phone photos are stored sideways with a turn flag

        if image.mode in ("RGBA", "LA", "P", "PA"):
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, "white")
            flat.paste(image, mask=image.getchannel("A"))
            return flat

        return image.convert("RGB")

def read_lines(image, languages):
    words = read_words(image, languages)
    heights = [word["box"][3] - word["box"][1] for word in words]
    factor = 1

    # Tesseract reads best when letters are about 30 pixels tall
    if heights and median(heights) < SMALL_TEXT:
        factor = 2
        image = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)

    words = [{**word, "box": tuple(v / factor for v in word["box"])} for word in read_everything(image, languages)]
    lines = group_lines(words)
    original = np.asarray(image if factor == 1 else image.resize((image.width // factor, image.height // factor), Image.LANCZOS)).astype(int)

    for line in lines:
        snap_to_ink(original, line)

    return lines

def snap_to_ink(pixels, line):
    # a word box can stop short of its first or last letter ("1ઇનલ" boxed without its "ફા"); the
    # line's ends move out to where pixels of the line's own text colour really end
    height, width = pixels.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in line["box"])
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
    tall = y1 - y0

    if tall < 4 or x1 - x0 < 2:
        return

    ox0, oy0, ox1, oy1 = max(0, x0 - 6), max(0, y0 - 6), min(width, x1 + 6), min(height, y1 + 6)
    outer = pixels[oy0:oy1, ox0:ox1].reshape(-1, 3)
    inside = np.zeros((oy1 - oy0, ox1 - ox0), bool)
    inside[y0 - oy0:y1 - oy0, x0 - ox0:x1 - ox0] = True
    background = np.median(outer[~inside.reshape(-1)], axis=0)
    region = pixels[y0:y1, x0:x1]
    distance = np.abs(region - background).max(axis=2)

    if not (distance > INK).any():
        return

    # the text colour: the middle of the strokes, far from the background
    text_color = np.median(region[distance >= np.percentile(distance[distance > INK], 50)], axis=0)
    left, right = max(0, x0 - tall), min(width, x1 + tall)
    strip = pixels[y0:y1, left:right]
    columns = (np.abs(strip - text_color).max(axis=2) < 45).sum(axis=0) >= 2
    gap = max(2, round(0.25 * tall))

    def walk(start, step, stop):
        edge, x = start, start + step

        while (x - stop) * step < 0 and abs(x - edge) <= gap:
            if columns[x - left]:
                edge = x
            x += step

        return edge

    new_x0 = walk(x0, -1, left - 1)
    new_x1 = walk(x1 - 1, 1, right) + 1
    line["box"] = (new_x0, line["box"][1], new_x1, line["box"][3])

    # the outer words follow, so their letters are erased too
    first, last = line["words"][0], line["words"][-1]
    line["words"][0] = {**first, "box": (min(first["box"][0], new_x0),) + tuple(first["box"][1:])}
    last = line["words"][-1]
    line["words"][-1] = {**last, "box": tuple(last["box"][:2]) + (max(last["box"][2], new_x1), last["box"][3])}

def group_lines(words):
    # the readings number lines differently, so lines are rebuilt from where the words are:
    # first rows (words that overlap in height), then each row split where a gap is wide
    # enough to be a column, not a space
    rows = []

    for word in sorted(words, key=lambda w: (w["box"][1] + w["box"][3]) / 2):
        x0, y0, x1, y1 = word["box"]

        for row in rows:
            top, bottom = row["top"], row["bottom"]

            if min(y1, bottom) - max(y0, top) > 0.5 * min(y1 - y0, bottom - top):
                row["words"].append(word)
                row["top"], row["bottom"] = min(top, y0), max(bottom, y1)
                break
        else:
            rows.append({"words": [word], "top": y0, "bottom": y1})

    lines = []

    for row in rows:
        ordered = sorted(row["words"], key=lambda word: word["box"][0])
        height = median(word["box"][3] - word["box"][1] for word in ordered)
        current = [ordered[0]]

        for word in ordered[1:]:
            if word["box"][0] - max(w["box"][2] for w in current) > 2.5 * height:
                lines.append(current)
                current = []

            current.append(word)

        lines.append(current)

    return sorted(({"words": line, "text": " ".join(word["text"] for word in line),
                    "box": (min(w["box"][0] for w in line), min(w["box"][1] for w in line),
                            max(w["box"][2] for w in line), max(w["box"][3] for w in line))} for line in lines),
                  key=lambda line: (line["box"][1], line["box"][0]))

def study_line(pixels, line):
    # each word's letter pixels, found by colour against the background just around it, and the
    # line's text colour and weight. Kept on the words as "area" (x0, y0, x1, y1), "ink" (the
    # letter pixels in that area) and "background" (the colour around them)
    import cv2

    height, width = pixels.shape[:2]
    lx0, ly0, lx1, ly1 = line["box"]
    line_height = max(1, ly1 - ly0)
    margin = max(3, round(0.2 * line_height))
    words = line["words"]
    ink_colors, strokes = [], []

    for n, word in enumerate(words):
        # a word's area runs halfway to its neighbours and over the whole line's height:
        # Tesseract's box can be narrower than a letter (one-letter words) or miss a vowel sign
        left = (words[n - 1]["box"][2] + word["box"][0]) / 2 if n else word["box"][0] - margin
        right = (word["box"][2] + words[n + 1]["box"][0]) / 2 if n + 1 < len(words) else word["box"][2] + margin
        x0, y0, x1, y1 = max(0, int(left)), max(0, int(ly0 - margin)), min(width, int(right + 1)), min(height, int(ly1 + margin + 1))
        word["area"], word["background"] = (x0, y0, max(x0, x1), max(y0, y1)), None
        word["ink"] = word["faint"] = np.zeros((max(0, y1 - y0), max(0, x1 - x0)), bool)

        if x1 <= x0 or y1 <= y0:
            continue

        # the background is what surrounds the area, so a gradient or photo is matched locally
        ox0, oy0, ox1, oy1 = max(0, x0 - 6), max(0, y0 - 6), min(width, x1 + 6), min(height, y1 + 6)
        outer = pixels[oy0:oy1, ox0:ox1].reshape(-1, 3)
        inside = np.zeros((oy1 - oy0, ox1 - ox0), bool)
        inside[y0 - oy0:y1 - oy0, x0 - ox0:x1 - ox0] = True
        ring = outer[~inside.reshape(-1)]
        background = np.median(ring if len(ring) else outer, axis=0)

        region = pixels[y0:y1, x0:x1].astype(int)
        distance = np.abs(region - background).max(axis=2)
        ink = distance > INK
        word["ink"], word["faint"], word["background"] = ink, distance > FAINT, background

        if ink.any():
            # the colour from the middle of the strokes: their edges are blended with the background
            ink_colors.append(region[ink & (distance >= np.percentile(distance[ink], 50))])
            stroke = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
            strokes.append(2 * np.percentile(stroke[ink], 90))

    colors = np.concatenate(ink_colors) if ink_colors else np.zeros((1, 3))
    line["rgb"] = tuple(int(v) for v in np.median(colors, axis=0))
    line["color"] = "%02X%02X%02X" % line["rgb"]
    line["stroke"] = median(strokes) if strokes else 0.0
    line["bold"] = bool(strokes) and line["stroke"] / line_height > BOLD_STROKE
    give_shapes_to_words(line, (height, width))
    line["shadow"] = find_shadow(pixels, line)

def give_shapes_to_words(line, shape):
    # a word's area can hold a piece of its neighbour (a box cut short): the line's letter pixels
    # are split into connected shapes, and each shape goes to the word its middle sits in
    import cv2

    words = [word for word in line["words"] if word["ink"].size]

    if len(words) < 2:
        return

    x0 = min(word["area"][0] for word in words)
    y0 = min(word["area"][1] for word in words)
    x1 = max(word["area"][2] for word in words)
    y1 = max(word["area"][3] for word in words)
    ink = np.zeros((y1 - y0, x1 - x0), np.uint8)
    faint = np.zeros_like(ink)

    for word in words:
        ax0, ay0, ax1, ay1 = word["area"]
        ink[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0] |= word["ink"].astype(np.uint8)
        faint[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0] |= word["faint"].astype(np.uint8)

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    owner = {}

    for label in range(1, count):
        middle = centroids[label][0] + x0
        # the word whose box holds the shape's middle, else the nearest
        owner[label] = min(range(len(words)), key=lambda n: 0 if words[n]["box"][0] <= middle <= words[n]["box"][2]
                           else min(abs(middle - words[n]["box"][0]), abs(middle - words[n]["box"][2])))

    for n, word in enumerate(words):
        mine = np.isin(labels, [label for label, who in owner.items() if who == n])

        if not mine.any():
            continue

        ys, xs = np.nonzero(mine)
        ax0, ay0, ax1, ay1 = word["area"]
        nx0, ny0 = min(ax0, x0 + int(xs.min())), min(ay0, y0 + int(ys.min()))
        nx1, ny1 = max(ax1, x0 + int(xs.max()) + 1), max(ay1, y0 + int(ys.max()) + 1)
        word["area"] = (nx0, ny0, nx1, ny1)
        word["ink"] = mine[ny0 - y0:ny1 - y0, nx0 - x0:nx1 - x0]
        word["faint"] = faint[ny0 - y0:ny1 - y0, nx0 - x0:nx1 - x0].astype(bool)

def find_shadow(pixels, line):
    # a drop shadow is a second copy of the letters in another colour, a few pixels off:
    # (shadow colour, dx, dy), or None
    import cv2

    colours = [pixels[w["area"][1]:w["area"][3], w["area"][0]:w["area"][2]][w["ink"]] for w in line["words"] if w["ink"].any()]

    if not colours:
        return None

    colours = np.concatenate(colours).astype(np.float32)

    if len(colours) < 200:
        return None

    _, labels, centres = cv2.kmeans(colours, 2, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3, cv2.KMEANS_PP_CENTERS)
    shares = np.bincount(labels.ravel(), minlength=2) / len(labels)

    if shares.min() < 0.15 or np.abs(centres[0] - centres[1]).max() < 80:
        return None

    # the letters are the colour that differs most from the background; the other is the shadow
    background = np.median([w["background"] for w in line["words"] if w["background"] is not None], axis=0)
    text_index = int(np.argmax([np.abs(centre - background).max() for centre in centres]))
    text_rgb, shadow_rgb = centres[text_index], centres[1 - text_index]

    # the soft edge of a letter is a blend of its colour and the background, not a shadow
    span = background - text_rgb
    along = float(np.dot(shadow_rgb - text_rgb, span) / max(1e-6, np.dot(span, span)))
    off_line = np.abs(shadow_rgb - (text_rgb + along * span)).max()

    if 0.05 < along < 0.95 and off_line < 40:
        return None

    x0, y0 = min(w["area"][0] for w in line["words"]), min(w["area"][1] for w in line["words"])
    x1, y1 = max(w["area"][2] for w in line["words"]), max(w["area"][3] for w in line["words"])
    region = pixels[y0:y1, x0:x1].astype(np.float32)
    letters = np.abs(region - text_rgb).max(axis=2) < 50
    shade = np.abs(region - shadow_rgb).max(axis=2) < 50
    best, best_share = None, 0.0
    reach = max(2, round(0.12 * (line["box"][3] - line["box"][1])))

    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            if not (dx or dy):
                continue

            moved = np.roll(np.roll(letters, dy, axis=0), dx, axis=1)
            share = (moved & shade).sum() / max(1, shade.sum())

            if share > best_share:
                best, best_share = (dx, dy), share

    # a real drop shadow sits a few pixels away; one pixel is just the letters' own edge
    if best is None or best_share < 0.5 or max(abs(best[0]), abs(best[1])) < max(2, round(0.03 * (line["box"][3] - line["box"][1]))):
        return None

    line["rgb"] = tuple(int(v) for v in text_rgb)
    line["color"] = "%02X%02X%02X" % line["rgb"]

    return tuple(int(v) for v in shadow_rgb), best[0], best[1]

def decide_bold(line, latin):
    # regular or bold: the line's text drawn both ways in the font it will get, and the weight
    # whose strokes are as thick as the picture's wins (one fixed threshold misreads dark text on paper)
    import cv2

    if not line.get("stroke"):
        return

    line["bold"] = False
    size = max(4, round(fit_line(line, latin)["size"]))
    strokes = {}

    for bold in (False, True):
        canvas = Image.new("L", (max(8, int(size * len(line["text"]) * 0.9) + size), size * 2), 0)
        draw = ImageDraw.Draw(canvas)
        cursor = size * 0.2

        for kind, text in pieces(line["text"]):
            font = pil_font(font_for(kind, latin), bold, size)

            if font is None:
                return

            draw.text((cursor, size * 1.4), text, font=font, fill=255, anchor="ls")
            cursor += font.getlength(text)

        ink = (np.array(canvas) > INK).astype(np.uint8)
        distance = cv2.distanceTransform(ink, cv2.DIST_L2, 3)
        strokes[bold] = 2 * np.percentile(distance[ink > 0], 90) if ink.any() else 0

    line["bold"] = abs(line["stroke"] - strokes[True]) < abs(line["stroke"] - strokes[False])

def letters_mask(shape, line, words):
    # the given words' letters with their soft edges: edge pixels blend into the background and
    # miss the colour test, so pixels only a little off the background count too when they sit
    # next to a letter; a last small margin covers what is left
    import cv2

    core = np.zeros(shape, np.uint8)
    faint = np.zeros(shape, np.uint8)

    for word in words:
        x0, y0, x1, y1 = word["area"]
        core[y0:y1, x0:x1] |= word["ink"].astype(np.uint8)
        faint[y0:y1, x0:x1] |= word["faint"].astype(np.uint8)

    height = max(1, line["box"][3] - line["box"][1])
    near = max(3, round(0.12 * height))
    reach = cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * near + 1, 2 * near + 1)))
    margin = max(2, round(0.04 * height))

    return cv2.dilate(core | (faint & reach), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1)))

def fill_in(pixels, mask):
    # the picture painted over the masked pixels from the pixels around them
    import cv2

    return cv2.inpaint(pixels, mask * 255, 5, cv2.INPAINT_TELEA)

def erase_letters(image, lines):
    # every line's letters painted out; each line also learns its colour and weight
    pixels = np.array(image)
    mask = np.zeros(pixels.shape[:2], np.uint8)

    for line in lines:
        study_line(pixels, line)
        mask |= letters_mask(mask.shape, line, line["words"])

    return Image.fromarray(fill_in(pixels, mask)), mask

def pieces(text):
    # the line split where the alphabet changes: Gujarati words in a Gujarati font, numbers and
    # English in a Latin one; spaces stay with the piece before them
    parts = []

    for char in text:
        script = script_of(char)
        kind = script if script and script != "Latin" else "Latin" if script == "Latin" or char.isdigit() else None

        if parts and (kind is None or kind == parts[-1][0]):
            parts[-1][1] += char
        else:
            parts.append([kind or "Latin", char])

    return [(kind, text) for kind, text in parts]

def font_for(kind, latin):
    return (script_font(kind) if kind != "Latin" else None) or latin

def pil_font(family, bold, size):
    face = face_file(family, bold) or face_file(family)

    if face is None:
        return None

    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC

    return ImageFont.truetype(str(face[0]), size, index=face[1], layout_engine=layout)

def measure(parts, latin, bold, size=100):
    # (width, ink top, ink bottom, ascent, descent, left bearing) of the line set at size, in that unit
    width, top, bottom, ascent, descent, left = 0.0, 0.0, 0.0, 0.0, 0.0, None

    for kind, text in parts:
        font = pil_font(font_for(kind, latin), bold, size)

        if font is None:
            return None

        x0, y0, x1, y1 = font.getbbox(text, anchor="ls")
        a, d = font.getmetrics()
        left = x0 if left is None else left
        top, bottom = min(top, y0), max(bottom, y1)
        ascent, descent = max(ascent, a), max(descent, d)
        width += font.getlength(text)

    return width, top, bottom, ascent, descent, left or 0

def fit_line(line, latin, scale=1.0):
    # the line's font size, letter spacing, baseline and left edge, in the units of scale
    # (1 = the picture's pixels, else points on the Word page)
    x0, x1 = line["box"][0] * scale, line["box"][2] * scale
    natural = measure(pieces(line["text"]), latin, line["bold"])

    # each word on its own says how big the letters are and where they sit; the middle answer
    # is used, so one word box that came out too tall (it happens on busy photos) changes nothing
    sizes, baselines = [], []

    for word in line["words"]:
        shape = measure(pieces(word["text"]), latin, line["bold"])

        if shape and shape[2] - shape[1] > 0:
            wy0, wy1 = word["box"][1] * scale, word["box"][3] * scale
            sizes.append(100 * (wy1 - wy0) / (shape[2] - shape[1]))
            baselines.append((wy1, shape[2]))

    if natural is None or natural[0] <= 0 or not sizes:
        y0, y1 = line["box"][1] * scale, line["box"][3] * scale
        return {"size": max(4.0, (y1 - y0) * 0.8), "spacing": 0.0, "baseline": y1, "ascent": (y1 - y0) * 0.8,
                "descent": (y1 - y0) * 0.2, "x0": x0}

    width, _, _, ascent, descent, left = natural
    by_height = median(sizes)
    by_width = 100 * (x1 - x0) / width

    # the width fits the line exactly when the reading has all its letters; when the two
    # disagree, letters were lost or added, and the height is the safer size
    fits = 1 / FIT_LIMIT <= by_width / by_height <= FIT_LIMIT
    size = max(4.0, min(400.0, by_width if fits else by_height))
    # letter spacing makes up the last difference, so the line ends where it ended; only a
    # little when the size came from the height, so letters never run into each other
    room = 0.2 if fits else 0.04
    spacing = ((x1 - x0) - width * size / 100) / max(1, len(line["text"]))

    return {"size": size, "spacing": max(-room * size, min(room * size, spacing)),
            "baseline": median(wy1 - bottom * size / 100 for wy1, bottom in baselines),
            "ascent": ascent * size / 100, "descent": descent * size / 100, "x0": x0 - left * size / 100}

def line_xml(line, scale, page_width, latin, shape_id):
    fit = fit_line(line, latin, scale)
    size, spacing = fit["size"], fit["spacing"]
    # the box's top is where the font's top is, so the letters land on their old baseline
    box_top = fit["baseline"] - fit["ascent"]
    line_height = fit["ascent"] + fit["descent"]
    runs = ""

    for kind, text in pieces(line["text"]):
        family = clean_text(font_for(kind, latin)).replace('"', "&quot;")
        bold = "<w:b/><w:bCs/>" if line["bold"] else ""
        twips = round(spacing * 20)
        half_points = max(2, round(size * 2))
        runs += (f'<w:r><w:rPr><w:rFonts w:ascii="{family}" w:hAnsi="{family}" w:cs="{family}" w:eastAsia="{family}"/>{bold}'
                 f'<w:color w:val="{line["color"]}"/>' + (f'<w:spacing w:val="{twips}"/>' if twips else "")
                 + f'<w:sz w:val="{half_points}"/><w:szCs w:val="{half_points}"/></w:rPr>'
                 f'<w:t xml:space="preserve">{clean_text(text)}</w:t></w:r>')

    # the box runs to the page edge: a renderer whose font runs a little wider never wraps the line
    x0 = fit["x0"]
    box = pymupdf.Rect(x0, box_top, max(x0 + 1, page_width), box_top + line_height * 1.6)

    return anchor_xml(shape_id, box, False, textbox_graphic(box, runs, line_height))

def background_bytes(image):
    # flat designs compress best as png, photos as jpeg
    png, jpg = BytesIO(), BytesIO()
    image.save(png, format="PNG", optimize=True)
    image.save(jpg, format="JPEG", quality=90)

    return min(png.getvalue(), jpg.getvalue(), key=len)

def write_image_docx(images, docx_path):
    # images: one PIL picture per page; returns (details, notes)
    languages, problem = choose_languages(images[0])

    if problem:
        raise RuntimeError(problem)

    latin = installed_font("Arial", "sans")
    doc = Document()
    shape_id, all_lines, unsure = 0, 0, []

    for number, image in enumerate(images):
        lines = read_lines(image, languages)
        clean, _ = erase_letters(image, lines)

        for line in lines:
            decide_bold(line, latin)
        scale = PAGE_LONG_SIDE / max(image.width, image.height)
        page_width, page_height = image.width * scale, image.height * scale

        section = doc.sections[0] if number == 0 else doc.add_section(WD_SECTION.NEW_PAGE)
        section.page_width, section.page_height = Pt(page_width), Pt(page_height)
        section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Pt(0)
        section.header_distance = section.footer_distance = Pt(0)

        paragraph = doc.add_paragraph()
        rid, _ = doc.part.get_or_add_image(BytesIO(background_bytes(clean)))
        page = pymupdf.Rect(0, 0, page_width, page_height)
        shape_id += 1
        paragraph._p.append(parse_xml(anchor_xml(shape_id, page, True, picture_graphic(shape_id, rid, page))))

        for line in lines:
            shape_id += 1
            paragraph._p.append(parse_xml(line_xml(line, scale, page_width, latin, shape_id)))
            unsure += [word["text"] for word in line["words"] if 0 <= word["conf"] < UNSURE]

        all_lines += len(lines)

    doc.save(docx_path)
    # the Word template names Office fonts a Mac may lack; script runs get fonts with their letters
    fit_fonts(docx_path, {})

    alphabets = " + ".join({"guj": "Gujarati", "hin": "Hindi", "mar": "Marathi", "eng": "English"}.get(code, code) for code in languages.split("+"))
    details = f"text placed where it stands, {len(images)} page{'s' if len(images) != 1 else ''}, {all_lines} lines read as {alphabets}"
    notes = [note for note in [packs_note()] if note]

    if not all_lines:
        notes.append("no text found in the picture, the Word file has the picture only")

    if unsure:
        shown = ", ".join(f"'{text}'" for text in unsure[:8])
        notes.append(f"check {len(unsure)} word{'s' if len(unsure) != 1 else ''} the OCR was unsure of: {shown}" + (" ..." if len(unsure) > 8 else ""))

    return details, notes

def image_to_docx(src, dst):
    details, notes = write_image_docx([load_picture(src)], dst)

    return [dst], [details] + notes
