const PAGE_SIZE = 100;
const MAX_SELECTION = 200;
const STORAGE_KEY = "all-shortest-selected-problems-v1";

const state = {
  data: [],
  filtered: [],
  page: 0,
  selected: new Set(),
  generatedEpoch: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function link(url, text, className = "") {
  if (!url) {
    return escapeHtml(text);
  }
  return `<a class="${className}" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(text)}</a>`;
}

function userLink(userId) {
  return link(
    `https://atcoder.jp/users/${encodeURIComponent(userId)}`,
    userId,
    "user-link",
  );
}

function loadSelection() {
  try {
    const values = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    if (Array.isArray(values)) {
      state.selected = new Set(values.filter((value) => typeof value === "string"));
    }
  } catch {
    state.selected = new Set();
  }
}

function saveSelection() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...state.selected]));
}

function currentRows() {
  const start = state.page * PAGE_SIZE;
  return state.filtered.slice(start, start + PAGE_SIZE);
}

function selectedItems() {
  const byId = new Map(state.data.map((row) => [row.problem_id, row]));
  return [...state.selected]
    .map((problemId) => byId.get(problemId))
    .filter(Boolean);
}

function setStatus(message) {
  document.querySelector("#archive-status").textContent = message;
}

function updateSelectionCount() {
  document.querySelector("#selected-count").textContent =
    `${state.selected.size}件選択中`;
  const rows = currentRows();
  document.querySelector("#toggle-page").checked =
    rows.length > 0 && rows.every((row) => state.selected.has(row.problem_id));
}

function applyFilters() {
  const query = document
    .querySelector("#archive-search")
    .value
    .trim()
    .toLowerCase();
  const maxBytesInput = document.querySelector("#archive-max-bytes").value;
  const maxBytes = maxBytesInput === "" ? null : Number(maxBytesInput);
  const sort = document.querySelector("#archive-sort").value;

  state.filtered = state.data.filter((row) => {
    if (Number.isFinite(maxBytes) && row.length > maxBytes) {
      return false;
    }
    if (!query) {
      return true;
    }
    return [
      row.problem_id,
      row.problem_name,
      row.problem_title,
      row.user_id,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query));
  });

  state.filtered.sort((left, right) => {
    if (sort === "bytes-asc") {
      return left.length - right.length || left.problem_id.localeCompare(right.problem_id);
    }
    if (sort === "bytes-desc") {
      return right.length - left.length || left.problem_id.localeCompare(right.problem_id);
    }
    if (sort === "user") {
      return left.user_id.localeCompare(right.user_id) ||
        left.problem_id.localeCompare(right.problem_id);
    }
    return left.problem_id.localeCompare(right.problem_id);
  });

  const maxPage = Math.max(0, Math.ceil(state.filtered.length / PAGE_SIZE) - 1);
  state.page = Math.min(state.page, maxPage);
  render();
}

