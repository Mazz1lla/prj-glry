"use strict";

// ============================================================
// Общее состояние
// ============================================================
const state = {
  folder: "",
  files: [],
  index: 0,
  history: [],
  currentTags: [],       // массив чипов для текущего изображения
  currentBucket: "",
  browsePath: "",
  searchResults: [],     // {id, thumb, width, height, source, selected}
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function toast(msg, type = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (type ? " " + type : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || res.statusText);
  }
  return data;
}

// ============================================================
// Вкладки
// ============================================================
$$(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab-btn").forEach((b) => b.classList.remove("active"));
    $$(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
  });
});

// ============================================================
// Рабочая папка
// ============================================================
async function refreshState() {
  const s = await api("/api/state");
  state.folder = s.folder;
  $("#folderLabel").textContent = s.folder || "не выбрана";
  fillBucketLists(s.buckets || []);
  if (s.folder) {
    await rebuildFileList();
    await refreshSearchBuckets();
  }
}

function fillBucketLists(buckets) {
  for (const id of ["#bucketList", "#bucketList2"]) {
    const dl = $(id);
    dl.innerHTML = buckets.map((b) => `<option value="${b}">`).join("");
  }
}

$("#btnChooseFolder").addEventListener("click", () => openFolderModal());

function openFolderModal() {
  $("#folderModal").classList.remove("hidden");
  browseTo(state.folder || undefined);
}

async function browseTo(path) {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  const data = await api("/api/browse" + q);
  state.browsePath = data.path;
  $("#browsePath").textContent = data.path;
  $("#browseList").innerHTML = data.dirs
    .map((d) => `<div class="browse-item" data-name="${d}">📁 ${d}</div>`)
    .join("") || `<div class="browse-item" style="color:var(--text-dim)">(нет вложенных папок)</div>`;
  $("#browseList").querySelectorAll(".browse-item[data-name]").forEach((el) => {
    el.addEventListener("click", () => browseTo(state.browsePath + "/" + el.dataset.name));
  });
  $("#btnBrowseUp").dataset.parent = data.parent || "";
}
$("#btnBrowseUp").addEventListener("click", () => {
  const p = $("#btnBrowseUp").dataset.parent;
  if (p) browseTo(p);
});
$("#btnBrowseCancel").addEventListener("click", () => $("#folderModal").classList.add("hidden"));
$("#btnBrowseChoose").addEventListener("click", async () => {
  try {
    await api("/api/folder", { method: "POST", body: JSON.stringify({ folder: state.browsePath }) });
    $("#folderModal").classList.add("hidden");
    toast("Рабочая папка выбрана", "success");
    await refreshState();
  } catch (e) {
    toast(e.message, "error");
  }
});

// ============================================================
// Список файлов / навигация
// ============================================================
async function rebuildFileList() {
  const mode = $("#onlyUntagged").checked ? "untagged" : "all";
  const data = await api(`/api/files?mode=${mode}`);
  state.files = data.files;
  state.index = 0;
  state.history = [];
  updateProgress();
  await showCurrent();
}
$("#onlyUntagged").addEventListener("change", rebuildFileList);

function updateProgress() {
  const n = state.files.length;
  $("#progressLabel").textContent = n ? `${Math.min(state.index + 1, n)}/${n}` : "0/0";
}

