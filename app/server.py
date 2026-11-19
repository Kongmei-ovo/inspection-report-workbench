from base64 import b64encode
from argparse import ArgumentParser
from io import BytesIO
from pathlib import PurePosixPath
import json
import re
import webbrowser
from zipfile import BadZipFile, ZipFile

from flask import Flask, jsonify, render_template, request, send_file
from pdfplumber.utils.exceptions import PdfminerException
from PIL import UnidentifiedImageError
from werkzeug.serving import make_server

import loading
import preparation
import sqc

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 120 * 1024 * 1024


def upload(field, suffix):
    file = request.files.get(field)
    if not file or not file.filename.lower().endswith(suffix):
        raise ValueError(f"请选择 {suffix} 文件。")
    return file


def download(data, name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    response = send_file(BytesIO(data), as_attachment=True, download_name=name)
    response.headers["X-Report-Bytes"] = str(len(data))
    return response


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="附件超过 120 MB，请分批添加。"), 413


@app.errorhandler(ValueError)
@app.errorhandler(KeyError)
@app.errorhandler(BadZipFile)
@app.errorhandler(UnidentifiedImageError)
def invalid(error):
    message = str(error) if isinstance(error, ValueError) else "无法读取附件或模板，请检查文件格式。"
    return jsonify(error=message), 400


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/preparation")
def read_preparation():
    file = upload("file", ".pdf")
    try:
        return jsonify(preparation.read_pdf(file.read(), file.filename))
    except (RuntimeError, PdfminerException) as error:
        raise ValueError("无法打开备货单 PDF，请检查文件。") from error


@app.post("/api/sqc")
def read_sqc():
    file = upload("file", ".xlsx")
    return jsonify(sqc.inspect(file.read()))


@app.post("/api/sqc/export")
def export_sqc():
    raw = upload("file", ".xlsx").read()
    return download(*sqc.export(raw, sqc.order_numbers(request.form.get("orders"))))


@app.post("/api/photos/zip")
def read_photos():
    photos = []
    raw = upload("file", ".zip").read()
    with ZipFile(BytesIO(raw)) as archive:
        items = [
            item for item in archive.infolist() if not item.is_dir()
            and PurePosixPath(item.filename).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            and not item.filename.startswith("__MACOSX/")
        ]
        if len(items) > 100 or sum(item.file_size for item in items) > 240_000_000:
            raise ValueError("照片 ZIP 请分批，每批最多 100 张且解压后小于 240 MB。")
        failures = []
        for item in items:
            try:
                data, _ = loading.photo_bytes(archive.read(item))
            except (RuntimeError, OSError):
                failures.append(item.filename)
                continue
            photos.append({"name": item.filename, "data": b64encode(data).decode("ascii")})
    return jsonify(photos=photos, failures=failures)


@app.post("/api/loading/export")
def export_loading():
    values = json.loads(request.form["values"])
    values = {field: str(values.get(field, "")).strip() for field in loading.FIELDS}
    if not all(values[key] for key in ("supplier", "products", "po", "quantity")):
        raise ValueError("请填写供应商、PO、品名和数量。")
    if not re.fullmatch(r"P[A-Z]*\d{4,}", values["po"]):
        raise ValueError("PO 格式不正确。")
    photos = {
        index: request.files[f"photo{index}"].read()
        for index in range(len(loading.SLOTS)) if f"photo{index}" in request.files
    }
    draft = not all(values.values()) or len(photos) < len(loading.SLOTS)
    return download(*loading.export(values, photos, draft))


def main():
    parser = ArgumentParser(description="验货报告工作台")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    with make_server("127.0.0.1", args.port, app, threaded=True) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Workbench: {url}", flush=True)
        print("Keep this window open while using the workbench. Press Ctrl+C to exit.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
