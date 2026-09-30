import math
import re
import shutil
import subprocess
from collections import Counter, defaultdict

import numpy as np
import pikepdf
import pymupdf

# Word files have no soft masks or see-through text, so pdf2docx turns PDF shadows into
# solid boxes and doubled words. This copies the PDF without them before converting.

PAINT_OPS = {"f", "F", "f*", "B", "B*", "b", "b*", "S", "s"}
COLOR_OPS = {"g", "rg", "k", "sc", "scn", "cs"}
SHADOW_REACH = 0.35    # a copy offset under this share of the font size reads as a shadow
OVERDRAW_REACH = 0.05  # closer than this it is the same text painted twice
IDENTITY = (1, 0, 0, 1, 0, 0)

def multiply(m, n):
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a*a2 + b*c2, a*b2 + b*d2, c*a2 + d*c2, c*b2 + d*d2, e*a2 + f*c2 + e2, e*b2 + f*d2 + f2)

def font_name(resources, name):
    # labels differ per use (/F12, /F13) while the font is the same, so compare the font itself
    font = resources.get("/Font", {}).get(name)
    base = str(font.get("/BaseFont", name)) if font is not None else str(name)

    # drop the subset tag, "/ABCDEF+DejaVuSans" -> "DejaVuSans"
    return base.lstrip("/").split("+")[-1]

def numbers(operands):
    return tuple(float(x) for x in operands)

def shown_bytes(op, operands):
    if op == "TJ":
        return b"".join(bytes(item) for item in operands[0] if isinstance(item, pikepdf.String))

    return bytes(operands[-1])

def is_soft_shadow_image(image, fitz_doc):
    # blurred shadows and glows are one flat colour whose transparency fades out;
    # icons are one colour with sharp edges, photos have many colours
    if "/SMask" not in image or image.objgen[0] == 0 or image.SMask.objgen[0] == 0:
        return False

    pix = pymupdf.Pixmap(fitz_doc, image.objgen[0])
    mask = pymupdf.Pixmap(fitz_doc, image.SMask.objgen[0])
    colors = np.frombuffer(pix.samples, np.uint8).reshape(-1, pix.n)[:, :pix.n - pix.alpha]
    alpha = np.frombuffer(mask.samples, np.uint8).reshape(-1, mask.n)[:, 0]

    if len(colors) == len(alpha):
        colors = colors[alpha > 16]

    seen = alpha[alpha > 16]

    if len(colors) == 0 or len(seen) == 0:
        return False

    flat = (colors.max(axis=0).astype(int) - colors.min(axis=0)).max() <= 24
    soft = (seen < 240).mean() >= 0.5

    return bool(flat and soft)

