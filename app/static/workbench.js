"use strict";

const $ = (id) => document.getElementById(id);
const state = {pdfs: [], qc: [], current: null, photos: [], assigned: new Map()};
const slots = ["空柜", "柜号", "地板", "干燥剂", "开始装柜", "装柜完成", "警示标识", "防护网或绑带", "关柜", "封条及封条号"];
const loadingForm = $("loading-form");

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function status(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle("error", error);
}

function bodyWith(file) {
  const form = new FormData();
  form.append("file", file);
  return form;
}

async function post(url, body) {
  const response = await fetch(url, {method: "POST", body});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || "处理失败，请检查文件或重试。");
  }
  return response;
}

async function download(url, form) {
  const response = await post(url, form);
  const header = response.headers.get("Content-Disposition") || "";
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
  const plain = header.match(/filename="?([^";]+)"?/i);
  const blob = await response.blob();
  const link = element("a");
  link.href = URL.createObjectURL(blob);
  link.download = encoded ? decodeURIComponent(encoded[1]) : (plain?.[1] || "报告");
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1500);
  return blob.size;
}

function options(select, entries, empty, current = "") {
  select.replaceChildren(new Option(empty, ""), ...entries.map(([value, label]) => new Option(label, value)));
  select.value = entries.some(([value]) => value === current) ? current : "";
}

function fileChip(container, label, onRemove, onSelect, active = false) {
  const chip = element("span", "", "chip" + (active ? " active" : ""));
  const name = element(onSelect ? "button" : "span", label);
  if (onSelect) {
    name.onclick = onSelect;
    name.setAttribute("aria-pressed", active);
  }
  const remove = element("button", "×");
  remove.ariaLabel = "移除 " + label;
  remove.onclick = onRemove;
  chip.append(name, remove);
  container.append(chip);
}

document.querySelectorAll(".nav").forEach((button) => {
  button.onclick = () => {
    const view = button.dataset.view;
    for (const item of document.querySelectorAll(".nav")) {
      item.classList.toggle("active", item === button);
      if (item === button) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    }
    $("sqc").hidden = view !== "sqc";
    $("loading").hidden = view !== "loading";
    $("title").textContent = view === "sqc" ? "SQC 验货报告" : "装柜报告";
    $("description").textContent = view === "sqc"
      ? "按 PO 和品名核对备货明细，保留原 Excel 的检验记录与附图。"
      : "按发票选择 PO，整理装柜照片和报告字段，生成 Word 文件。";
  };
});

async function importFiles(input, url, collection, statusID, redraw) {
  const files = [...input.files];
  if (!files.length) return;
  const failures = [];
  input.disabled = true;
  try {
    for (const [i, file] of files.entries()) {
      status(statusID, `正在读取 ${i + 1}/${files.length} · ${file.name}`);
      try {
        const result = await (await post(url, bodyWith(file))).json();
        const entry = {file, ...result};
        const existing = collection.findIndex((item) => item.file.name === file.name);
        if (existing === -1) collection.push(entry);
        else collection.splice(existing, 1, entry);
        if (collection === state.qc && (!state.current || state.current.file.name === file.name)) {
          state.current = entry;
        }
      } catch (error) {
        failures.push(`${file.name} · ${error.message}`);
      }
    }
    redraw();
    if (failures.length) status(statusID, failures.join("；"), true);
  } finally {
    input.value = "";
    input.disabled = false;
  }
}

function renderPdf() {
  $("pdf-list").replaceChildren();
  for (const pdf of state.pdfs) {
    fileChip($("pdf-list"), `${pdf.file.name} · ${pdf.rows.length} 条`, () => {
      state.pdfs.splice(state.pdfs.indexOf(pdf), 1);
      renderPdf();
    });
  }
  const warnings = state.pdfs.flatMap((pdf) => pdf.warnings);
  status("pdf-status", warnings.length ? warnings.slice(0, 6).join("；") : state.pdfs.length
    ? `已导入 ${state.pdfs.length} 份备货单 · ${state.pdfs.reduce((sum, pdf) => sum + pdf.rows.length, 0)} 条明细`
    : "等待导入", Boolean(warnings.length));
  renderInvoice();
  renderQc();
}