async function showCurrent(overrideFilename) {
  updateProgress();
  const img = $("#mainImage");
  const empty = $("#emptyState");
  const filename = overrideFilename || state.files[state.index];
  if (!filename || (!overrideFilename && state.index >= state.files.length)) {
    img.classList.add("hidden");
    empty.classList.remove("hidden");
    $("#filenameLabel").textContent = "—";
    $("#bucketBadge").classList.add("hidden");
    setChips([]);
    $("#presetBanner").classList.add("hidden");
    return;
  }
  empty.classList.add("hidden");
  img.classList.remove("hidden");

  img.src = `/api/image/${encodeURIComponent(filename)}?t=${Date.now()}`;
  $("#filenameLabel").textContent = filename;

  const item = await api(`/api/item/${encodeURIComponent(filename)}`);
  state.currentBucket = item.bucket;

  if (item.bucket) {
    $("#bucketBadge").textContent = item.bucket;
    $("#bucketBadge").classList.remove("hidden");
    $("#bucketInput").value = item.bucket;
  } else {
    $("#bucketBadge").classList.add("hidden");
    $("#bucketInput").value = "";
  }

  // Теги: если уже есть — показываем их. Если пусто и есть пресет бакета — предзаполняем пресетом (редактируемо).
  let tagsStr = item.tags;
  let fromPreset = false;
  if (!tagsStr && item.preset) {
    tagsStr = item.preset;
    fromPreset = true;
  }
  setChips(tagsStr ? tagsStr.split(/\s+/).filter(Boolean) : []);

  if (item.preset) {
    $("#presetBanner").classList.remove("hidden");
    $("#presetText").textContent = fromPreset
      ? `📌 Пресет бакета «${item.bucket}» подставлен — отредактируйте при необходимости`
      : `📌 У бакета «${item.bucket}» есть пресет: ${item.preset}`;
  } else {
    $("#presetBanner").classList.add("hidden");
  }

  await loadQuickTags(item.bucket);
}

// ============================================================
// Чипы тегов
// ============================================================
function setChips(tags) {
  state.currentTags = [...tags];
  renderChips();
}
function renderChips() {
  $("#chips").innerHTML = state.currentTags
    .map((t, i) => `<span class="chip" data-i="${i}">${escapeHtml(t)}<span class="x" data-i="${i}">✕</span></span>`)
    .join("");
  $("#chips").querySelectorAll(".x").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      state.currentTags.splice(+el.dataset.i, 1);
      renderChips();
    });
  });
}
function addTag(tag) {
  tag = tag.trim();
  if (!tag) return;
  if (!state.currentTags.includes(tag)) {
    state.currentTags.push(tag);
    renderChips();
  }
  $("#tagInput").value = "";
  hideSuggestions();
}
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("#chipsBox").addEventListener("click", (e) => {
  if (e.target.id === "chipsBox" || e.target.id === "chips") $("#tagInput").focus();
});

// ---- автоподсказки при вводе ----
let suggestTimer = null;
let suggestItems = [];
let suggestActive = -1;

$("#tagInput").addEventListener("input", () => {
  clearTimeout(suggestTimer);
  suggestTimer = setTimeout(fetchSuggestions, 120);
});
$("#tagInput").addEventListener("focus", fetchSuggestions);
$("#tagInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    if (suggestActive >= 0 && suggestItems[suggestActive]) {
      addTag(suggestItems[suggestActive].tag);
    } else {
      addTag($("#tagInput").value);
    }
  } else if (e.key === "Backspace" && !$("#tagInput").value) {
    state.currentTags.pop();
    renderChips();
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    suggestActive = Math.min(suggestActive + 1, suggestItems.length - 1);
    renderSuggestions();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    suggestActive = Math.max(suggestActive - 1, 0);
    renderSuggestions();
  } else if (e.key === "Escape") {
    hideSuggestions();
  }
});

