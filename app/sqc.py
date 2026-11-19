"""Read QC fields and edit only the relevant cells in the original workbook."""
from io import BytesIO
from pathlib import PurePosixPath
import re
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree
from openpyxl import load_workbook
from PIL import Image

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
LIMIT = 5_000_000


def order_numbers(value):
    return list(dict.fromkeys(re.findall(r"\d{5,}", str(value or ""))))


def inspect(raw):
    workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True, keep_links=False)
    try:
        if "SQC report" not in workbook.sheetnames:
            raise ValueError("没有找到 SQC report 工作表。")
        sheet = workbook["SQC report"]
        rows = list(sheet.iter_rows(min_row=1, max_row=75, max_col=50))
        product_row = next(
            (row for row in rows if any("product name" in str(c.value).lower() for c in row)),
            None,
        )
        if not product_row:
            raise ValueError("无法定位品名和 PO，请使用与样本相同的 SQC 版式。")
        values = {c.column_letter: c for c in product_row if c.value is not None}
        product = str(values["Q"].value)
        po = values["AD"]
        quick = [
            row[36].coordinate for row in rows
            if re.fullmatch(r"Sample [1-5]", str(row[30].value), re.I)
            and str(row[36].value).strip().lower() == "p"
        ]
        return {
            "product": product,
            "orders": order_numbers(po.value),
            "supplier": str(values["E"].value),
            "po_cell": po.coordinate,
            "quick": quick,
            "size": len(raw),
            "date": str(sheet["AR2"].value or "").split(" ")[0],
            "verdict": str(sheet["AW9"].value or ""),
        }
    finally:
        workbook.close()


def _replace_cells(book, updates, sheet_name):
    pkg = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    doc = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    with ZipFile(BytesIO(book)) as source:
        wb = etree.fromstring(source.read("xl/workbook.xml"))
        rid = next(s.get(doc + "id") for s in wb.find(NS + "sheets") if s.get("name") == sheet_name)
        rels = etree.fromstring(source.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels.findall(pkg + "Relationship") if r.get("Id") == rid)
        part = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = etree.fromstring(source.read(part))
        cells = {c.get("r"): c for c in root.iter(NS + "c")}
        for address, value in updates.items():
            cell = cells[address]
            for child in list(cell):
                if child.tag in {NS + "v", NS + "f", NS + "is"}:
                    cell.remove(child)
            cell.set("t", "inlineStr")
            inline = etree.SubElement(cell, NS + "is")
            etree.SubElement(inline, NS + "t").text = value
        return part, etree.tostring(root, encoding="UTF-8", xml_declaration=True)


def _compress_image(data, kind, max_side):
    if kind not in {"jpg", "jpeg", "png"}:
        return data
    try:
        with Image.open(BytesIO(data)) as image:
            if kind == "png" and image.width <= max_side and image.height <= max_side:
                return data
            image.thumbnail((max_side, max_side))
            result = BytesIO()
            if kind == "png":
                image.save(result, format="PNG", optimize=True)
            else:
                image.convert("RGB").save(result, format="JPEG", quality=83, optimize=True,
                                          icc_profile=image.info.get("icc_profile"),
                                          exif=image.info.get("exif", b""))
            return result.getvalue() if result.tell() < len(data) else data
    except (OSError, ValueError):
        return data


def _photo_jpeg(data, max_side):
    """Convert large opaque photographs embedded as PNG into JPEG parts."""
    if len(data) < 100_000:
        return None
    try:
        with Image.open(BytesIO(data)) as image:
            if "transparency" in image.info or (
                "A" in image.getbands() and image.getchannel("A").getextrema()[0] < 255
            ):
                return None
            image.thumbnail((max_side, max_side))
            output = BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=86, optimize=True)
            return output.getvalue() if output.tell() < len(data) else None
    except (OSError, ValueError):
        return None


def _repack(source, part, worksheet, side):
    converted = {}
    if side:
        for name in source.namelist():
            if name.startswith("xl/media/") and name.lower().endswith(".png"):
                jpeg = _photo_jpeg(source.read(name), side)
                if jpeg:
                    stem = PurePosixPath(name).stem
                    new_name = f"xl/media/{stem}_fhl_photo.jpeg"
                    if new_name not in source.namelist():
                        converted[name] = (new_name, jpeg)
    new_targets = {
        PurePosixPath(name).name: PurePosixPath(new_name).name
        for name, (new_name, _) in converted.items()
    }
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as result:
        for item in source.infolist():
            name = item.filename
            if name in converted:
                new_name, jpeg = converted[name]
                result.writestr(new_name, jpeg)
                continue
            data = worksheet if name == part else source.read(item)
            if side and name.startswith("xl/media/"):
                data = _compress_image(data, name.rsplit(".", 1)[-1].lower(), side)
            if converted and name.endswith(".rels"):
                relationships = etree.fromstring(data)
                changed = False
                for rel in relationships:
                    target = rel.get("Target", "")
                    prefix, slash, old = target.rpartition("/")
                    if rel.get("Type", "").endswith("/image") and old in new_targets:
                        rel.set("Target", prefix + slash + new_targets[old])
                        changed = True
                if changed:
                    data = etree.tostring(relationships, encoding="UTF-8", xml_declaration=True)
            if converted and name == "[Content_Types].xml":
                types = etree.fromstring(data)
                if not any(node.get("Extension", "").lower() == "jpeg" for node in types):
                    default = types.tag.replace("}Types", "}Default")
                    etree.SubElement(types, default, Extension="jpeg", ContentType="image/jpeg")
                    data = etree.tostring(types, encoding="UTF-8", xml_declaration=True)
            result.writestr(name, data)
    return output.getvalue()


def export(raw, orders):
    meta = inspect(raw)
    if not orders or not all(re.fullmatch(r"\d{5,}", order) for order in orders):
        raise ValueError("请至少填写一个有效 PO。")
    if set(orders) - set(meta["orders"]):
        raise ValueError("新增 PO 需要相应的 QC 原始报告，不能沿用本报告的检测证据。")
    updates = {cell: "P" for cell in meta["quick"]}
    if orders != meta["orders"]:
        updates[meta["po_cell"]] = "/".join(orders)
    part, worksheet = _replace_cells(raw, updates, "SQC report")
    with ZipFile(BytesIO(raw)) as source:
        for side in (None, 1900, 1400):
            output = _repack(source, part, worksheet, side)
            if len(output) <= LIMIT:
                break
    status = "Passed" if meta["verdict"].upper() == "P" else "QC review"
    company = meta["supplier"]
    if "BRIGHT WOOD" in company.upper():
        company = "BRIGHT WOOD"
    name = " ".join(filter(None, [
        meta["date"].replace("-", ""), "P" + ",".join(orders), meta["product"],
        company, "SQC inspection " + status + ".xlsx",
    ]))
    return output, name