$("pdf").onchange = () => importFiles($("pdf"), "/api/preparation", state.pdfs, "pdf-status", renderPdf);
$("qc").onchange = () => importFiles($("qc"), "/api/sqc", state.qc, "qc-status", renderQc);

function productKey(text) {
  return text.normalize("NFKC").toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function renderQc() {
  $("qc-list").replaceChildren();
  for (const item of state.qc) {
    fileChip($("qc-list"), `P${item.orders.join(", ")} · ${item.product}`, () => {
      state.qc.splice(state.qc.indexOf(item), 1);
      if (item === state.current) state.current = state.qc[0] || null;
      renderQc();
    }, () => {
      state.current = item;
      status("qc-export-status", "");
      renderQc();
    }, item === state.current);
  }
  status("qc-status", state.qc.length ? `已导入 ${state.qc.length} 份 QC 报告` : "等待导入");
  const item = state.current;
  $("qc-review").hidden = !item;
  if (!item) return;
  $("qc-title").textContent = item.product;
  $("qc-meta").textContent = `原检验日期 ${item.date || "未填"} · 快检通过记录 ${item.quick.length} 条 · 原文件 ${(item.size / 1e6).toFixed(1)} MB`;
  $("qc-matches").replaceChildren();
  const all = state.pdfs.flatMap((pdf) => pdf.rows);
  for (const po of item.orders) {
    const related = all.filter((row) => row.po === po);
    const exact = related.filter((row) => row.product && productKey(row.product) === productKey(item.product));
    const matches = exact.length === 1 ? "已匹配" : exact.length > 1
      ? `${exact.length} 条候选，核对来源` : related.length ? "PO 相同，品名待核对" : "备货单未匹配";
    const row = element("div", "", "match");
    const label = element("label");
    const include = element("input");
    include.type = "checkbox";
    include.checked = !item.excluded?.has(po);
    include.value = po;
    include.onchange = () => {
      item.excluded ??= new Set();
      if (include.checked) item.excluded.delete(po);
      else item.excluded.add(po);
    };
    label.append(include, document.createTextNode(`P${po} · ${matches}`));
    row.append(label);
    if (related.length) {
      const details = element("details");
      details.open = exact.length !== 1;
      details.append(element("summary", "查看备货明细"), table(exact.length ? exact : related));
      row.append(details);
    }
    $("qc-matches").append(row);
  }
}

function table(rows) {
  const wrapper = element("div", "", "table-wrap");
  const grid = element("table");
  const head = grid.createTHead().insertRow();
  for (const col of ["货号", "品名", "数量", "来源"]) head.append(element("th", col));
  const body = grid.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const text of [row.sku, row.product, row.quantity === null ? "待核对" : `${row.quantity} ${row.unit}`, `${row.source} · 第 ${row.page} 页`]) {
      tr.insertCell().textContent = text;
    }
  }
  wrapper.append(grid);
  return wrapper;
}

$("qc-export").onclick = async () => {
  const entry = state.current;
  const orders = entry.orders.filter((po) => !entry.excluded?.has(po));
  const body = bodyWith(entry.file);
  body.append("orders", orders.join("/"));
  await exportReport($("qc-export"), "qc-export-status", "/api/sqc/export", body);
};

async function exportReport(button, id, url, body) {
  button.disabled = true;
  status(id, "正在生成文件…");
  try {
    const size = await download(url, body);
    status(id, `已生成 ${(size / 1e6).toFixed(2)} MB${size > 5e6 ? " · 超过 5 MB，发送前请核对" : ""}`);
  } catch (error) {
    status(id, error.message, true);
  } finally {
    button.disabled = false;
  }
}

function currentInvoiceRows() {
  const selection = $("invoice").value;
  if (!selection) return [];
  const [source, invoice] = JSON.parse(selection);
  return state.pdfs.find((pdf) => pdf.file.name === source)?.rows.filter((row) => row.invoice === invoice) || [];
}