async function fetchSuggestions() {
  const prefix = $("#tagInput").value.trim();
  const bucket = state.currentBucket || "";
  const data = await api(`/api/suggestions?bucket=${encodeURIComponent(bucket)}&prefix=${encodeURIComponent(prefix)}&limit=25`);
  suggestItems = data.suggestions.filter((s) => !state.currentTags.includes(s.tag));
  suggestActive = suggestItems.length ? 0 : -1;
  renderSuggestions();
}
function renderSuggestions() {
  const box = $("#suggestBox");
  if (!suggestItems.length) {
    hideSuggestions();
    return;
  }
  box.innerHTML = suggestItems
    .map(
      (s, i) =>
        `<div class="suggest-item ${i === suggestActive ? "active" : ""}" data-i="${i}">
          <span>${s.in_bucket ? '<span class="star">★</span>' : ""}${escapeHtml(s.tag)}</span>
          <span class="freq">${s.freq}</span>
        </div>`
    )
    .join("");
  box.classList.remove("hidden");
  box.querySelectorAll(".suggest-item").forEach((el) => {
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      addTag(suggestItems[+el.dataset.i].tag);
    });
  });
}
function hideSuggestions() {
  $("#suggestBox").classList.add("hidden");
  suggestItems = [];
  suggestActive = -1;
}
document.addEventListener("click", (e) => {
  if (!$("#chipsBox").contains(e.target)) hideSuggestions();
});

// ---- быстрые теги бакета (клик или Alt+1..9) ----
async function loadQuickTags(bucket) {
  const data = await api(`/api/suggestions?bucket=${encodeURIComponent(bucket || "")}&limit=9`);
  const list = data.suggestions;
  $("#quickTags").innerHTML = list
    .map(
      (s, i) =>
        `<span class="qtag" data-tag="${escapeHtml(s.tag)}"><span class="idx">${i + 1}</span>${escapeHtml(s.tag)}</span>`
    )
    .join("") || `<span style="color:var(--text-dim);font-size:12.5px">пока нет данных для этого бакета</span>`;
  $("#quickTags").querySelectorAll(".qtag").forEach((el) => {
    el.addEventListener("click", () => addTag(el.dataset.tag));
  });
  state.quickTagList = list;
}

document.addEventListener("keydown", (e) => {
  if (e.altKey && /^[1-9]$/.test(e.key)) {
    const idx = +e.key - 1;
    const list = state.quickTagList || [];
    if (list[idx]) {
      e.preventDefault();
      addTag(list[idx].tag);
    }
  }
});

// ============================================================
// Сохранение / навигация по изображениям
// ============================================================
async function saveAndNext() {
  if (!state.files.length || state.index >= state.files.length) return;
  const filename = state.files[state.index];
  const tagString = state.currentTags.join(" ").trim();
  if (!tagString) {
    toast("Введите хотя бы один тег", "error");
    return;
  }
  try {
    await api(`/api/item/${encodeURIComponent(filename)}`, { method: "POST", body: JSON.stringify({ tags: tagString }) });
  } catch (e) {
    toast(e.message, "error");
    return;
  }
  state.history.push(filename);
  if ($("#onlyUntagged").checked) {
    // Файл больше не в списке "неразмеченных" — убираем его из локального списка.
    // Следующий файл сдвигается на текущую позицию, поэтому индекс не увеличиваем.
    state.files.splice(state.index, 1);
  } else {
    state.index++;
  }
  await showCurrent();
}
function skipImage() {
  if (!state.files.length || state.index >= state.files.length) return;
  state.history.push(state.files[state.index]);
  state.index++;
  showCurrent();
}
function prevImage() {
  if (!state.history.length) return;
  const filename = state.history.pop();
  // Если файл всё ещё есть в текущем отфильтрованном списке — переходим к нему по индексу
  // (это сохраняет обычную навигацию вперёд/назад). Если его там уже нет (например,
  // он был размечен и скрыт фильтром «только неразмеченные») — просто показываем его,
  // не трогая индекс списка.
  const idx = state.files.indexOf(filename);
  if (idx >= 0) {
    state.index = idx;
    showCurrent();
  } else {
    showCurrent(filename);
  }
}

$("#btnSaveNext").addEventListener("click", saveAndNext);
$("#btnSkip").addEventListener("click", skipImage);
$("#btnPrev").addEventListener("click", prevImage);

