"""Exercise the packaged binary over HTTP with synthetic documents."""
from io import BytesIO
from pathlib import Path
from queue import Queue
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
import json
import re
import subprocess
import sys

from docx import Document
from openpyxl import Workbook, load_workbook
from PIL import Image
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
NAME = "InspectionWorkbench"


def multipart(url, values=None, files=None):
    boundary = uuid4().hex
    body = bytearray()
    for key, value in (values or {}).items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for key, (name, data) in (files or {}).items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n\r\n'.encode())
        body.extend(data + b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    req = Request(url, bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urlopen(req, timeout=15) as response:
            return response.read()
    except HTTPError as error:
        raise RuntimeError(f"{url}: {error.code} {error.read().decode('utf-8')}") from error


def verify(url):
    with urlopen(url, timeout=10) as response:
        assert b"workbench.js" in response.read()
    with urlopen(url + "static/workbench.js", timeout=10) as response:
        assert b"/api/sqc" in response.read()

    with pymupdf.open() as pdf:
        page = pdf.new_page(width=842, height=595)
        page.insert_text((22, 70), "发票号: MJ260001", fontname="china-s", fontsize=10)
        for x, text in [(109, "P123456"), (162, "TEST-OAK"), (216, "TEST OAK TABLE"), (425, "70"), (454, "N")]:
            page.insert_text((x, 130), text, fontsize=10)
        parsed = json.loads(multipart(url + "api/preparation", files={"file": ("orders.pdf", pdf.tobytes())}))
        row = parsed["rows"][0]
        assert row["invoice"] == "MJ260001", row
        assert (row["po"], row["product"], row["quantity"]) == ("123456", "TEST OAK TABLE", 70), row

    book = Workbook()
    sheet = book.active
    sheet.title = "SQC report"
    sheet["L5"] = "Product name"
    sheet["E5"] = "Demo supplier"
    sheet["Q5"] = "TEST OAK TABLE"
    sheet["AD5"] = "123456/123457"
    sheet["AE38"], sheet["AK38"] = "Sample 1", "p"
    original = BytesIO()
    book.save(original)
    updated = multipart(url + "api/sqc/export", {"orders": "123456"}, {"file": ("qc.xlsx", original.getvalue())})
    sheet = load_workbook(BytesIO(updated)).active
    assert sheet["AD5"].value == "123456" and sheet["AK38"].value == "P"

    photo = BytesIO()
    Image.new("RGB", (40, 30), "teal").save(photo, "JPEG")
    data = {
        "supplier": "Demo supplier", "po": "P123456",
        "products": "TEST OAK TABLE", "quantity": "70pcs",
    }
    report = multipart(url + "api/loading/export", {"values": json.dumps(data)},
                       {"photo0": ("photo.jpg", photo.getvalue())})
    doc = Document(BytesIO(report))
    assert doc.tables[0].rows[3].cells[1].text == "P123456"
    assert doc.tables[0].rows[4].cells[1].text == "70pcs"
    assert len(doc.inline_shapes) == 2


def main():
    exe = ROOT / "dist" / NAME / (NAME + (".exe" if sys.platform == "win32" else ""))
    process = subprocess.Popen([str(exe), "--no-browser", "--port", "0"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines = Queue()

    def read_output():
        for line in process.stdout:
            print(line, end="")
            lines.put(line)
        lines.put(None)

    Thread(target=read_output, daemon=True).start()
    try:
        for _ in range(25):
            line = lines.get(timeout=30)
            if line is None:
                raise RuntimeError("Packaged process exited before startup")
            match = re.search(r"http://127\.0\.0\.1:\d+/", line)
            if match:
                verify(match.group())
                print("Packaged app passed PDF, Excel and Word smoke checks.")
                return
        raise RuntimeError("Packaged app startup URL missing")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
