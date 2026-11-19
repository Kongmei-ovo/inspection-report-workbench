"""Read the coordinate-based FHL preparation-sheet PDF."""
import re

import pymupdf

ORDER = re.compile(r"P(?:O)?\s*([A-Z]*\d{4,})", re.I)
INVOICE = re.compile(r"发票号\s*[:：]\s*(MJ\d+)", re.I)


def read_pdf(raw, filename):
    rows = []
    skipped = []
    invoice = ""
    with pymupdf.open(stream=raw, filetype="pdf") as document:
        if len(document) > 200:
            raise ValueError("单个备货单请控制在 200 页以内。")
        for index, page in enumerate(document, 1):
            content = page.get_text()
            match = INVOICE.search(content)
            if match:
                invoice = match.group(1).upper()
            words = []
            for word in page.get_text("words"):
                box = pymupdf.Rect(word[:4]) * page.rotation_matrix
                words.append((box.x0 / page.rect.width * 842, box.y0, word[4]))
            actions = sorted(
                (x, y, ORDER.fullmatch(value).group(1).upper())
                for x, y, value in words if 90 <= x <= 138 and ORDER.fullmatch(value)
            )
            actions.sort(key=lambda item: item[1])
            for row_index, (left, top, po) in enumerate(actions):
                bottom = actions[row_index + 1][1] - 3 if row_index + 1 < len(actions) else top + 29
                def line_key(word):
                    return round((word[1] - top) / 4), word[0]
                names = sorted(
                    (w for w in words if left - 2 <= w[0] < 296 and top - 2 <= w[1] < bottom),
                    key=line_key,
                )
                values = [word[2] for word in names]
                if len(values) < 3:
                    skipped.append(f"第 {index} 页 P{po} 品名读取失败")
                    continue
                sku = values[1]
                product = []
                for word in values[2:]:
                    if re.search(r"[\u3400-\u9fff]", word) or word.startswith(("(", "（")):
                        break
                    product.append(word)
                if len(product) > 2 and product[0] == product[-1]:
                    product.pop()
                numeric = sorted(
                    (w for w in words if 405 <= w[0] <= 484 and abs(w[1] - top) < 7),
                    key=lambda w: w[0],
                )
                quantity = re.fullmatch(r"([\d,]+)\s*([A-Z]+)", " ".join(w[2] for w in numeric))
                if not quantity or not product:
                    skipped.append(f"第 {index} 页 P{po} {sku} 品名或数量需要检查")
                rows.append({
                    "invoice": invoice, "po": po, "sku": sku, "product": " ".join(product),
                    "quantity": int(quantity.group(1).replace(",", "")) if quantity else None,
                    "unit": quantity.group(2) if quantity else "",
                    "source": filename, "page": index,
                })
    if not rows:
        raise ValueError("此 PDF 没有读到备货明细。请使用系统导出的文字版 PDF。")
    return {"rows": rows, "warnings": skipped}