document.addEventListener("keydown", (e) => {
  const inField = e.target.id === "tagInput" || e.target.tagName === "INPUT";
  if (e.key === "Enter" && e.target.id !== "tagInput") {
    // Enter вне поля тега — сохранить и дальше
  }
  if (e.key === "ArrowRight" && !inField) skipImage();
  if (e.key === "ArrowLeft" && !inField) prevImage();
});

// ---- копировать теги из предыдущего файла того же бакета ----
$("#btnCopyPrev").addEventListener("click", async () => {
  const bucket = state.currentBucket;
  if (!bucket) {
    toast("Текущий файл не в формате бакета", "error");
    return;
  }
  const data = await api(`/api/files?mode=all`);
  const files = data.files.filter((f) => f.startsWith(bucket + "_") && f !== state.files[state.index]);
  // берём с самым большим номером, у которого уже есть теги
  let best = null, bestN = -1;
  for (const f of files) {
    const m = f.match(/^(.+)_(\d+)\.\w+$/i);
    if (!m) continue;
    const n = +m[2];
    const item = await api(`/api/item/${encodeURIComponent(f)}`);
    if (item.has_tags && n > bestN) {
      best = item.tags;
      bestN = n;
    }
  }
  if (best) {
    setChips(best.split(/\s+/).filter(Boolean));
    toast("Теги скопированы");
  } else {
    toast("Нет размеченных файлов в этом бакете", "error");
  }
});

// ============================================================
// Переименование
// ============================================================
$("#btnRenameBucket").addEventListener("click", async () => {
  if (!state.files.length) return;
  const filename = state.files[state.index];
  const bucket = $("#bucketInput").value.trim();
  if (!bucket) {
    toast("Введите название бакета", "error");
    return;
  }
  try {
    const res = await api("/api/rename", { method: "POST", body: JSON.stringify({ filename, bucket }) });
    if (res.unchanged) {
      toast("Имя не изменилось");
    } else {
      toast(`Переименовано в ${res.filename}`, "success");
      state.files[state.index] = res.filename;
      await refreshState();
      state.index = state.files.indexOf(res.filename);
      if (state.index < 0) state.index = 0;
      await showCurrent();
    }
  } catch (e) {
    toast(e.message, "error");
  }
});

$("#btnRenameFree").addEventListener("click", () => {
  if (!state.files.length) return;
  $("#renameFreeInput").value = state.files[state.index];
  $("#renameModal").classList.remove("hidden");
  $("#renameFreeInput").focus();
});
$("#btnRenameFreeCancel").addEventListener("click", () => $("#renameModal").classList.add("hidden"));
$("#btnRenameFreeConfirm").addEventListener("click", async () => {
  const filename = state.files[state.index];
  const newName = $("#renameFreeInput").value.trim();
  if (!newName) return;
  try {
    const res = await api("/api/rename_free", { method: "POST", body: JSON.stringify({ filename, new_name: newName }) });
    $("#renameModal").classList.add("hidden");
    toast(`Переименовано в ${res.filename}`, "success");
    state.files[state.index] = res.filename;
    await showCurrent();
  } catch (e) {
    toast(e.message, "error");
  }
});

// ============================================================
// Удаление
// ============================================================
$("#btnDelete").addEventListener("click", async () => {
  if (!state.files.length) return;
  const filename = state.files[state.index];
  if (!confirm(`Удалить файл «${filename}»? Это действие необратимо.`)) return;
  try {
    await api(`/api/item/${encodeURIComponent(filename)}`, { method: "DELETE" });
  } catch (e) {
    toast(e.message, "error");
    return;
  }
  state.files.splice(state.index, 1);
  if (state.index >= state.files.length) state.index = Math.max(0, state.files.length - 1);
  toast("Файл удалён");
  await showCurrent();
});