class Cleaner:
    def __init__(self, fitz_doc):
        self.fitz_doc = fitz_doc
        self.done_forms = set()
        self.removed = {"shadow shapes": 0, "shadow pictures": 0, "shadow text": 0, "hidden text": 0}

    def clean(self, instructions, resources, state):
        # returns the instructions without shadows, or None when nothing changed
        output = list(instructions)
        stack = []
        texts = []
        tm = tlm = IDENTITY
        moved = False
        bt = 0
        changed = False

        def drop(i):
            output[i] = None

        for i, item in enumerate(instructions):
            if isinstance(item, pikepdf.ContentStreamInlineImage):
                continue

            operands, op = item.operands, str(item.operator)

            if op == "q":
                stack.append(dict(state))
            elif op == "Q" and stack:
                state = stack.pop()
            elif op == "cm":
                state["ctm"] = multiply(numbers(operands), state["ctm"])
            elif op == "gs":
                gs = resources.get("/ExtGState", {}).get(operands[0])

                if gs is not None:
                    smask = gs.get("/SMask")

                    if smask is not None:
                        state["smask"] = smask != pikepdf.Name("/None")

                    if gs.get("/ca") is not None:
                        state["alpha"] = float(gs.get("/ca"))
            elif op in COLOR_OPS:
                state["fill"] = (op, tuple(str(x) for x in operands))
            elif op == "Tf":
                state["font"], state["size"] = font_name(resources, operands[0]), float(operands[1])
            elif op == "TL":
                state["leading"] = float(operands[0])
            elif op == "BT":
                tm = tlm = IDENTITY
                moved = True
                bt += 1
            elif op in ("Td", "TD"):
                tx, ty = numbers(operands)
                if op == "TD":
                    state["leading"] = -ty
                tm = tlm = multiply((1, 0, 0, 1, tx, ty), tlm)
                moved = True
            elif op == "Tm":
                tm = tlm = numbers(operands)
                moved = True
            elif op in ("T*", "'", '"'):
                tm = tlm = multiply((1, 0, 0, 1, 0, -state["leading"]), tlm)
                moved = True

            if op in ("Tj", "TJ", "'", '"'):
                trm = multiply(tm, state["ctm"])
                texts.append({
                    "index": i, "op": op, "operands": operands, "bt": bt, "moved": moved,
                    "key": (state["font"], shown_bytes(op, operands)),
                    "size": state["size"] * math.sqrt(abs(trm[0]*trm[3] - trm[1]*trm[2])),
                    "at": (trm[4], trm[5]), "fill": state["fill"], "alpha": state["alpha"],
                })
                # the pen now sits after the text, somewhere we do not track
                moved = False

            elif op in PAINT_OPS and state["smask"]:
                # a shape painted through a soft mask is a blurred shadow or glow
                output[i] = pikepdf.ContentStreamInstruction([], pikepdf.Operator("n"))
                self.removed["shadow shapes"] += 1
                changed = True

            elif op == "sh" and state["smask"]:
                drop(i)
                self.removed["shadow shapes"] += 1
                changed = True

            elif op == "Do":
                xobject = resources.get("/XObject", {}).get(operands[0])

                if xobject is None:
                    continue

                if xobject.get("/Subtype") == pikepdf.Name.Image and is_soft_shadow_image(xobject, self.fitz_doc):
                    drop(i)
                    self.removed["shadow pictures"] += 1
                    changed = True

                elif xobject.get("/Subtype") == pikepdf.Name.Form and xobject.objgen not in self.done_forms:
                    self.done_forms.add(xobject.objgen)
                    inner = dict(state)
                    inner["ctm"] = multiply(numbers(xobject.get("/Matrix", IDENTITY)), state["ctm"])
                    cleaned = self.clean(pikepdf.parse_content_stream(xobject), xobject.get("/Resources", resources), inner)

                    if cleaned is not None:
                        xobject.write(pikepdf.unparse_content_stream(cleaned))

        for n, text in enumerate(texts):
            later = texts[n + 1:n + 5]

            # removing text is only safe when whatever is drawn next sets its own position
            if later and later[0]["bt"] == text["bt"] and not later[0]["moved"]:
                continue

            if text["alpha"] == 0:
                reason = "hidden text"
            elif text["moved"] and any(self.is_shadow(text, other) for other in later):
                reason = "shadow text"
            else:
                continue

            self.remove_text(output, text)
            self.removed[reason] += 1
            changed = True

        if not changed:
            return None

        flat = []

        for item in output:
            if item is not None:
                flat.extend(item if isinstance(item, list) else [item])

        return flat

    @staticmethod
    def is_shadow(text, other):
        # the same text drawn again just beside it, on top: the earlier copy is the shadow
        if not other["moved"] or other["key"] != text["key"] or other["alpha"] == 0:
            return False

        if abs(other["size"] - text["size"]) > 0.02 * text["size"]:
            return False

        distance = math.dist(text["at"], other["at"])

        if distance <= OVERDRAW_REACH * text["size"]:
            return True

        # letters drawn one by one sit a letter apart, so single letters never count
        return other["fill"] != text["fill"] and distance <= SHADOW_REACH * text["size"] and len(text["key"][1]) >= 2

    @staticmethod
    def remove_text(output, text):
        i, op, operands = text["index"], text["op"], text["operands"]

        # ' and " also move to the next line, keep that part
        if op == "'":
            output[i] = pikepdf.ContentStreamInstruction([], pikepdf.Operator("T*"))
        elif op == '"':
            output[i] = [
                pikepdf.ContentStreamInstruction([operands[0]], pikepdf.Operator("Tw")),
                pikepdf.ContentStreamInstruction([operands[1]], pikepdf.Operator("Tc")),
                pikepdf.ContentStreamInstruction([], pikepdf.Operator("T*")),
            ]
        else:
            output[i] = None

def remove_shadows(src, dst):
    # writes a copy of src without shadows to dst, returns {kind: count removed}
    fitz_doc = pymupdf.open(src)

    with pikepdf.open(src) as pdf:
        cleaner = Cleaner(fitz_doc)

        for page in pdf.pages:
            if "/Contents" not in page.obj:
                continue

            state = {"ctm": IDENTITY, "smask": False, "alpha": 1.0, "fill": None, "font": None, "size": 0.0, "leading": 0.0}
            cleaned = cleaner.clean(pikepdf.parse_content_stream(page), page.obj.get("/Resources", pikepdf.Dictionary()), state)

            if cleaned is not None:
                page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(cleaned))

        pdf.save(dst)

    fitz_doc.close()

    return {kind: count for kind, count in cleaner.removed.items() if count}

# Letters the PDF does not name: a font may draw "Th" as one joined glyph and never say
# which letters it stands for. Readers then show the glyph's number, which Pages draws as ?.
# OCR reads the whole word ("Thanking"), the known letters ("?anking") give away the rest,
# and the font's ToUnicode map gets the missing entry so every converter reads it right.

UNKNOWN = "\ufffd"
PLAIN = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE  # unknown -> U+FFFD
CODED = pymupdf.TEXTFLAGS_RAWDICT | pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE   # unknown -> its code
OCR_DPI = 400
VOTES_NEEDED = 3

