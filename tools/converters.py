import csv
import html
import io
import json
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# Every direct conversion is one edge: (from, to, function, what it needs).
# tools/convert.py chains edges, so heic -> docx runs heic -> png -> pdf -> docx.
# A function takes (src, dst) and returns (files it wrote, notes for the reply).

IMAGES = ["png", "jpg", "webp", "bmp", "gif", "tiff", "ico"]
LOSSLESS = {"png", "bmp", "gif", "tiff"}
NO_ALPHA = {"jpg", "bmp"}
AUDIO = ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"]
VIDEO = ["mp4", "mov", "mkv", "webm", "avi"]
DOCS = ["docx", "doc", "odt", "rtf"]
SHEETS = ["xlsx", "xls", "ods"]
SLIDES = ["pptx", "ppt", "odp"]
PAGE_DPI = 200          # pdf pages as pictures
MAX_PAGE = 842          # a picture's page is at most A4's long side, in points
LONG_NUMBER = 10        # longer digit runs (phone, ID numbers) stay text in Excel

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    IMAGES.append("heic")
except ImportError:
    pass

class Edge:
    def __init__(self, source, target, run, needs=None, hint=""):
        self.source, self.target, self.run = source, target, run
        self.needs = needs      # a check that says whether the tool is here
        self.hint = hint        # how to get the tool when it is not

    def available(self):
        return self.needs is None or bool(self.needs())

EDGES = []

def edges(sources, targets, run, needs=None, hint=""):
    for source in sources:
        for target in targets:
            if source != target:
                EDGES.append(Edge(source, target, run, needs, hint))

# ---------- pictures (Pillow) ----------

def image_to_image(src, dst):
    from PIL import Image, ImageOps, ImageSequence

    target = dst.suffix[1:]
    notes = []

    with Image.open(src) as image:
        frames = getattr(image, "n_frames", 1)

        if frames > 1 and target in ("gif", "webp"):
            # animations stay animated
            pictures = [frame.copy() for frame in ImageSequence.Iterator(image)]
            pictures[0].save(dst, save_all=True, append_images=pictures[1:], duration=image.info.get("duration", 100), loop=image.info.get("loop", 0))
            return [dst], notes

        if frames > 1:
            notes.append(f"{src.name} is animated, kept the first frame")

        icc = image.info.get("icc_profile")
        # phones store "turn this photo" as a flag; apply it so the picture stays upright
        picture = ImageOps.exif_transpose(image)
        picture.load()
        exif = picture.getexif()

    if target in NO_ALPHA and ("A" in picture.getbands() or "transparency" in picture.info):
        rgba = picture.convert("RGBA")

        if rgba.getchannel("A").getextrema()[0] < 255:
            notes.append(f"transparent parts filled with white, {target.upper()} has no transparency")

        # on white, not black: dropping the alpha channel alone turns see-through parts black
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        picture = flat
    elif target in NO_ALPHA or picture.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        picture = picture.convert("RGB")

    options = {"icc_profile": icc} if icc else {}

    if target == "jpg":
        # 4:4:4 keeps coloured edges sharp
        options.update(quality=95, subsampling=0, optimize=True, exif=exif)
    elif target == "webp" and src.suffix[1:].lower() in LOSSLESS:
        options.update(lossless=True)
    elif target == "webp":
        options.update(quality=95)
    elif target == "tiff":
        options.update(compression="tiff_lzw")
    elif target == "ico":
        if max(picture.size) > 256:
            notes.append("icons are at most 256x256, scaled down")
        options = {}
    elif target == "heic":
        options.update(quality=90)

    picture.save(dst, format={"jpg": "JPEG", "tiff": "TIFF", "heic": "HEIF"}.get(target, target.upper()), **options)

    return [dst], notes

