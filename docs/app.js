const state = {
  data: null,
  tab: "problems",
  recommendations: [],
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
  if (!Number.isFinite(Number(epoch)) || Number(epoch) <= 0) {
    return "不明";
  }
  return formatter.format(new Date(Number(epoch) * 1000));
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

function byteText(value) {
  return Number.isFinite(Number(value)) ? `${Number(value)} B` : "不明";
}

function updateByteText(before, after) {
  if (!Number.isFinite(Number(before)) || !Number.isFinite(Number(after))) {
    return "不明";
  }
  const difference = Number(before) - Number(after);
  return difference > 0 ? `−${difference} B` : `${difference} B`;
}

function renderSummary() {
  const summary = state.data.summary ?? {};
  const cards = [
    ["保持経験", summary.ever_held_count ?? 0],
    ["現在保持中", summary.holding_count ?? 0],
    ["現在未保持", summary.active_update_count ?? 0],
    ["更新ログ", summary.update_log_count ?? 0],
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

  const rows = (state.data.problems ?? []).filter((row) => {
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
      const status = holding ? "保持中" : "他ユーザーが更新";
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
          <td class="number">${valueOrUnknown(row.target_length)}</td>
          <td>${valueOrUnknown(row.target_language)}</td>
          <td>${userLink(row.current_user_id)}</td>
          <td class="number">${valueOrUnknown(row.current_length)}</td>
          <td>${valueOrUnknown(row.current_language)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderActiveUpdates() {
  const rows = state.data.active_updates ?? [];
  document.querySelector("#active-update-count").textContent =
    `${rows.length} 問題が現在ほかのユーザーによって更新されています`;

  document.querySelector("#active-updates-body").innerHTML = rows
    .map((row) => {
      const before = row.before ?? {};
      const after = row.after ?? {};
      return `
        <tr>
          <td>${dateText(after.epoch_second)}</td>
          <td>${link(row.problem_url, `${row.problem_id} ${row.problem_name}`, "problem-link")}</td>
          <td>${userLink(after.user_id)}</td>
          <td class="number">${byteText(before.length)}</td>
          <td class="number">${byteText(after.length)}</td>
          <td class="number improvement">${updateByteText(before.length, after.length)}</td>
          <td>${valueOrUnknown(after.language)}</td>
          <td>${link(after.submission_url, "提出を見る")}</td>
        </tr>
      `;
    })
    .join("");
}

function renderUpdateLog() {
  const rows = state.data.update_log ?? [];
  document.querySelector("#update-log-count").textContent =
    `${rows.length} 件のShortest更新を記録`;

  document.querySelector("#update-log-body").innerHTML = rows
    .map((row) => {
      const before = row.before ?? {};
      const after = row.after ?? {};
      const problem = link(
        row.problem_url,
        `${row.problem_id} ${row.problem_name}`,
        "problem-link",
      );
      const difference = updateByteText(before.length, after.length);

      return `
        <tr class="update-before">
          <td>${problem}</td>
          <td>${dateText(before.epoch_second)}</td>
          <td>${userLink(before.user_id)}</td>
          <td>${valueOrUnknown(before.language)}</td>
          <td class="number">${byteText(before.length)}</td>
          <td>${link(before.submission_url, "提出を見る")}</td>
        </tr>
        <tr class="update-after">
          <td><span class="delta">${difference}</span></td>
          <td>${dateText(after.epoch_second)}</td>
          <td>${userLink(after.user_id)}</td>
          <td>${valueOrUnknown(after.language)}</td>
          <td class="number">${byteText(after.length)}</td>
          <td>${link(after.submission_url, "提出を見る")}</td>
        </tr>
      `;
    })
    .join("");
}

function shuffled(values) {
  const result = [...values];
  for (let index = result.length - 1; index > 0; index -= 1) {
    const target = Math.floor(Math.random() * (index + 1));
    [result[index], result[target]] = [result[target], result[index]];
  }
  return result;
}

function renderRecommendations() {
  const rows = state.recommendations;
  document.querySelector("#recommendation-count").textContent =
    `${rows.length} 問題を提案中`;

  document.querySelector("#recommendations-body").innerHTML = rows
    .map((row) => {
      const current = row.current ?? {};
      return `
        <tr>
          <td>${link(row.problem_url, `${row.problem_id} ${row.problem_name}`, "problem-link")}</td>
          <td>${userLink(current.user_id)}</td>
          <td>${valueOrUnknown(current.language)}</td>
          <td class="number">${byteText(current.length)}</td>
          <td>${link(current.submission_url, "提出を見る")}</td>
        </tr>
      `;
    })
    .join("");
}

function renderNotes() {
  document.querySelector("#notes").innerHTML = (state.data.notes ?? [])
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
    state.recommendations = [...(state.data.recommendations ?? [])];

    renderSummary();
    renderProblems();
    renderActiveUpdates();
    renderUpdateLog();
    renderRecommendations();
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

document
  .querySelector("#shuffle-recommendations")
  .addEventListener("click", () => {
    state.recommendations = shuffled(state.recommendations);
    renderRecommendations();
  });

load();