def unnamed_words(page):
    # (font, [(char, code of an unnamed glyph or None)], bbox) for words holding unnamed glyphs
    plain = page.get_text("rawdict", flags=PLAIN)["blocks"]
    coded = page.get_text("rawdict", flags=CODED)["blocks"]

    for plain_block, coded_block in zip(plain, coded):
        for plain_line, coded_line in zip(plain_block.get("lines", []), coded_block.get("lines", [])):
            for plain_span, coded_span in zip(plain_line["spans"], coded_line["spans"]):
                if len(plain_span["chars"]) != len(coded_span["chars"]):
                    continue

                word = []

                for char, coded_char in list(zip(plain_span["chars"], coded_span["chars"])) + [(None, None)]:
                    if char is not None and not char["c"].isspace():
                        word.append((char, coded_char))
                        continue

                    if any(c["c"] == UNKNOWN for c, _ in word):
                        bbox = pymupdf.Rect()

                        for c, _ in word:
                            bbox |= c["bbox"]

                        yield plain_span["font"], [(c["c"], ord(cc["c"]) if c["c"] == UNKNOWN else None) for c, cc in word], bbox

                    word = []

def ocr_word(page, bbox, language):
    pix = page.get_pixmap(dpi=OCR_DPI, clip=bbox, colorspace=pymupdf.csGRAY)

    # white margin instead of a wider clip, which would catch bits of the next word
    pad = max(8, pix.height // 2)
    canvas = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, pix.width + 2 * pad, pix.height + 2 * pad), False)
    canvas.clear_with(255)
    pix.set_origin(pad, pad)
    canvas.copy(pix, pix.irect)

    # psm 7: the picture is one line of text
    result = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "7", "-l", language], input=canvas.tobytes("png"), capture_output=True, timeout=60)

    return result.stdout.decode("utf-8", "ignore").strip()

def recover(word, text):
    # "?anking" read as "Thanking" -> ("Th",)
    pattern = "".join("(.{1,4}?)" if code is not None else re.escape(char) for char, code in word)
    match = re.fullmatch(pattern, re.sub(r"\s+", "", text))

    if match is None or not all(group.isprintable() for group in match.groups()):
        return None

    return match.groups()

def cmap_codes(cmap):
    # the codes a ToUnicode map already names
    codes = set()

    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        codes.update(int(src, 16) for src in re.findall(r"<([0-9A-Fa-f]+)>\s*<[0-9A-Fa-f]*>", block))

    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        for low, high in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(?:<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            codes.update(range(int(low, 16), int(high, 16) + 1))

    return codes

def count_unknown(path):
    with pymupdf.open(path) as doc:
        return sum(page.get_text("text", flags=PLAIN).count(UNKNOWN) for page in doc)

def name_unknown_letters(src, dst, language="eng"):
    # writes dst with the unnamed glyphs named when OCR could read them; returns (letters named, letters left)
    before = count_unknown(src)

    if before == 0 or shutil.which("tesseract") is None:
        return 0, before

    votes = defaultdict(Counter)

    with pymupdf.open(src) as doc:
        for page in doc:
            for font, word, bbox in unnamed_words(page):
                codes = [code for _, code in word if code is not None]

                # three matching reads settle a glyph, no need to OCR every "The"
                if all(votes[(font, code)] and votes[(font, code)].most_common(1)[0][1] >= VOTES_NEEDED for code in codes):
                    continue

                groups = recover(word, ocr_word(page, bbox, language))

                for code, text in zip(codes, groups or []):
                    votes[(font, code)][text] += 1

    names = {key: counter.most_common(1)[0][0] for key, counter in votes.items() if counter}

    if not names:
        return 0, before

    with pikepdf.open(src) as pdf:
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Dictionary) or obj.get("/Type") != pikepdf.Name.Font or "/ToUnicode" not in obj:
                continue

            font = str(obj.get("/BaseFont", "")).lstrip("/").split("+")[-1]
            cmap = obj.ToUnicode.read_bytes().decode("latin-1")
            named = cmap_codes(cmap)
            width = 4 if obj.get("/Subtype") == pikepdf.Name.Type0 else 2
            # only codes this font leaves unnamed: another subset of the same font may use the code for a real letter
            entries = [f"<{code:0{width}X}> <{text.encode('utf-16-be').hex().upper()}>" for (name, code), text in names.items() if name == font and code not in named]

            if not entries:
                continue

            # at most 100 entries per block, and a later entry wins over an earlier one
            blocks = "".join(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n" for chunk in (entries[i:i + 100] for i in range(0, len(entries), 100)))
            obj.ToUnicode.write(cmap.replace("endcmap", blocks + "endcmap", 1).encode("latin-1"))

        pdf.save(dst)

    after = count_unknown(dst)

    return before - after, after
