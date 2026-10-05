from statistics import median

import numpy as np
from PIL import Image, ImageDraw

from tools.image_word import decide_bold, fill_in, fit_line, font_for, letters_mask, pieces, pil_font, study_line

# Changing words inside a picture. Only what the change touches is redrawn:
#   - the changed words are painted out and drawn again in the line's size, colour and weight,
#     on the line's baseline, in a font that has their letters
#   - words after them (and before them, on a centred line) slide as copies of their own
#     pixels, so a longer or shorter word never overlaps or leaves a hole, and their shadows,
#     outlines and exact letter shapes come along
#   - every other line is left exactly as it was, pixel for pixel

CENTERED = 0.02     # a line whose middle is this close to the picture's middle (share of width) is centred

def word_spans(words):
    # where each word sits in the line's text, words joined by single spaces
    spans, at = [], 0

    for word in words:
        spans.append((at, at + len(word["text"])))
        at += len(word["text"]) + 1

    return spans

def ink_box(parts, latin, bold, size):
    # (left, width) of the letters' ink when the pieces are drawn one after another at this size
    left, right, cursor = None, None, 0.0

    for kind, text in parts:
        font = pil_font(font_for(kind, latin), bold, max(1, round(size)))
        x0, _, x1, _ = font.getbbox(text, anchor="ls")

        if text.strip():
            left = cursor + x0 if left is None else left
            right = cursor + x1

        cursor += font.getlength(text)

    return (left or 0.0), ((right - left) if left is not None else 0.0)

def paste_moved(pixels, original, line, words, shift):
    # each word's own pixels at its new place, blended in at its letters' soft edges
    import cv2

    if not shift or not words:
        return

    height, width = pixels.shape[:2]
    mask = letters_mask((height, width), line, words).astype(np.float32)
    soft = cv2.GaussianBlur(mask, (5, 5), 0)
    moved_soft = np.zeros_like(soft)
    moved_pixels = np.zeros_like(pixels, dtype=np.float32)

    # every column of the source moves by shift; what slides off the picture is dropped
    src = slice(max(0, -shift), min(width, width - shift))
    dst = slice(max(0, shift), min(width, width + shift))
    moved_soft[:, dst] = soft[:, src]
    moved_pixels[:, dst] = original[:, src]

    alpha = moved_soft[:, :, None]
    pixels[:] = (alpha * moved_pixels + (1 - alpha) * pixels).astype(np.uint8)

def draw_text(pixels, text, x, baseline, line, latin, size):
    # the new words, drawn with their letters' ink starting at x, sitting on the baseline
    layer = Image.new("RGBA", (pixels.shape[1], pixels.shape[0]), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    parts = pieces(text)
    left, _ = ink_box(parts, latin, line["bold"], size)
    cursor = x - left

    shadow = line.get("shadow")

    for kind, piece in parts:
        font = pil_font(font_for(kind, latin), line["bold"], max(1, round(size)))

        # the shadow first, so the letters sit on top of it as in the picture
        if shadow:
            colour, dx, dy = shadow
            draw.text((cursor + dx, baseline + dy), piece, font=font, fill=tuple(colour) + (255,), anchor="ls")

        draw.text((cursor, baseline), piece, font=font, fill=line["rgb"] + (255,), anchor="ls")
        cursor += font.getlength(piece)

    base = Image.fromarray(pixels).convert("RGBA")
    pixels[:] = np.array(Image.alpha_composite(base, layer).convert("RGB"))

def ink_edges(word):
    # where the word's letters really start and end; Tesseract's box can stop short of them
    columns = word["ink"].any(axis=0) if word.get("ink") is not None and word["ink"].size else None

    if columns is None or not columns.any():
        return word["box"][0], word["box"][2]

    used = columns.nonzero()[0]

    return word["area"][0] + int(used[0]), word["area"][0] + int(used[-1]) + 1

def edit_line(pixels, original, line, changes, latin):
    # changes: [(find, replace)], each found in line["text"]; returns the line's new text and
    # the box (x0, y0, x1, y1) the line now covers
    words, text = line["words"], line["text"]
    spans = word_spans(words)
    hits = []

    for find, replace in changes:
        start = text.find(find)
        hits.append((start, start + len(find), replace))

    touched = [n for n, (a, b) in enumerate(spans) if any(a < end and b > start for start, end, _ in hits)]
    first, last = touched[0], touched[-1]
    seg_start, seg_end = spans[first][0], spans[last][1]
    segment = text[seg_start:seg_end]

    # rewritten from the right, so earlier positions in the segment stay where they were
    for start, end, replace in sorted(hits, reverse=True):
        segment = segment[:start - seg_start] + replace + segment[end - seg_start:]

    segment = " ".join(segment.split())
    new_text = " ".join((text[:seg_start] + segment + text[seg_end:]).split())

    fit = fit_line(line, latin)
    size, baseline = fit["size"], fit["baseline"]
    edges = [ink_edges(word) for word in words]
    old_x0, old_x1 = edges[first][0], edges[last][1]
    gaps = [edges[n + 1][0] - edges[n][1] for n in range(len(words) - 1)]
    gap = median(gaps) if gaps else 0.3 * size

    if segment:
        _, new_width = ink_box(pieces(segment), latin, line["bold"], size)
        delta = new_width - (old_x1 - old_x0)
    else:
        # the words are gone, and so is one of the spaces around them
        new_width = 0
        delta = -(old_x1 - old_x0) - (gap if len(words) > len(touched) else 0)

    width = pixels.shape[1]
    lx0, lx1 = edges[0][0], edges[-1][1]
    centred = abs((lx0 + lx1) / 2 - width / 2) < CENTERED * width
    before = round(-delta / 2) if centred else 0
    after = round(delta / 2) if centred else round(delta)

    prefix, suffix = words[:first], words[last + 1:]
    moving = (prefix if before else []) + (suffix if after else [])
    painted_out = words[first:last + 1] + moving

    pixels[:] = fill_in(pixels, letters_mask(pixels.shape[:2], line, painted_out))
    paste_moved(pixels, original, line, prefix, before)
    paste_moved(pixels, original, line, suffix, after)

    if segment:
        # after a removed start of line, the new words start where the old ones did
        draw_text(pixels, segment, old_x0 + before, baseline, line, latin, size)

    new_x0 = (lx0 + before) if prefix else old_x0 + before
    new_x1 = (lx1 + after) if suffix else old_x0 + before + new_width

    return new_text, (min(new_x0, lx0), line["box"][1], max(new_x1, lx1), line["box"][3])

def apply_edits(image, lines, edits, latin):
    # edits: [{"line": index into lines, "find": ..., "replace": ...}]; returns the new picture
    # and, per changed line, (index, new text, box it covers)
    original = np.array(image)
    pixels = original.copy()

    # colours, weights and letter pixels all come from the untouched picture
    for line in lines:
        study_line(original, line)
        decide_bold(line, latin)

    results = []

    for index in sorted({edit["line"] for edit in edits}):
        changes = [(edit["find"], edit["replace"]) for edit in edits if edit["line"] == index]
        new_text, box = edit_line(pixels, original, lines[index], changes, latin)
        results.append((index, new_text, box))

    return Image.fromarray(pixels), results