function renderInvoice() {
  const old = $("invoice").value;
  const oldPo = $("po").value;
  const entries = state.pdfs.flatMap((pdf) => [...new Set(pdf.rows.map((row) => row.invoice).filter(Boolean))]
    .map((invoice) => [JSON.stringify([pdf.file.name, invoice]), `${invoice} · ${pdf.file.name}`]));
  const selected = old || (entries.length === 1 ? entries[0][0] : "");
  options($("invoice"), entries, entries.length ? "请选择发票" : "请先添加备货单", selected);
  if (old !== $("invoice").value) renderPo();
  else if (old) {
    const entries = [...new Set(currentInvoiceRows().map((row) => row.po))].map((po) => [po, "P" + po]);
    options($("po"), entries, "请选择 PO", oldPo);
    if (oldPo !== $("po").value) fillPo();
  }
}

function renderPo() {
  const entries = [...new Set(currentInvoiceRows().map((row) => row.po))].map((po) => [po, "P" + po]);
  options($("po"), entries, "请选择 PO", entries.length === 1 ? entries[0][0] : "");
  fillPo();
}

function fillPo() {
  const rows = currentInvoiceRows().filter((row) => row.po === $("po").value);
  $("loading-lines").replaceChildren();
  if (rows.length) $("loading-lines").append(table(rows));
  loadingForm.elements.products.value = [...new Set(rows.map((row) => row.product).filter(Boolean))].join(", ");
  loadingForm.elements.quantity.value = rows.length && rows.every((row) => row.quantity !== null)
    ? rows.reduce((sum, row) => sum + row.quantity, 0) + "pcs" : "";
  for (const key of ["container", "seal", "inspection_date", "loading_date"]) {
    loadingForm.elements[key].value = "";
  }
  status("loading-export-status", "");
  state.assigned.clear();
  renderPhotos();
}

$("invoice").onchange = renderPo;
$("po").onchange = fillPo;

function addPhoto(name, file, photoUrl) {
  state.photos.push({name, file, url: photoUrl || URL.createObjectURL(file)});
}

async function importPhotos(input) {
  const files = [...input.files];
  if (!files.length) return;
  const errors = [];
  input.disabled = true;
  status("photo-status", "正在读取照片…");
  try {
    for (const file of files) {
      const name = file.webkitRelativePath || file.name;
      if (file.name.toLowerCase().endsWith(".zip")) {
        try {
          const data = await (await post("/api/photos/zip", bodyWith(file))).json();
          for (const photo of data.photos) {
            const bytes = Uint8Array.from(atob(photo.data), (letter) => letter.charCodeAt(0));
            addPhoto(file.name + "/" + photo.name, new Blob([bytes], {type: "image/jpeg"}));
          }
          errors.push(...data.failures);
        } catch (error) {
          errors.push(`${name} · ${error.message}`);
        }
      } else if (/\.(jpe?g|png|webp)$/i.test(name)) addPhoto(name, file);
    }
    renderPhotos();
    if (errors.length) status("photo-status", errors.slice(0, 8).join("；"), true);
  } finally {
    input.disabled = false;
    input.value = "";
  }
}

$("photo-files").onchange = () => importPhotos($("photo-files"));
$("photo-folder").onchange = () => importPhotos($("photo-folder"));

function renderPhotos() {
  $("photo-slots").replaceChildren();
  const invoice = currentInvoiceRows()[0]?.invoice;
  const po = $("po").value;
  const list = state.photos.map((photo, index) => ({
    photo, index, rank: Number(Boolean(invoice && photo.name.includes(invoice))) * 2
      + Number(Boolean(po && photo.name.includes(po))),
  })).sort((a, b) => b.rank - a.rank);
  const entries = list.map(({photo, index}) => [String(index), photo.name]);
  $("photo-gallery").hidden = !list.length;
  $("photo-gallery-list").replaceChildren(...list.map(({photo, index}) => {
    const card = element("article", "", "gallery-card");
    const image = element("img");
    image.loading = "lazy";
    image.src = photo.url;
    image.alt = photo.name;
    const select = element("select");
    select.ariaLabel = "分配照片 " + (index + 1);
    options(select, slots.map((slot, i) => [String(i), slot]), "分配到…");
    select.onchange = () => {
      if (select.value === "") return;
      state.assigned.set(Number(select.value), index);
      renderPhotos();
    };
    const name = element("small", photo.name.split("/").at(-1));
    card.append(image, name, select);
    return card;
  }));
  slots.forEach((slot, index) => {
    const card = element("article");
    const label = element("label", slot);
    const select = element("select");
    const selected = state.assigned.get(index);
    options(select, entries, "选择照片", selected === undefined ? "" : String(selected));
    select.onchange = () => {
      if (select.value === "") state.assigned.delete(index);
      else state.assigned.set(index, Number(select.value));
      showPhoto();
      updatePhotoStatus();
    };
    const preview = element("div", "", "photo-preview");
    function showPhoto() {
      const chosen = state.photos[state.assigned.get(index)];
      if (!chosen) {
        preview.textContent = "未选择";
        return;
      }
      const image = element("img");
      image.src = chosen.url;
      image.alt = chosen.name;
      preview.replaceChildren(image);
    }
    label.append(select);
    card.append(label, preview);
    $("photo-slots").append(card);
    showPhoto();
  });
  updatePhotoStatus();
}

