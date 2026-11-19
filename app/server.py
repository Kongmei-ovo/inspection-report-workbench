from base64 import b64encode
from argparse import ArgumentParser
from io import BytesIO
from pathlib import PurePosixPath
import json
import re
from secrets import compare_digest, token_urlsafe
import webbrowser
from zipfile import BadZipFile, ZipFile

from flask import Flask, jsonify, render_template, request, send_file
from pdfplumber.utils.exceptions import PdfminerException
from PIL import UnidentifiedImageError
from werkzeug.serving import make_server

import loading
import preparation
import sqc
import updater

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 120 * 1024 * 1024
app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]
UPDATE_TOKEN = token_urlsafe(32)


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
    version = updater.CURRENT if updater.version_key(updater.CURRENT) else "开发版"
    return render_template("index.html", version=version, update_token=UPDATE_TOKEN)


@app.errorhandler(updater.UpdateError)
def update_error(error):
    return jsonify(error=str(error)), 503


@app.get("/api/update")
def find_update():
    release = updater.check()
    return jsonify(
        current=updater.CURRENT,
        latest=release.version if release else None,
        size=release.size if release else None,
        can_install=bool(updater.FROZEN and updater.version_key(updater.CURRENT)),
        supported=bool(updater.target()),
    )


@app.post("/api/update/install")
def install_update():
    if not compare_digest(request.headers.get("X-Workbench-Update", "").encode(), UPDATE_TOKEN.encode()):
        return jsonify(error="更新请求未授权，请刷新工作台后重试。"), 403
    payload = request.get_json(silent=True)
    requested = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(requested, str) or not updater.version_key(requested):
        return jsonify(error="更新版本无效，请重新检查更新。"), 400
    release = updater.check()
    if not release or release.version != requested:
        return jsonify(error="可用版本已变化，请重新检查更新。"), 409
    bundle = updater.install(release)
    updater.launch(bundle)
    return jsonify(version=release.version, location=str(bundle))


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
    if not args.no_browser:
        newer = updater.newer_installed()
        if newer:
            try:
                updater.launch(newer)
                return
            except updater.UpdateError:
                print("Could not start the updated copy. Starting the current copy instead.", flush=True)
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