def image_to_pdf(src, dst):
    from PIL import Image

    with Image.open(src) as image:
        width, height = image.size
        dpi = image.info.get("dpi", (96, 96))[0] or 96
        upright = image.getexif().get(0x0112, 1) == 1

        # jpg and png go in untouched; anything else, or a sideways photo, becomes png first
        if src.suffix.lower() in (".jpg", ".jpeg", ".png") and upright:
            data = src.read_bytes()
        else:
            from PIL import ImageOps
            buffer = io.BytesIO()
            ImageOps.exif_transpose(image).save(buffer, format="PNG")
            data = buffer.getvalue()
            width, height = Image.open(io.BytesIO(data)).size

    # true size for scans that know their dpi, but never larger than A4
    page_w, page_h = width * 72 / dpi, height * 72 / dpi
    scale = min(1, MAX_PAGE / max(page_w, page_h))
    doc = pymupdf.open()
    page = doc.new_page(width=page_w * scale, height=page_h * scale)
    page.insert_image(page.rect, stream=data)
    doc.save(dst)

    return [dst], []

def pdf_to_images(src, dst):
    written = []

    with pymupdf.open(src) as doc:
        if doc.needs_pass:
            raise ValueError(f"{src.name} is password protected, unlock it first")

        for page in doc:
            out = dst if doc.page_count == 1 else dst.with_name(f"{dst.stem}-{page.number + 1}{dst.suffix}")
            page.get_pixmap(dpi=PAGE_DPI).save(out, jpg_quality=95)
            written.append(out)

    return written, ([f"{len(written)} pages, one picture each"] if len(written) > 1 else [])

def image_to_docx(src, dst):
    # the picture stays as the page, its words become text boxes where they stand (tools/image_word.py)
    from tools.image_word import image_to_docx as convert

    return convert(src, dst)

edges(IMAGES, IMAGES, image_to_image)
edges(IMAGES, ["pdf"], image_to_pdf)
edges(IMAGES, ["docx"], image_to_docx)
edges(["pdf"], ["png", "jpg"], pdf_to_images)

# ---------- documents (PyMuPDF, python-docx, markdown) ----------

def pdf_to_docx(src, dst):
    from tools.documents import write_word

    details, notes = write_word(src, dst)
    return [dst], [details] + notes

def pdf_to_txt(src, dst):
    from tools.documents import is_scanned, pdf_text, run_ocr

    notes = []

    with pymupdf.open(src) as doc:
        scanned = sum(is_scanned(page) for page in doc) * 2 >= doc.page_count

    source = src

    with tempfile.TemporaryDirectory() as tmp:
        if scanned:
            error = run_ocr(src, Path(tmp) / "ocr.pdf")

            if error is None:
                source = Path(tmp) / "ocr.pdf"
                notes.append("scanned, text read with OCR")
            else:
                notes.append(f"scanned, OCR failed: {error}")

        dst.write_text(pdf_text(source), encoding="utf-8")

    return [dst], notes

def docx_to_txt(src, dst):
    from tools.documents import docx_text

    dst.write_text(docx_text(src), encoding="utf-8")
    return [dst], []

def blocks(doc):
    # paragraphs and tables in reading order; python-docx lists them separately
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, doc)
        elif child.tag.endswith("}tbl"):
            yield Table(child, doc)

def docx_to_md(src, dst):
    lines = []
    number = 0

    for block in blocks(Document(src)):
        if hasattr(block, "rows"):
            rows = [[cell.text.replace("|", "\\|").replace("\n", " ") for cell in row.cells] for row in block.rows]

            if rows:
                lines += ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * len(rows[0])]
                lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
                lines.append("")

            continue

        parts = []

        # runs and links in order; paragraph.runs alone skips the text inside links
        for item in block.iter_inner_content():
            text = item.text

            if getattr(item, "address", None):
                text = f"[{text}]({item.address})"
            elif text.strip() and getattr(item, "bold", None):
                text = f"**{text}**"
            elif text.strip() and getattr(item, "italic", None):
                text = f"*{text}*"

            parts.append(text)

        text = "".join(parts)
        style = block.style.name if block.style is not None else ""

        level = int(style[-1]) - 1 if style.startswith("List") and style[-1:].isdigit() else 0
        number = number + 1 if style.startswith("List Number") else 0

        if style.startswith("Heading") and style[-1:].isdigit():
            text = "#" * int(style[-1]) + " " + text
        elif style == "Title":
            text = "# " + text
        elif style.startswith("List Number"):
            text = "  " * level + f"{number}. " + text
        elif style.startswith("List"):
            text = "  " * level + "- " + text

        # items of one list stay together; a new list or anything else gets a blank line before it
        kind = lambda line: "number" if re.match(r"\s*\d+\. ", line) else "bullet" if re.match(r"\s*- ", line) else None
        previous = lines[-2] if len(lines) > 1 and lines[-1] == "" else ""

        if kind(previous) and (kind(text) == kind(previous) or level > 0):
            lines[-1] = text
            lines.append("")
        else:
            lines += [text, ""]

    dst.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return [dst], ["Markdown keeps text, headings, lists and tables, not pictures or page layout"]

