import re
from io import BytesIO
from xml.sax.saxutils import escape

import pymupdf
from docx import Document
from docx.enum.section import WD_SECTION
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Pt

# Exact layout: every page becomes a picture of its design (text removed) behind
# editable text boxes placed where each line was. Nothing reflows, so nothing drifts.

BACKGROUND_DPI = 150
EMU = 12700                 # EMUs per point
DESIGNED_SHARE = 0.15       # share of text lines on coloured panels that marks a designed page
WPS = 'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
COMMON_FAMILIES = {"TimesNewRoman": "Times New Roman", "Times": "Times New Roman", "CourierNew": "Courier New", "Courier": "Courier New", "Arial": "Arial", "Helvetica": "Helvetica"}

def font_family(fonts, name):
    # pdf2docx reads the family from the embedded font file; fonts that are not embedded fall back to their name
    found = fonts.get(name)

    if found is not None:
        family = found.name
    else:
        base = re.sub(r"(PSMT|MT|PS)$", "", re.split(r"[-,]", name.split("+")[-1])[0])
        family = COMMON_FAMILIES.get(base, base)

    return plain_family(family)

def plain_family(family):
    # bold and italic are switched on in the run, "Charis SIL Bold" is no family Word knows
    return re.sub(r"(\s+(Regular|Bold|Italic|Oblique))+$", "", family, flags=re.I) or family

def colored(fill):
    return fill is not None and min(fill) < 0.94

def looks_designed(doc):
    # designed pages put text on coloured panels, banners and pictures; letters and reports put it on white paper
    lines = on_panel = 0

    for page in doc:
        panels = [pymupdf.Rect(d["rect"]) for d in page.get_drawings() if colored(d.get("fill")) and d.get("fill_opacity") != 0]
        panels += [pymupdf.Rect(image["bbox"]) for image in page.get_image_info()]

        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                x0, y0, x1, y1 = line["bbox"]
                center = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)
                lines += 1

                if any(center in panel for panel in panels):
                    on_panel += 1

    return lines > 0 and on_panel / lines >= DESIGNED_SHARE

def background_png(doc, number):
    # a copy of the page with every letter taken out; shapes, photos and shadows stay as they look
    copy = pymupdf.open()
    copy.insert_pdf(doc, from_page=number, to_page=number)
    page = copy[0]
    page.add_redact_annot(page.rect, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    pix = page.get_pixmap(dpi=BACKGROUND_DPI)

    # flat designs compress best as png, photos as jpeg
    return min(pix.tobytes("png"), pix.tobytes("jpg", jpg_quality=90), key=len)

def clean_text(text):
    # XML 1.0 has no room for control characters
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text))

def run_xml(span, fonts):
    family = escape(font_family(fonts, span["font"]), {'"': "&quot;"})
    bold = span["flags"] & 16 or "bold" in span["font"].lower()
    italic = span["flags"] & 2 or re.search(r"italic|oblique", span["font"], re.I)
    size = max(2, round(span["size"] * 2))

    # element order inside w:rPr is fixed by the schema: rFonts, b, i, color, sz, szCs
    return (
        f'<w:r><w:rPr><w:rFonts w:ascii="{family}" w:hAnsi="{family}" w:cs="{family}" w:eastAsia="{family}"/>'
        + ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "")
        + f'<w:color w:val="{span["color"]:06X}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
        f'<w:t xml:space="preserve">{clean_text(span["text"])}</w:t></w:r>'
    )

def anchor_xml(shape_id, rect, behind, graphic):
    return (
        f'<w:r {nsdecls("w", "wp", "a", "pic", "r")} {WPS}><w:drawing>'
        f'<wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="{251658240 + shape_id}" '
        f'behindDoc="{1 if behind else 0}" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="page"><wp:posOffset>{round(rect.x0 * EMU)}</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="page"><wp:posOffset>{round(rect.y0 * EMU)}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{round(rect.width * EMU)}" cy="{round(rect.height * EMU)}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
        f'<wp:docPr id="{shape_id}" name="Shape {shape_id}"/><wp:cNvGraphicFramePr/>'
        f'<a:graphic>{graphic}</a:graphic></wp:anchor></w:drawing></w:r>'
    )

def picture_graphic(shape_id, rid, rect):
    return (
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
        f'<pic:nvPicPr><pic:cNvPr id="{shape_id}" name="Page design {shape_id}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{round(rect.width * EMU)}" cy="{round(rect.height * EMU)}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData>'
    )

def textbox_graphic(rect, runs, line_height):
    return (
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp>'
        '<wps:cNvSpPr txBox="1"/><wps:spPr>'
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{round(rect.width * EMU)}" cy="{round(rect.height * EMU)}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></wps:spPr>'
        '<wps:txbx><w:txbxContent><w:p><w:pPr>'
        f'<w:spacing w:before="0" w:after="0" w:line="{round(line_height * 20)}" w:lineRule="atLeast"/>'
        f'</w:pPr>{runs}</w:p></w:txbxContent></wps:txbx>'
        # the box grows to its text: a clipped box would hide words
        '<wps:bodyPr rot="0" vert="horz" wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t" anchorCtr="0"><a:spAutoFit/></wps:bodyPr>'
        '</wps:wsp></a:graphicData>'
    )

def write_exact_docx(design_pdf, text_pdf, docx_path):
    # design_pdf gives the look (shadows and all), text_pdf the words (shadow copies already removed)
    from pdf2docx.font.Fonts import Fonts

    design = pymupdf.open(design_pdf)
    text = pymupdf.open(text_pdf)
    fonts = Fonts.extract(text)
    word = Document()
    shape_id = 0

    for number, page in enumerate(text):
        section = word.sections[0] if number == 0 else word.add_section(WD_SECTION.NEW_PAGE)
        section.page_width, section.page_height = Pt(page.rect.width), Pt(page.rect.height)
        section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Pt(18)

        paragraph = word.add_paragraph()
        rid, _ = word.part.get_or_add_image(BytesIO(background_png(design, number)))
        shape_id += 1
        paragraph._p.append(parse_xml(anchor_xml(shape_id, page.rect, True, picture_graphic(shape_id, rid, page.rect))))

        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [span for span in line["spans"] if span["alpha"] > 0 and span["text"]]

                if not spans or not "".join(span["text"] for span in spans).strip():
                    continue

                # boxes run to the page edge: a stand-in font wider than the PDF's still fits, and the
                # text stays put because it starts at the left and the box is see-through
                box = pymupdf.Rect(line["bbox"])
                box.x1 = max(box.x1, page.rect.x1 - 1)
                shape_id += 1
                runs = "".join(run_xml(span, fonts) for span in spans)
                paragraph._p.append(parse_xml(anchor_xml(shape_id, box, False, textbox_graphic(box, runs, line["bbox"][3] - line["bbox"][1]))))

    word.save(docx_path)