function updatePhotoStatus() {
  status("photo-status", state.photos.length
    ? `已导入 ${state.photos.length} 张 · 已分配 ${state.assigned.size}/${slots.length} 个位置`
    : "等待导入");
  $("loading-export").textContent = loadingComplete() ? "导出 Word" : "导出 Word 草稿";
}

function loadingComplete() {
  return [...new FormData(loadingForm).values()].every((value) => value.trim())
    && state.assigned.size === slots.length;
}

loadingForm.addEventListener("input", updatePhotoStatus);
loadingForm.addEventListener("submit", (event) => event.preventDefault());
$("loading-export").onclick = async () => {
  if (!$("po").value) {
    status("loading-export-status", "请先选择发票和 PO。", true);
    return;
  }
  if (!loadingForm.reportValidity()) return;
  const values = Object.fromEntries(new FormData(loadingForm));
  values.po = "P" + $("po").value;
  for (const key of ["inspection_date", "loading_date"]) {
    if (values[key]) values[key] = values[key].split("-").reverse().join(".");
  }
  const body = new FormData();
  body.append("values", JSON.stringify(values));
  for (const [slot, index] of state.assigned) body.append("photo" + slot, state.photos[index].file, "photo.jpg");
  await exportReport($("loading-export"), "loading-export-status", "/api/loading/export", body);
};

renderPhotos();

let availableUpdate;
const updateCheck = $("update-check");
const updateInstall = $("update-install");
const updateRelease = $("update-release");

updateCheck.onclick = async () => {
  updateCheck.disabled = true;
  updateInstall.hidden = true;
  updateRelease.hidden = true;
  status("update-status", "正在检查…");
  try {
    const response = await fetch("/api/update", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    availableUpdate = data.latest;
    if (!data.supported) {
      status("update-status", "当前平台无自动更新包。");
      updateRelease.hidden = false;
    } else if (!data.latest) {
      status("update-status", "没有可用的新版本。");
    } else {
      const size = (data.size / 1e6).toFixed(0);
      status("update-status", `可更新到 ${data.latest} · ${size} MB`);
      updateInstall.hidden = !data.can_install;
      updateRelease.hidden = false;
    }
  } catch (error) {
    status("update-status", error.message || "检查更新失败。");
    updateRelease.hidden = false;
  } finally {
    updateCheck.disabled = false;
  }
};

updateInstall.onclick = async () => {
  if (!availableUpdate) return;
  if (!window.confirm("新版会打开独立窗口。请先导出本页正在编辑的报告，再继续更新。")) return;
  updateInstall.disabled = true;
  updateCheck.disabled = true;
  status("update-status", "正在下载并校验更新包，请保留此窗口…");
  try {
    const token = document.querySelector('meta[name="workbench-update"]').content;
    const response = await fetch("/api/update/install", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Workbench-Update": token},
      body: JSON.stringify({version: availableUpdate}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    status("update-status", `${data.version} 已启动，可以关闭旧窗口。`);
    updateInstall.hidden = true;
  } catch (error) {
    status("update-status", error.message || "安装更新失败。");
    updateRelease.hidden = false;
  } finally {
    updateCheck.disabled = false;
    updateInstall.disabled = false;
  }
};