def txt_to_docx(src, dst):
    doc = Document()

    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        doc.add_paragraph(line)

    doc.save(dst)
    return [dst], []

def list_indent_to_four(text):
    # Python-Markdown nests lists at 4 spaces, most editors write 2: scale the file's own step to 4
    indents = [len(m.group(1)) for m in re.finditer(r"^( +)(?:[-*+]|\d+\.) ", text, re.M)]
    step = min(indents, default=4)

    if step >= 4:
        return text

    return re.sub(r"^( +)(?=(?:[-*+]|\d+\.) )", lambda m: " " * (len(m.group(1)) // step * 4), text, flags=re.M)

def md_to_html(src, dst):
    import markdown

    text = list_indent_to_four(src.read_text(encoding="utf-8", errors="replace"))
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    dst.write_text(page_html(src.stem, body), encoding="utf-8")
    return [dst], []

def txt_to_html(src, dst):
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "".join(f"<p>{html.escape(line) or '&nbsp;'}</p>" for line in lines)
    dst.write_text(page_html(src.stem, body), encoding="utf-8")
    return [dst], []

def page_html(title, body):
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            '<style>body{font-family:sans-serif;font-size:11pt;line-height:1.4} p{margin:0 0 6pt 0}'
            'table{border-collapse:collapse} td,th{border:1px solid #888;padding:3pt 6pt} pre{font-family:monospace}</style>'
            f'</head><body>{body}</body></html>')

def html_to_pdf(src, dst):
    story = pymupdf.Story(html=src.read_text(encoding="utf-8", errors="replace"), archive=str(src.parent))
    writer = pymupdf.DocumentWriter(str(dst))
    page = pymupdf.paper_rect("a4")
    more = True

    while more:
        device = writer.begin_page(page)
        more, _ = story.place(page + (50, 50, -50, -50))
        story.draw(device)
        writer.end_page()

    writer.close()
    return [dst], []

def html_root(src):
    from lxml import html as lxml_html

    root = lxml_html.fromstring(src.read_text(encoding="utf-8", errors="replace") or "<p></p>")
    body = root.find("body")
    return body if body is not None else root

def html_to_txt(src, dst):
    root = html_root(src)

    # text_content() glues blocks together; give each block its own line
    for element in root.iter("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"):
        element.tail = "\n" + (element.tail or "")

    text = re.sub(r"\n{3,}", "\n\n", root.text_content())
    dst.write_text(text.strip() + "\n", encoding="utf-8")
    return [dst], []

def add_link(paragraph, url, text):
    # python-docx has no link call: a w:hyperlink pointing at an external relationship
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    run, props, color, underline, words = (OxmlElement(tag) for tag in ("w:r", "w:rPr", "w:color", "w:u", "w:t"))
    color.set(qn("w:val"), "0563C1")
    underline.set(qn("w:val"), "single")
    props.extend([color, underline])
    words.text = text
    words.set(qn("xml:space"), "preserve")
    run.extend([props, words])
    link.append(run)
    paragraph._p.append(link)

def add_inline(paragraph, element, bold=False, italic=False, code=False):
    def run(text):
        if text:
            r = paragraph.add_run(text)
            r.bold, r.italic = bold or None, italic or None
            if code:
                r.font.name = "Courier New"

    run(element.text)

    for child in element:
        tag = child.tag if isinstance(child.tag, str) else ""

        if tag == "br":
            paragraph.add_run().add_break()
        elif tag == "a" and child.get("href", "").startswith(("http://", "https://", "mailto:")):
            add_link(paragraph, child.get("href"), child.text_content())
        else:
            add_inline(paragraph, child, bold or tag in ("strong", "b"), italic or tag in ("em", "i"), code or tag == "code")

        run(child.tail)

def add_block(doc, element, depth=0):
    tag = element.tag if isinstance(element.tag, str) else ""

    if re.fullmatch(r"h[1-6]", tag):
        add_inline(doc.add_heading(level=int(tag[1])), element)
    elif tag in ("ul", "ol"):
        style = ("List Bullet" if tag == "ul" else "List Number") + (f" {min(depth, 2) + 1}" if depth else "")

        for item in element.findall("li"):
            paragraph = doc.add_paragraph(style=style)
            inner = [child for child in item if child.tag in ("ul", "ol")]

            for child in inner:
                item.remove(child)

            add_inline(paragraph, item)

            for child in inner:
                add_block(doc, child, depth + 1)
    elif tag == "pre":
        paragraph = doc.add_paragraph()

        for n, line in enumerate(element.text_content().splitlines()):
            if n:
                paragraph.add_run().add_break()
            paragraph.add_run(line).font.name = "Courier New"
    elif tag == "table":
        rows = element.findall(".//tr")
        width = max((len(row.findall("td") + row.findall("th")) for row in rows), default=0)

        if rows and width:
            table = doc.add_table(rows=len(rows), cols=width, style="Table Grid")

            for r, row in enumerate(rows):
                for c, cell in enumerate([cell for cell in row if cell.tag in ("td", "th")]):
                    add_inline(table.cell(r, c).paragraphs[0], cell, bold=cell.tag == "th")
    elif tag == "blockquote":
        add_inline(doc.add_paragraph(style="Quote"), element)
    elif tag in ("p", "hr") or (tag and not len(element)):
        add_inline(doc.add_paragraph(), element)
    else:
        # div, section, article and friends only hold other blocks
        for child in element:
            add_block(doc, child, depth)

def html_to_docx(src, dst):
    doc = Document()

    for element in html_root(src):
        add_block(doc, element)

    doc.save(dst)
    return [dst], []

edges(["pdf"], ["docx"], pdf_to_docx)
edges(["pdf"], ["txt"], pdf_to_txt)
# md before txt: a docx -> pdf without office apps then keeps headings, lists and tables
edges(["docx"], ["md"], docx_to_md)
edges(["docx"], ["txt"], docx_to_txt)
edges(["txt"], ["docx"], txt_to_docx)
edges(["md"], ["html"], md_to_html)
edges(["txt"], ["html"], txt_to_html)
edges(["html"], ["pdf"], html_to_pdf)
edges(["html"], ["txt"], html_to_txt)
edges(["html"], ["docx"], html_to_docx)

# ---------- spreadsheets and data (openpyxl, csv, json) ----------

def read_csv(src):
    data = src.read_bytes()
    text = data.decode("utf-8-sig") if data[:3] == b"\xef\xbb\xbf" else data.decode("utf-8", errors="replace")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    return list(csv.reader(io.StringIO(text), dialect))

def cell_value(value):
    # numbers become numbers, but "0981..." and long IDs stay text so Excel does not mangle them
    if re.fullmatch(r"-?(0|[1-9]\d{0,%d})" % (LONG_NUMBER - 1), value):
        return int(value)

    if re.fullmatch(r"-?(0|[1-9]\d*)\.\d+", value) and len(value) <= 16:
        return float(value)

    return value

def csv_to_xlsx(src, dst):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = src.stem[:31] or "Sheet1"

    for row in read_csv(src):
        sheet.append([cell_value(value) for value in row])

    book.save(dst)
    return [dst], []

def xlsx_to_csv(src, dst):
    from openpyxl import load_workbook

    book = load_workbook(src, read_only=True, data_only=True)
    written = []

    for sheet in book.worksheets:
        # one sheet keeps the plain name; several get their sheet name added
        title = re.sub(r"[^\w-]+", "_", sheet.title)
        out = dst if len(book.worksheets) == 1 else dst.with_name(f"{dst.stem}-{title}{dst.suffix}")

        with open(out, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)

            for row in sheet.iter_rows(values_only=True):
                writer.writerow(["" if value is None else value for value in row])

        written.append(out)

    book.close()
    return written, ([f"{len(written)} sheets, one csv each"] if len(written) > 1 else [])

def csv_to_json(src, dst):
    rows = read_csv(src)
    header, body = (rows[0], rows[1:]) if rows else ([], [])
    dst.write_text(json.dumps([dict(zip(header, row)) for row in body], indent=2, ensure_ascii=False), encoding="utf-8")
    return [dst], []

def json_to_csv(src, dst):
    data = json.loads(src.read_text(encoding="utf-8"))
    data = [data] if isinstance(data, dict) else data

    if not isinstance(data, list):
        raise ValueError("the JSON is not a list of rows")

    with open(dst, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        if data and all(isinstance(row, dict) for row in data):
            keys = list(dict.fromkeys(key for row in data for key in row))
            writer.writerow(keys)
            writer.writerows([[row.get(key, "") for key in keys] for row in data])
        else:
            writer.writerows([row if isinstance(row, list) else [row] for row in data])

    return [dst], []

def csv_to_html(src, dst):
    rows = read_csv(src)
    head = "".join(f"<th>{html.escape(value)}</th>" for value in rows[0]) if rows else ""
    body = "".join("<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in row) + "</tr>" for row in rows[1:])
    dst.write_text(page_html(src.stem, f"<table><tr>{head}</tr>{body}</table>"), encoding="utf-8")
    return [dst], []

edges(["csv"], ["xlsx"], csv_to_xlsx)
edges(["xlsx"], ["csv"], xlsx_to_csv)
edges(["csv"], ["json"], csv_to_json)
edges(["json"], ["csv"], json_to_csv)
edges(["csv"], ["html"], csv_to_html)

# ---------- audio and video (ffmpeg) ----------

FFMPEG_ARGS = {
    "mp3": ["-vn", "-q:a", "2"],
    "m4a": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "aac": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "wav": ["-vn"],
    "flac": ["-vn"],
    "ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "6"],
    "opus": ["-vn", "-c:a", "libopus", "-b:a", "128k"],
    # yuv420p plays everywhere; even sizes are what the h264 encoder needs
    "mp4": ["-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:a", "aac", "-movflags", "+faststart"],
    "mov": ["-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:a", "aac"],
    "mkv": ["-c:v", "libx264", "-crf", "20", "-c:a", "aac"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus"],
    "avi": ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame"],
    # a palette made from the clip itself keeps gif colours clean
    "gif": ["-vf", "fps=12,scale='min(640,iw)':-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"],
}

def ffmpeg(src, dst):
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)] + FFMPEG_ARGS[dst.suffix[1:]] + [str(dst)]
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "ffmpeg failed")

    return [dst], []

HAS_FFMPEG = lambda: shutil.which("ffmpeg")
FFMPEG_HINT = "ffmpeg (brew install ffmpeg)"
edges(AUDIO + VIDEO, AUDIO, ffmpeg, HAS_FFMPEG, FFMPEG_HINT)
edges(VIDEO + ["gif"], VIDEO, ffmpeg, HAS_FFMPEG, FFMPEG_HINT)
edges(VIDEO, ["gif"], ffmpeg, HAS_FFMPEG, FFMPEG_HINT)

# ---------- office apps, most faithful first: Word (Windows), Pages / Numbers / Keynote and textutil (Mac), LibreOffice ----------

def soffice():
    for candidate in [shutil.which("soffice"), shutil.which("libreoffice"),
                      "/Applications/LibreOffice.app/Contents/MacOS/soffice", r"C:\Program Files\LibreOffice\program\soffice.exe"]:
        if candidate and Path(candidate).exists():
            return candidate

    return None

def libreoffice(src, dst):
    with tempfile.TemporaryDirectory() as tmp:
        # its own profile, so a LibreOffice window that is already open does not block it
        profile = Path(tmp, "profile").as_uri()
        command = [soffice(), "--headless", "--norestore", f"-env:UserInstallation={profile}", "--convert-to", dst.suffix[1:], "--outdir", tmp, str(src)]
        subprocess.run(command, capture_output=True, timeout=300)
        out = Path(tmp) / f"{src.stem}{dst.suffix}"

        if not out.exists():
            raise RuntimeError("LibreOffice could not convert it")

        shutil.move(out, dst)

    return [dst], []

WORD_FORMATS = {"pdf": 17, "docx": 16, "doc": 0, "rtf": 6, "odt": 23, "txt": 2}

def word_save_as(src, dst):
    from tools.documents import allow_silent_pdf_open
    import pythoncom
    import win32com.client

    allow_silent_pdf_open()
    pythoncom.CoInitialize()
    word = None

    try:
        # a separate hidden Word, so documents the user has open are left alone
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(src.resolve()), ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False, Visible=False)
        doc.SaveAs2(str(dst.resolve()), FileFormat=WORD_FORMATS[dst.suffix[1:]])
        doc.Close(False)
    finally:
        if word is not None:
            word.Quit()

        pythoncom.CoUninitialize()

    return [dst], []

