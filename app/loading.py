"""Fill the retained Word layout with the reviewed shipment and photographs."""
from io import BytesIO
from pathlib import Path
import re

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Inches
from PIL import Image, ImageOps

TEMPLATE = Path(__file__).with_name("templates") / "container-loading.docx"
SLOTS = (
    "空柜", "柜号", "地板", "干燥剂", "开始装柜",
    "装柜完成", "警示标识", "防护网或绑带", "关柜", "封条及封条号",
)
FIELDS = (
    "supplier", "products", "po", "quantity", "inspection_date",
    "inspector", "loading_date", "container", "seal",
)


def cells(doc):
    rows = doc.tables[0].rows
    return [rows[i].cells[1] for i in range(1, 9)] + [rows[8].cells[3]]


def photo_cells(doc):
    return [cell for i in (2, 4, 6, 8, 10) for cell in doc.tables[1].rows[i].cells]


def set_cell(cell, value):
    paragraph = cell.paragraphs[0]
    runs = paragraph.runs
    if runs:
        runs[0].text = value
        for run in runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(value)
    for other in cell.paragraphs[1:]:
        other.clear()


def photo_bytes(data, side=1500, quality=83):
    with Image.open(BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((side, side))
        output = BytesIO()
        image.save(output, "JPEG", quality=quality, optimize=True)
        return output.getvalue(), image.size


def export(values, photos, draft=False):
    doc = Document(TEMPLATE)
    for cell, key in zip(cells(doc), FIELDS, strict=True):
        set_cell(cell, values.get(key, ""))
    for index, row in enumerate(doc.tables[1].rows):
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        if index == 0 or index % 2 == 1:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.keep_with_next = True
    for index, cell in enumerate(photo_cells(doc)):
        paragraph = cell.paragraphs[0]
        paragraph.clear()
        if index not in photos:
            paragraph.add_run("Photo pending")
            continue
        data, (width, height) = photo_bytes(photos[index])
        scale = min(2.65 / width, 2.65 / height)
        paragraph.add_run().add_picture(BytesIO(data), width=Inches(width * scale))
    output = BytesIO()
    doc.save(output)
    groups = dict.fromkeys(
        name.strip().split()[0] for name in values["products"].split(",") if name.strip()
    )
    date = re.sub(r"^(\d\d)\.(\d\d)\.(\d{4})$", r"\3\2\1", values["inspection_date"])
    prefix = f"{date} " if re.fullmatch(r"\d{8}", date) else ""
    filename = f"{prefix}{values['po']} {', '.join(groups)} range {values['supplier']} Container Loading Report.docx"
    return output.getvalue(), ("草稿 " if draft else "") + filename
