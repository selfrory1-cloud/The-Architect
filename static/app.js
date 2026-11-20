const $ = (sel) => document.querySelector(sel);
const SVG_NS = "http://www.w3.org/2000/svg";
let lastResult = null;

function readForm(form) {
  const fd = new FormData(form);
  const data = Object.fromEntries(fd.entries());
  for (const cb of form.querySelectorAll("input[type=checkbox]")) data[cb.name] = cb.checked;
  return data;
}

function showMessages(kind, list) {
  for (const text of list || []) {
    const div = document.createElement("div");
    div.className = `msg ${kind}`;
    div.textContent = text;
    $("#messages").appendChild(div);
  }
}

function el(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

const DRAW_ORDER = ["floor", "stairs", "prop", "wall", "door"];

function drawLevel(level) {
  const { preview } = lastResult;
  const items = preview.levels[level] || [];
  const rooms = preview.rooms.filter((r) => String(r.level) === String(level));
  const all = Object.values(preview.levels).flat();
  const minX = Math.min(...all.map((i) => i.x)) - 1;
  const minZ = Math.min(...all.map((i) => i.z)) - 1;
  const maxX = Math.max(...all.map((i) => i.x + i.w)) + 1;
  const maxZ = Math.max(...all.map((i) => i.z + i.d)) + 1;
  const svg = el("svg", { viewBox: `${minX} 0 ${maxX - minX} ${maxZ - minZ}` });
  // TaleSpire z points north; SVG y points down, so flip vertically.
  const flipY = (z, d) => maxZ - (z + d);
  const sorted = [...items].sort((a, b) => DRAW_ORDER.indexOf(a.category) - DRAW_ORDER.indexOf(b.category));
  for (const it of sorted) {
    const r = el("rect", { x: it.x, y: flipY(it.z, it.d), width: it.w, height: it.d, class: it.category });
    const title = el("title", {});
    title.textContent = `${it.asset} (${it.zone})`;
    r.appendChild(title);
    svg.appendChild(r);
  }
  for (const room of rooms) {
    const t = el("text", { x: room.x + room.w / 2, y: flipY(room.z, room.d) + room.d / 2, class: "room-label" });
    t.textContent = room.name;
    svg.appendChild(t);
  }
  $("#plan").replaceChildren(svg);
  for (const b of document.querySelectorAll("#level-tabs button")) {
    b.classList.toggle("active", b.dataset.level === String(level));
  }
}

function render(result) {
  lastResult = result;
  $("#slab").value = result.slab;
  $("#copy").disabled = false;
  $("#download-recipe").disabled = false;
  const s = result.stats;
  const cats = Object.entries(s.by_category).map(([k, v]) => `${k}: ${v}`).join(" · ");
  $("#stats").textContent = `${s.total} assets (${s.unique_assets} unique) — ${cats} — ${s.slab_chars} chars`;
  showMessages("note", result.notes);
  showMessages("warn", result.warnings);
  const tabs = $("#level-tabs");
  tabs.replaceChildren();
  for (const level of Object.keys(result.preview.levels)) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.level = level;
    b.textContent = `Floor ${Number(level) + 1}`;
    b.addEventListener("click", () => drawLevel(level));
    tabs.appendChild(b);
  }
  drawLevel(Object.keys(result.preview.levels)[0]);
}

$("#build-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#messages").replaceChildren();
  const btn = e.submitter;
  btn.disabled = true;
  btn.textContent = "Generating…";
  try {
    const resp = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readForm(e.target)),
    });
    const data = await resp.json();
    if (data.ok) render(data);
    else showMessages("error", data.errors);
  } catch (err) {
    showMessages("error", [String(err)]);
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate slab";
  }
});

$("#copy").addEventListener("click", async () => {
  const text = $("#slab").value;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    $("#slab").select();
    document.execCommand("copy");
  }
  $("#copy").textContent = "Copied!";
  setTimeout(() => ($("#copy").textContent = "Copy slab"), 1500);
});

$("#download-recipe").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(lastResult.recipe, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `recipe-${lastResult.recipe.theme}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

$("#decode").addEventListener("click", async () => {
  const resp = await fetch("/api/decode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slab: $("#decode-input").value }),
  });
  const data = await resp.json();
  $("#decode-output").textContent = JSON.stringify(data.ok ? data : data.errors, null, 2);
});

const catalogPageSize = 50;
let catalogOffset = 0;
let catalogTimer = null;

async function loadCatalog(reset = false) {
  if (reset) catalogOffset = 0;
  const params = new URLSearchParams({
    q: $("#catalog-search").value,
    category: $("#catalog-category").value,
    limit: String(catalogPageSize),
    offset: String(catalogOffset),
  });
  const response = await fetch(`/api/catalog?${params}`);
  const data = await response.json();
  if (!response.ok) {
    $("#catalog-count").textContent = (data.errors || ["Could not load catalog"]).join("; ");
    return;
  }
  const category = $("#catalog-category");
  if (category.options.length === 1) {
    for (const name of data.categories) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      category.appendChild(option);
    }
  }
  const body = $("#catalog-table tbody");
  body.replaceChildren();
  for (const asset of data.entries) {
    const row = document.createElement("tr");
    for (const value of [
      asset.name,
      asset.category,
      asset.uuid,
      asset.calibrated ? "Placement ready" : "Catalog only",
    ]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
  const first = data.total ? data.offset + 1 : 0;
  const last = Math.min(data.offset + data.entries.length, data.total);
  $("#catalog-count").textContent = `${first}–${last} of ${data.total} entries`;
  $("#catalog-prev").disabled = data.offset === 0;
  $("#catalog-next").disabled = data.offset + data.entries.length >= data.total;
}

$("#catalog-search").addEventListener("input", () => {
  clearTimeout(catalogTimer);
  catalogTimer = setTimeout(() => loadCatalog(true), 200);
});
$("#catalog-category").addEventListener("change", () => loadCatalog(true));
$("#catalog-prev").addEventListener("click", () => {
  catalogOffset = Math.max(0, catalogOffset - catalogPageSize);
  loadCatalog();
});
$("#catalog-next").addEventListener("click", () => {
  catalogOffset += catalogPageSize;
  loadCatalog();
});
loadCatalog().catch((err) => {
  $("#catalog-count").textContent = `Could not load catalog: ${err}`;
});