// ============================================================
// Пресеты
// ============================================================
$("#btnSavePreset").addEventListener("click", async () => {
  if (!state.currentBucket) {
    toast("Файл не в формате бакета — нет бакета для пресета", "error");
    return;
  }
  const tags = state.currentTags.join(" ").trim();
  await api(`/api/presets/${encodeURIComponent(state.currentBucket)}`, { method: "POST", body: JSON.stringify({ tags }) });
  toast(`Пресет бакета «${state.currentBucket}» сохранён`, "success");
  await showCurrent();
});

// ============================================================
// ПОИСК И ЗАГРУЗКА
// ============================================================
async function refreshSearchBuckets() {
  const data = await api("/api/buckets");
  fillBucketLists(data.buckets.map((b) => b.name));
}

$("#searchQuery").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch(0);
});
$("#btnSearch").addEventListener("click", () => runSearch(0));
$("#btnLoadMore").addEventListener("click", () => runSearch((state.searchPage || 0) + 1));

async function runSearch(page) {
  const query = $("#searchQuery").value.trim();
  if (!query) {
    toast("Введите поисковый запрос", "error");
    return;
  }
  if (!state.folder) {
    toast("Сначала выберите рабочую папку", "error");
    return;
  }
  state.searchPage = page;
  $("#searchStatus").textContent = "Ищу и загружаю превью…";
  if (page === 0) {
    state.searchResults = [];
    $("#searchGrid").innerHTML = "";
    if (!$("#searchBucket").value.trim()) {
      $("#searchBucket").value = query.split(/\s+/)[0]?.toLowerCase() || "misc";
    }
    if (!$("#searchTags").value.trim()) {
      $("#searchTags").value = `${$("#searchBucket").value} ${query}`.trim();
    }
  }
  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        engine: $("#searchEngine").value,
        page,
        count: +$("#searchCount").value,
        square_only: $("#squareOnly").checked,
      }),
    });
    for (const r of data.results) {
      r.selected = true;
      state.searchResults.push(r);
    }
    renderSearchGrid();
    $("#searchStatus").textContent = `Найдено превью: ${state.searchResults.length}` + (data.errors.length ? " (частичные ошибки: " + data.errors.join("; ") + ")" : "");
  } catch (e) {
    $("#searchStatus").textContent = "";
    toast(e.message, "error");
  }
}

function renderSearchGrid() {
  $("#searchGrid").innerHTML = state.searchResults
    .map(
      (r) => `
      <div class="result-card ${r.selected ? "selected" : ""}" data-id="${r.id}">
        <img src="data:image/jpeg;base64,${r.thumb}" />
        <div class="result-meta">${r.width}×${r.height} · ${r.source}</div>
      </div>`
    )
    .join("");
  $("#searchGrid").querySelectorAll(".result-card").forEach((el) => {
    el.addEventListener("click", () => {
      const r = state.searchResults.find((x) => x.id === el.dataset.id);
      r.selected = !r.selected;
      el.classList.toggle("selected", r.selected);
    });
  });
}

$("#btnSelectAll").addEventListener("click", () => {
  state.searchResults.forEach((r) => (r.selected = true));
  renderSearchGrid();
});
$("#btnSelectNone").addEventListener("click", () => {
  state.searchResults.forEach((r) => (r.selected = false));
  renderSearchGrid();
});

$("#btnDownload").addEventListener("click", async () => {
  const bucket = $("#searchBucket").value.trim();
  if (!bucket) {
    toast("Укажите бакет для сохранения", "error");
    return;
  }
  const ids = state.searchResults.filter((r) => r.selected).map((r) => r.id);
  if (!ids.length) {
    toast("Ничего не выбрано", "error");
    return;
  }
  try {
    const res = await api("/api/download", {
      method: "POST",
      body: JSON.stringify({ ids, bucket, tags: $("#searchTags").value.trim() }),
    });
    toast(`Сохранено: ${res.saved}, дублей пропущено: ${res.duplicates}, ошибок: ${res.failed}`, "success");
    await refreshState();
  } catch (e) {
    toast(e.message, "error");
  }
});

// ============================================================
// Инициализация
// ============================================================
refreshState();