function renderTable() {
  const rows = currentRows();
  document.querySelector("#archive-body").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td class="checkbox-cell">
            <input
              class="row-checkbox"
              type="checkbox"
              data-problem-id="${escapeHtml(row.problem_id)}"
              ${state.selected.has(row.problem_id) ? "checked" : ""}
              aria-label="${escapeHtml(row.problem_id)}を選択"
            >
          </td>
          <td>${link(row.problem_url, `${row.problem_id} ${row.problem_name}`, "problem-link")}</td>
          <td>${userLink(row.user_id)}</td>
          <td class="number">${escapeHtml(row.length)}</td>
          <td>${link(row.submission_url, "提出を見る")}</td>
        </tr>
      `,
    )
    .join("");

  document.querySelectorAll(".row-checkbox").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const problemId = checkbox.dataset.problemId;
      if (!problemId) {
        return;
      }

      if (checkbox.checked) {
        if (state.selected.size >= MAX_SELECTION) {
          checkbox.checked = false;
          setStatus(`1回の選択上限は${MAX_SELECTION}件です。`);
          return;
        }
        state.selected.add(problemId);
      } else {
        state.selected.delete(problemId);
      }

      saveSelection();
      updateSelectionCount();
    });
  });
}

function renderPagination() {
  const pageCount = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
  document.querySelector("#page-status").textContent =
    `${state.page + 1} / ${pageCount}ページ（${state.filtered.length}問）`;
  document.querySelector("#previous-page").disabled = state.page <= 0;
  document.querySelector("#next-page").disabled =
    state.page >= pageCount - 1;
}

function render() {
  renderTable();
  renderPagination();
  updateSelectionCount();

  const generated = Number(state.generatedEpoch);
  const generatedText = Number.isFinite(generated)
    ? new Date(generated * 1000).toLocaleString("ja-JP")
    : "不明";
  setStatus(`全${state.data.length}問・一覧更新 ${generatedText}`);
}

function addRowsToSelection(rows) {
  let added = 0;
  for (const row of rows) {
    if (state.selected.has(row.problem_id)) {
      continue;
    }
    if (state.selected.size >= MAX_SELECTION) {
      break;
    }
    state.selected.add(row.problem_id);
    added += 1;
  }
  saveSelection();
  render();
  setStatus(`${added}件追加しました。上限は${MAX_SELECTION}件です。`);
}

function downloadBlob(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function selectionPayload() {
  return {
    version: 1,
    exported_epoch: Math.floor(Date.now() / 1000),
    items: selectedItems().map((row) => ({
      problem_id: row.problem_id,
      submission_id: row.submission_id,
      submission_url: row.submission_url,
      user_id: row.user_id,
      length: row.length,
    })),
  };
}

function utf8ToBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  const chunkSize = 0x8000;
  for (let start = 0; start < bytes.length; start += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(start, start + chunkSize));
  }
  return btoa(binary);
}

function downloadSelectionJson() {
  if (state.selected.size === 0) {
    setStatus("問題を1件以上選択してください。");
    return;
  }
  const payload = JSON.stringify(selectionPayload(), null, 2);
  downloadBlob(
    `shortest-selection-${state.selected.size}.json`,
    `${payload}\n`,
    "application/json;charset=utf-8",
  );
}

function downloadShell() {
  if (state.selected.size === 0) {
    setStatus("問題を1件以上選択してください。");
    return;
  }

  const encoded = utf8ToBase64(JSON.stringify(selectionPayload()));
  const shell = `#!/usr/bin/env bash
set -euo pipefail

repo="$HOME/mackerel38-shortest-tracker"
downloader="$repo/scripts/download_shortest_codes.py"

if [[ ! -f "$downloader" ]]; then
  echo "ダウンローダーが見つかりません: $downloader" >&2
  exit 1
fi

selection="$(mktemp)"
trap 'rm -f "$selection"' EXIT

base64 -d > "$selection" <<'SHORTEST_SELECTION_BASE64'
${encoded}
SHORTEST_SELECTION_BASE64

python3 "$downloader" "$selection"
`;

  downloadBlob(
    `download-shortest-codes-${state.selected.size}.sh`,
    shell,
    "text/x-shellscript;charset=utf-8",
  );
}

async function load() {
  loadSelection();

  try {
    const response = await fetch(
      `data/all-shortest.json?t=${Date.now()}`,
      { cache: "no-store" },
    );
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    state.data = Array.isArray(payload.problems) ? payload.problems : [];
    state.generatedEpoch = payload.generated_epoch;

    const validIds = new Set(state.data.map((row) => row.problem_id));
    state.selected = new Set(
      [...state.selected].filter((problemId) => validIds.has(problemId)),
    );
    saveSelection();
    applyFilters();
  } catch (error) {
    setStatus(`一覧の読込に失敗しました: ${error.message}`);
  }
}

["#archive-search", "#archive-max-bytes", "#archive-sort"].forEach((selector) => {
  document.querySelector(selector).addEventListener("input", () => {
    state.page = 0;
    applyFilters();
  });
  document.querySelector(selector).addEventListener("change", () => {
    state.page = 0;
    applyFilters();
  });
});

document.querySelector("#previous-page").addEventListener("click", () => {
  if (state.page > 0) {
    state.page -= 1;
    render();
  }
});

document.querySelector("#next-page").addEventListener("click", () => {
  const pageCount = Math.ceil(state.filtered.length / PAGE_SIZE);
  if (state.page + 1 < pageCount) {
    state.page += 1;
    render();
  }
});

document.querySelector("#select-page").addEventListener("click", () => {
  addRowsToSelection(currentRows());
});

document.querySelector("#select-filtered").addEventListener("click", () => {
  addRowsToSelection(state.filtered);
});

document.querySelector("#toggle-page").addEventListener("change", (event) => {
  const rows = currentRows();
  if (event.target.checked) {
    addRowsToSelection(rows);
  } else {
    rows.forEach((row) => state.selected.delete(row.problem_id));
    saveSelection();
    render();
  }
});

document.querySelector("#clear-selection").addEventListener("click", () => {
  state.selected.clear();
  saveSelection();
  render();
  setStatus("選択を解除しました。");
});

document.querySelector("#download-json").addEventListener(
  "click",
  downloadSelectionJson,
);
document.querySelector("#download-shell").addEventListener(
  "click",
  downloadShell,
);

load();
