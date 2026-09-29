import math

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