def has_word():
    if platform.system() != "Windows":
        return False

    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"))
        return True
    except (ImportError, OSError):
        return False

edges(["docx", "doc", "rtf", "odt", "txt"], list(WORD_FORMATS), word_save_as, has_word, "Microsoft Word")

IWORK = {
    # app: (files it opens, {target: AppleScript export format})
    "Pages": (["pages", "docx", "doc", "rtf"], {"pdf": "PDF", "docx": "Microsoft Word", "txt": "unformatted text", "epub": "EPUB"}),
    "Numbers": (["numbers", "xlsx", "xls"], {"pdf": "PDF", "xlsx": "Microsoft Excel", "csv": "CSV"}),
    "Keynote": (["key", "pptx", "ppt"], {"pdf": "PDF", "pptx": "Microsoft PowerPoint"}),
}

def iwork(app):
    def run(src, dst):
        # the first time, macOS asks whether this terminal may control the app
        quote = lambda path: str(path).replace("\\", "\\\\").replace('"', '\\"')
        script = f'''
        tell application "{app}"
            set theDoc to open (POSIX file "{quote(src)}")
            export theDoc to (POSIX file "{quote(dst)}") as {IWORK[app][1][dst.suffix[1:]]}
            close theDoc saving no
        end tell'''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)

        if result.returncode != 0 or not dst.exists():
            raise RuntimeError(result.stderr.strip() or f"{app} could not export it")

        return [dst], []

    return run

for app, (opens, exports) in IWORK.items():
    edges(opens, list(exports), iwork(app), lambda app=app: platform.system() == "Darwin" and Path(f"/Applications/{app}.app").exists(), f"{app} (Mac App Store)")

def textutil(src, dst):
    # built into every Mac; converts between the word-processing formats
    result = subprocess.run(["textutil", "-convert", dst.suffix[1:], "-output", str(dst), str(src)], capture_output=True, text=True)

    if result.returncode != 0 or not dst.exists():
        raise RuntimeError(result.stderr.strip() or "textutil failed")

    return [dst], []

edges(["docx", "doc", "rtf", "odt", "txt", "html"], ["docx", "doc", "rtf", "odt", "txt", "html"], textutil, lambda: shutil.which("textutil"), "a Mac")

# LibreOffice last: the native apps above keep more of the original look
LIBRE_HINT = "LibreOffice (libreoffice.org)"
edges(DOCS, DOCS + ["pdf", "txt", "html"], libreoffice, soffice, LIBRE_HINT)
edges(SHEETS, SHEETS + ["pdf", "csv"], libreoffice, soffice, LIBRE_HINT)
edges(SLIDES, SLIDES + ["pdf"], libreoffice, soffice, LIBRE_HINT)
