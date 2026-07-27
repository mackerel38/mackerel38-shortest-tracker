const state = {
  data: null,
  tab: "problems",
};

const formatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function dateText(epoch) {
  if (epoch === null || epoch === undefined || epoch === "") {
    return "不明";
  }
  const value = Number(epoch);
  if (!Number.isFinite(value) || value <= 0) {
    return "不明";
  }
  return formatter.format(new Date(value * 1000));
}

function link(url, text, className = "") {
  const safeText = escapeHtml(text);
  if (!url) {
    return safeText;
  }
  return `<a class="${className}" href="${escapeHtml(url)}" target="_blank" rel="noopener">${safeText}</a>`;
}

function userLink(userId) {
  if (!userId) {
    return "不明";
  }
  return link(
    `https://atcoder.jp/users/${encodeURIComponent(userId)}`,
    userId,
    "user-link",
  );
}

function valueOrUnknown(value) {
  return value === null || value === undefined || value === ""
    ? "不明"
    : escapeHtml(value);
}

function renderSummary() {
  const summary = state.data.summary;
  const cards = [
    ["保持経験", summary.ever_held_count],
    ["現在保持中", summary.holding_count],
    ["奪取済み", summary.lost_count],
    ["奪取イベント", summary.loss_event_count],
  ];

  document.querySelector("#summary").innerHTML = cards
    .map(
      ([label, value]) => `
        <article class="summary-card">
          <p>${escapeHtml(label)}</p>
          <strong>${escapeHtml(value)}</strong>
        </article>
      `,
    )
    .join("");

  document.querySelector("#updated").textContent =
    `最終更新: ${dateText(state.data.generated_epoch)} JST`;
}

function problemRows() {
  const query = document.querySelector("#search").value.trim().toLowerCase();
  const status = document.querySelector("#status-filter").value;
  const sort = document.querySelector("#sort").value;

  const rows = state.data.problems.filter((row) => {
    if (status !== "all" && row.status !== status) {
      return false;
    }
    if (!query) {
      return true;
    }

    return [
      row.problem_id,
      row.problem_name,
      row.problem_title,
      row.current_user_id,
      row.target_language,
      row.current_language,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query));
  });

  rows.sort((left, right) => {
    if (sort === "bytes-asc") {
      return (
        Number(left.target_length ?? Number.MAX_SAFE_INTEGER) -
        Number(right.target_length ?? Number.MAX_SAFE_INTEGER)
      );
    }
    if (sort === "problem-asc") {
      return left.problem_id.localeCompare(right.problem_id);
    }
    return (
      Number(right.last_acquired_epoch ?? 0) -
      Number(left.last_acquired_epoch ?? 0)
    );
  });

  return rows;
}

function renderProblems() {
  const rows = problemRows();
  document.querySelector("#problem-count").textContent =
    `${rows.length} 問題を表示中`;

  document.querySelector("#problems-body").innerHTML = rows
    .map((row) => {
      const holding = row.status === "holding";
      const status = holding ? "保持中" : "奪取済み";
      const targetSubmission = row.target_submission_url
        ? link(row.target_submission_url, "提出を見る")
        : "不明";

      return `
        <tr>
          <td><span class="status ${escapeHtml(row.status)}">${status}</span></td>
          <td>
            ${link(row.problem_url, `${row.problem_id} ${row.problem_name}`, "problem-link")}
          </td>
          <td>${targetSubmission}</td>
          <td>${dateText(row.target_epoch_second)}</td>
          <td class="number">${valueOrUnknown(row.target_length)}</td>
          <td>${valueOrUnknown(row.target_language)}</td>
          <td>${userLink(row.current_user_id)}</td>
          <td>${dateText(row.current_epoch_second)}</td>
          <td class="number">${valueOrUnknown(row.current_length)}</td>
          <td>${valueOrUnknown(row.current_language)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderLosses() {
  const rows = state.data.losses;
  document.querySelector("#loss-count").textContent =
    `${rows.length} 件の奪取を記録`;

  document.querySelector("#losses-body").innerHTML = rows
    .map((row) => {
      const before = Number.isFinite(Number(row.previous_length))
        ? `${row.previous_length} B`
        : "不明";
      const after = Number.isFinite(Number(row.new_length))
        ? `${row.new_length} B`
        : "不明";
      const difference =
        Number.isFinite(Number(row.previous_length)) &&
        Number.isFinite(Number(row.new_length))
          ? `${Number(row.new_length) - Number(row.previous_length)} B`
          : "不明";

      return `
        <tr>
          <td>${dateText(row.epoch_second)}</td>
          <td>${link(row.problem_url, `${row.problem_id} ${row.problem_name}`, "problem-link")}</td>
          <td>${userLink(row.new_user_id)}</td>
          <td class="number">${before}</td>
          <td class="number">${after}</td>
          <td class="number">${difference}</td>
          <td>${valueOrUnknown(row.language)}</td>
          <td>${link(row.submission_url, "提出を見る")}</td>
        </tr>
      `;
    })
    .join("");
}

function renderNotes() {
  document.querySelector("#notes").innerHTML = state.data.notes
    .map((note) => `<li>${escapeHtml(note)}</li>`)
    .join("");
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `${tab}-panel`);
  });
}

async function load() {
  try {
    const response = await fetch(`data/tracker.json?t=${Date.now()}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    state.data = await response.json();
    renderSummary();
    renderProblems();
    renderLosses();
    renderNotes();
  } catch (error) {
    document.querySelector("#updated").textContent =
      `データの読込に失敗しました: ${error.message}`;
  }
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => setTab(button.dataset.tab));
});

["#search", "#status-filter", "#sort"].forEach((selector) => {
  document.querySelector(selector).addEventListener("input", renderProblems);
  document.querySelector(selector).addEventListener("change", renderProblems);
});

load();
