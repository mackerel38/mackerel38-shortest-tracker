#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TARGET_USER = os.environ.get("TARGET_USER", "mackerel38")

BASE_URL = "https://kenkoooo.com/atcoder"
MERGED_URL = f"{BASE_URL}/resources/merged-problems.json"
PROBLEMS_URL = f"{BASE_URL}/resources/problems.json"
CONTESTS_URL = f"{BASE_URL}/resources/contests.json"
FROM_API = f"{BASE_URL}/atcoder-api/v3/from/{{}}"
USER_API = f"{BASE_URL}/atcoder-api/v3/user/submissions"

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state/tracker-state.json"
PUBLIC_PATH = ROOT / "docs/data/tracker.json"

REQUEST_INTERVAL = 1.1
OVERLAP_SECONDS = 10 * 60
GLOBAL_PAGE_LIMIT = 1000
USER_PAGE_LIMIT = 500
RECOMMENDATION_COUNT = 12
RECOMMENDATION_SCAN_LIMIT = 80
JST = timezone(timedelta(hours=9))

EXCLUDED_RECOMMENDATION_LANGUAGES = (
    "apl",
    "a言語",
    "clay",
    "dc",
    "ruby",
    "perl",
    "awk",
    "octave",
    "bash",
)


class TrackerError(RuntimeError):
    pass


_last_request_at = 0.0


def request_bytes(url: str) -> bytes:
    global _last_request_at

    last_error: Exception | None = None
    for attempt in range(3):
        wait = REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "mackerel38-shortest-tracker/2.0 "
                    "(https://github.com/mackerel38/"
                    "mackerel38-shortest-tracker)"
                ),
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                if response.status != 200:
                    raise TrackerError(f"HTTP {response.status}: {url}")
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
        finally:
            _last_request_at = time.monotonic()

    raise TrackerError(f"取得に失敗しました: {url}: {last_error}")


def request_json(url: str) -> Any:
    payload = request_bytes(url)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TrackerError(f"JSONの解析に失敗しました: {url}") from exc


def request_text(url: str) -> str:
    payload = request_bytes(url)
    return payload.decode("utf-8", errors="replace")


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
    temporary.replace(path)


def load_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def same_user(left: Any, right: Any) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.casefold() == right.casefold()
    )


def compact_shortest(problem: dict[str, Any]) -> dict[str, Any] | None:
    submission_id = problem.get("shortest_submission_id")
    user_id = problem.get("shortest_user_id")
    contest_id = problem.get("shortest_contest_id")
    length = problem.get("source_code_length")

    if (
        not isinstance(submission_id, int)
        or not isinstance(user_id, str)
        or not isinstance(contest_id, str)
        or not isinstance(length, int)
    ):
        return None

    return {
        "submission_id": submission_id,
        "user_id": user_id,
        "contest_id": contest_id,
        "length": length,
        "epoch_second": None,
        "language": None,
    }


def submission_snapshot(submission: dict[str, Any]) -> dict[str, Any]:
    return {
        "submission_id": int(submission["id"]),
        "user_id": str(submission["user_id"]),
        "contest_id": str(submission["contest_id"]),
        "length": int(submission["length"]),
        "epoch_second": int(submission["epoch_second"]),
        "language": str(submission.get("language") or ""),
    }


def copy_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "submission_id": snapshot.get("submission_id"),
        "user_id": snapshot.get("user_id"),
        "contest_id": snapshot.get("contest_id"),
        "length": snapshot.get("length"),
        "epoch_second": snapshot.get("epoch_second"),
        "language": snapshot.get("language"),
    }


def shortest_rank(snapshot: dict[str, Any] | None) -> tuple[int, int] | None:
    if not isinstance(snapshot, dict):
        return None
    length = snapshot.get("length")
    submission_id = snapshot.get("submission_id")
    if not isinstance(length, int) or not isinstance(submission_id, int):
        return None
    return (length, submission_id)


def is_strictly_better(
    candidate: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> bool:
    candidate_rank = shortest_rank(candidate)
    previous_rank = shortest_rank(previous)
    return (
        candidate_rank is not None
        and previous_rank is not None
        and candidate_rank < previous_rank
    )


def details_complete(snapshot: dict[str, Any] | None) -> bool:
    return (
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("epoch_second"), int)
        and isinstance(snapshot.get("language"), str)
        and bool(snapshot.get("language"))
    )


def new_state(now_epoch: int) -> dict[str, Any]:
    return {
        "version": 2,
        "target_user": TARGET_USER,
        "initialized_at": now_epoch,
        "last_checked_epoch": max(0, now_epoch - OVERLAP_SECONDS),
        "current": {},
        "ever_held": {},
        "updates": [],
        "update_keys": [],
        "submission_details": {},
        "target_problem_languages": {},
        "target_language_index_complete": False,
        "recommendation_day": "",
        "recommendations": [],
    }


def migrate_state(data: Any, now_epoch: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        return new_state(now_epoch)

    version = data.get("version")
    if version not in {1, 2}:
        raise TrackerError("state/tracker-state.json の形式が不正です")

    data["version"] = 2
    data["target_user"] = TARGET_USER
    data.setdefault("initialized_at", now_epoch)
    data.setdefault("last_checked_epoch", max(0, now_epoch - OVERLAP_SECONDS))
    data.setdefault("current", {})
    data.setdefault("ever_held", {})

    # 旧イベントは前後両方の日時・言語を持っていないため、
    # 不完全な履歴を表示せず、この機能の導入後から更新ログを記録する。
    data.setdefault("updates", [])
    data.setdefault("update_keys", [])
    data.setdefault("submission_details", {})
    data.setdefault("target_problem_languages", {})
    data.setdefault("target_language_index_complete", False)
    data.setdefault("recommendation_day", "")
    data.setdefault("recommendations", [])

    # 旧形式の誤った巻き戻し状態を修復する。
    for problem_id, held in data["ever_held"].items():
        if not isinstance(held, dict):
            continue
        current = data["current"].get(problem_id)
        if not isinstance(held.get("target_contest_id"), str):
            if isinstance(current, dict) and isinstance(current.get("contest_id"), str):
                held["target_contest_id"] = current.get("contest_id")
        if not isinstance(current, dict) or same_user(
            current.get("user_id"), TARGET_USER
        ):
            continue

        target_snapshot = {
            "submission_id": held.get("target_submission_id"),
            "user_id": TARGET_USER,
            "contest_id": (
                held.get("target_contest_id")
                or current.get("contest_id")
            ),
            "length": held.get("target_length"),
            "epoch_second": held.get("target_epoch_second"),
            "language": held.get("target_language"),
        }
        if is_strictly_better(target_snapshot, current):
            data["current"][problem_id] = target_snapshot

    data.pop("events", None)
    data.pop("event_keys", None)
    return data


def load_state(now_epoch: int) -> dict[str, Any] | None:
    data = load_json(STATE_PATH)
    if data is None:
        return None
    return migrate_state(data, now_epoch)


def strip_tags(fragment: str) -> str:
    return " ".join(
        html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split()
    )


def table_value(page: str, labels: tuple[str, ...]) -> str | None:
    alternatives = "|".join(re.escape(label) for label in labels)
    pattern = (
        rf"<th[^>]*>\s*(?:{alternatives})\s*</th>"
        rf"\s*<td[^>]*>(.*?)</td>"
    )
    match = re.search(pattern, page, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    value = strip_tags(match.group(1))
    return value or None


def parse_epoch(page: str) -> int | None:
    match = re.search(
        r"<time[^>]*>(.*?)</time>",
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None

    value = strip_tags(match.group(1))
    value = re.sub(r"\s+", " ", value).strip()

    formats = (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=JST)
            return int(parsed.timestamp())
        except ValueError:
            pass

    match = re.search(
        r"(\d{4}-\d{2}-\d{2})\s+"
        r"(\d{2}:\d{2}:\d{2})\s*([+-]\d{4})?",
        value,
    )
    if match is None:
        return None

    date_part, time_part, offset = match.groups()
    candidate = f"{date_part} {time_part}{offset or '+0900'}"
    return int(datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S%z").timestamp())


def fetch_submission_page_details(
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    contest_id = snapshot.get("contest_id")
    submission_id = snapshot.get("submission_id")
    if not isinstance(contest_id, str) or not isinstance(submission_id, int):
        return None

    url = (
        f"https://atcoder.jp/contests/{urllib.parse.quote(contest_id)}"
        f"/submissions/{submission_id}"
    )
    try:
        page = request_text(url)
    except TrackerError as exc:
        print(f"提出ページ詳細を取得できませんでした: {submission_id}: {exc}")
        return None

    language = table_value(page, ("言語", "Language"))
    epoch = parse_epoch(page)
    if not language and not isinstance(epoch, int):
        return None

    return {
        "language": language,
        "epoch_second": epoch,
    }


def hydrate_snapshot(
    state: dict[str, Any],
    snapshot: dict[str, Any],
) -> bool:
    if details_complete(snapshot):
        return True

    submission_id = snapshot.get("submission_id")
    if not isinstance(submission_id, int):
        return False

    cache = state["submission_details"]
    cached = cache.get(str(submission_id))
    if isinstance(cached, dict):
        if isinstance(cached.get("epoch_second"), int):
            snapshot["epoch_second"] = cached["epoch_second"]
        if isinstance(cached.get("language"), str) and cached["language"]:
            snapshot["language"] = cached["language"]
        if details_complete(snapshot):
            return True

    fetched = fetch_submission_page_details(snapshot)
    if fetched is None:
        return False

    if isinstance(fetched.get("epoch_second"), int):
        snapshot["epoch_second"] = fetched["epoch_second"]
    if isinstance(fetched.get("language"), str) and fetched["language"]:
        snapshot["language"] = fetched["language"]

    cache[str(submission_id)] = {
        "epoch_second": snapshot.get("epoch_second"),
        "language": snapshot.get("language"),
    }
    return details_complete(snapshot)


def cache_submission(
    state: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    submission_id = snapshot.get("submission_id")
    if not isinstance(submission_id, int):
        return
    if details_complete(snapshot):
        state["submission_details"][str(submission_id)] = {
            "epoch_second": snapshot.get("epoch_second"),
            "language": snapshot.get("language"),
        }


def fetch_all_user_submissions(user_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor = 0

    while True:
        query = urllib.parse.urlencode(
            {"user": user_id, "from_second": cursor}
        )
        payload = request_json(f"{USER_API}?{query}")
        if not isinstance(payload, list):
            raise TrackerError("ユーザー提出APIの形式が不正です")
        if not payload:
            break

        result.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < USER_PAGE_LIMIT:
            break

        epochs = [
            item.get("epoch_second")
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("epoch_second"), int)
        ]
        if not epochs:
            break

        next_cursor = max(epochs) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    return result


def index_target_submission(
    state: dict[str, Any],
    submission: dict[str, Any],
) -> None:
    if not same_user(submission.get("user_id"), TARGET_USER):
        return

    problem_id = submission.get("problem_id")
    language = submission.get("language")
    if not isinstance(problem_id, str) or not isinstance(language, str):
        return

    index = state["target_problem_languages"]
    languages = set(index.get(problem_id, []))
    languages.add(language)
    index[problem_id] = sorted(languages)


def ensure_target_language_index(
    state: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    if state.get("target_language_index_complete"):
        return {}

    print("推薦用にmackerel38の提出言語を初期集計します。")
    submissions = fetch_all_user_submissions(TARGET_USER)
    by_id: dict[int, dict[str, Any]] = {}

    for submission in submissions:
        index_target_submission(state, submission)
        submission_id = submission.get("id")
        if isinstance(submission_id, int):
            by_id[submission_id] = submission

    state["target_language_index_complete"] = True
    return by_id


def remember_target_hold(
    state: dict[str, Any],
    problem_id: str,
    snapshot: dict[str, Any],
) -> None:
    records = state["ever_held"]
    record = records.get(problem_id)

    if record is None:
        record = {
            "first_acquired_epoch": snapshot.get("epoch_second"),
            "last_acquired_epoch": snapshot.get("epoch_second"),
            "target_submission_id": snapshot.get("submission_id"),
            "target_contest_id": snapshot.get("contest_id"),
            "target_length": snapshot.get("length"),
            "target_language": snapshot.get("language"),
            "target_epoch_second": snapshot.get("epoch_second"),
            "acquisition_count": 1,
        }
        records[problem_id] = record
        return

    record["last_acquired_epoch"] = snapshot.get("epoch_second")
    record["target_submission_id"] = snapshot.get("submission_id")
    record["target_contest_id"] = snapshot.get("contest_id")
    record["target_length"] = snapshot.get("length")
    record["target_language"] = snapshot.get("language")
    record["target_epoch_second"] = snapshot.get("epoch_second")
    record["acquisition_count"] = int(record.get("acquisition_count") or 0) + 1

    if not isinstance(record.get("first_acquired_epoch"), int):
        record["first_acquired_epoch"] = snapshot.get("epoch_second")


def update_target_best(
    state: dict[str, Any],
    problem_id: str,
    snapshot: dict[str, Any],
) -> None:
    record = state["ever_held"].get(problem_id)
    if record is None:
        remember_target_hold(state, problem_id, snapshot)
        return

    record["target_submission_id"] = snapshot.get("submission_id")
    record["target_contest_id"] = snapshot.get("contest_id")
    record["target_length"] = snapshot.get("length")
    record["target_language"] = snapshot.get("language")
    record["target_epoch_second"] = snapshot.get("epoch_second")


def update_key(problem_id: str, submission_id: int) -> str:
    return f"{problem_id}:{submission_id}"


def add_update_log(
    state: dict[str, Any],
    problem_id: str,
    previous: dict[str, Any],
    current: dict[str, Any],
) -> None:
    submission_id = current.get("submission_id")
    if not isinstance(submission_id, int):
        return

    key = update_key(problem_id, submission_id)
    if key in state["update_keys"]:
        return

    # 表の両行に必要な情報を可能な限り補完する。
    hydrate_snapshot(state, previous)
    hydrate_snapshot(state, current)
    cache_submission(state, previous)
    cache_submission(state, current)

    state["update_keys"].append(key)
    state["updates"].append(
        {
            "problem_id": problem_id,
            "before": copy_snapshot(previous),
            "after": copy_snapshot(current),
        }
    )


def apply_shortest_update(
    state: dict[str, Any],
    problem_id: str,
    current: dict[str, Any],
) -> bool:
    previous = state["current"].get(problem_id)
    if isinstance(previous, dict) and not is_strictly_better(current, previous):
        return False

    tracked_before = problem_id in state["ever_held"]
    current_target = same_user(current.get("user_id"), TARGET_USER)
    previous_target = isinstance(previous, dict) and same_user(
        previous.get("user_id"), TARGET_USER
    )
    relevant = tracked_before or current_target

    cache_submission(state, current)

    if current_target:
        if previous_target:
            update_target_best(state, problem_id, current)
        else:
            remember_target_hold(state, problem_id, current)

    if relevant and isinstance(previous, dict):
        add_update_log(state, problem_id, previous, current)

    state["current"][problem_id] = copy_snapshot(current)
    return relevant


def fetch_recent_submissions(
    from_epoch: int,
    to_epoch: int,
) -> list[dict[str, Any]]:
    cursor = max(0, from_epoch)
    seen: set[int] = set()
    result: list[dict[str, Any]] = []

    while cursor <= to_epoch:
        payload = request_json(FROM_API.format(cursor))
        if not isinstance(payload, list):
            raise TrackerError("時刻指定提出APIの形式が不正です")
        if not payload:
            break

        maximum_epoch = cursor
        for submission in payload:
            if not isinstance(submission, dict):
                continue

            submission_id = submission.get("id")
            epoch = submission.get("epoch_second")
            if not isinstance(submission_id, int) or not isinstance(epoch, int):
                continue

            maximum_epoch = max(maximum_epoch, epoch)
            if epoch > to_epoch or submission_id in seen:
                continue

            seen.add(submission_id)
            result.append(submission)

        if len(payload) < GLOBAL_PAGE_LIMIT or maximum_epoch >= to_epoch:
            break

        next_cursor = maximum_epoch + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    result.sort(
        key=lambda item: (
            int(item.get("epoch_second") or 0),
            int(item.get("id") or 0),
        )
    )
    return result


def initialize_state(
    merged: list[dict[str, Any]],
    now_epoch: int,
) -> dict[str, Any]:
    state = new_state(now_epoch)

    for problem in merged:
        if not isinstance(problem, dict):
            continue
        problem_id = problem.get("id")
        snapshot = compact_shortest(problem)
        if isinstance(problem_id, str) and snapshot is not None:
            state["current"][problem_id] = snapshot

    target_submissions = ensure_target_language_index(state)

    for problem_id, snapshot in state["current"].items():
        if not same_user(snapshot.get("user_id"), TARGET_USER):
            continue

        submission_id = snapshot.get("submission_id")
        detail = (
            target_submissions.get(submission_id)
            if isinstance(submission_id, int)
            else None
        )
        if isinstance(detail, dict):
            snapshot.update(submission_snapshot(detail))
        else:
            hydrate_snapshot(state, snapshot)

        cache_submission(state, snapshot)
        remember_target_hold(state, problem_id, snapshot)

    return state


def process_recent(
    state: dict[str, Any],
    submissions: list[dict[str, Any]],
    contest_starts: dict[str, int],
) -> dict[int, dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}

    for submission in submissions:
        index_target_submission(state, submission)

        submission_id = submission.get("id")
        if isinstance(submission_id, int):
            by_id[submission_id] = submission

        if submission.get("result") != "AC":
            continue

        problem_id = submission.get("problem_id")
        contest_id = submission.get("contest_id")
        epoch = submission.get("epoch_second")
        length = submission.get("length")

        if (
            not isinstance(problem_id, str)
            or not isinstance(contest_id, str)
            or not isinstance(epoch, int)
            or not isinstance(length, int)
        ):
            continue

        contest_start = contest_starts.get(contest_id)
        if contest_start is not None and epoch <= contest_start:
            continue

        current = submission_snapshot(submission)
        apply_shortest_update(state, problem_id, current)

    return by_id


def reconcile(
    state: dict[str, Any],
    merged: list[dict[str, Any]],
    recent_by_id: dict[int, dict[str, Any]],
) -> None:
    for problem in merged:
        if not isinstance(problem, dict):
            continue

        problem_id = problem.get("id")
        authoritative = compact_shortest(problem)
        if not isinstance(problem_id, str) or authoritative is None:
            continue

        previous = state["current"].get(problem_id)
        if (
            isinstance(previous, dict)
            and previous.get("submission_id") == authoritative["submission_id"]
        ):
            authoritative["epoch_second"] = previous.get("epoch_second")
            authoritative["language"] = previous.get("language")
            state["current"][problem_id] = authoritative
            continue

        # merged-problems.json が新着提出APIより遅れている場合に、
        # 古い長い提出へ巻き戻さない。
        if isinstance(previous, dict) and not is_strictly_better(
            authoritative, previous
        ):
            continue

        submission_id = authoritative["submission_id"]
        detail = recent_by_id.get(submission_id)
        if isinstance(detail, dict):
            authoritative.update(submission_snapshot(detail))

        relevant = (
            problem_id in state["ever_held"]
            or same_user(authoritative.get("user_id"), TARGET_USER)
        )

        if relevant and not details_complete(authoritative):
            if not hydrate_snapshot(state, authoritative):
                # 詳細が反映されるまで現在値を進めず、次回再試行する。
                continue

        if relevant:
            apply_shortest_update(state, problem_id, authoritative)
        else:
            state["current"][problem_id] = authoritative


def hydrate_tracked_state(state: dict[str, Any]) -> None:
    for problem_id, held in state["ever_held"].items():
        if not isinstance(held, dict):
            continue

        target_snapshot = {
            "submission_id": held.get("target_submission_id"),
            "user_id": TARGET_USER,
            "contest_id": (
                held.get("target_contest_id")
                or (
                    state["current"].get(problem_id, {}).get("contest_id")
                    if isinstance(state["current"].get(problem_id), dict)
                    else None
                )
            ),
            "length": held.get("target_length"),
            "epoch_second": held.get("target_epoch_second"),
            "language": held.get("target_language"),
        }
        if hydrate_snapshot(state, target_snapshot):
            held["target_contest_id"] = target_snapshot.get("contest_id")
            held["target_length"] = target_snapshot.get("length")
            held["target_epoch_second"] = target_snapshot.get("epoch_second")
            held["target_language"] = target_snapshot.get("language")
            if not isinstance(held.get("first_acquired_epoch"), int):
                held["first_acquired_epoch"] = target_snapshot.get(
                    "epoch_second"
                )
            if not isinstance(held.get("last_acquired_epoch"), int):
                held["last_acquired_epoch"] = target_snapshot.get(
                    "epoch_second"
                )

        current = state["current"].get(problem_id)
        if isinstance(current, dict):
            hydrate_snapshot(state, current)

    for update in state["updates"]:
        if not isinstance(update, dict):
            continue
        before = update.get("before")
        after = update.get("after")
        if isinstance(before, dict):
            hydrate_snapshot(state, before)
        if isinstance(after, dict):
            hydrate_snapshot(state, after)


def problem_metadata(
    problems: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        problem_id = problem.get("id")
        contest_id = problem.get("contest_id")
        if not isinstance(problem_id, str) or not isinstance(contest_id, str):
            continue
        result[problem_id] = {
            "contest_id": contest_id,
            "problem_index": str(problem.get("problem_index") or ""),
            "name": str(problem.get("name") or problem_id),
            "title": str(
                problem.get("title")
                or problem.get("name")
                or problem_id
            ),
        }
    return result


def submission_url(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    contest_id = snapshot.get("contest_id")
    submission_id = snapshot.get("submission_id")
    if not isinstance(contest_id, str) or not isinstance(submission_id, int):
        return ""
    return (
        f"https://atcoder.jp/contests/{contest_id}"
        f"/submissions/{submission_id}"
    )


def problem_url(problem_id: str, contest_id: str) -> str:
    if not contest_id:
        return ""
    return (
        f"https://atcoder.jp/contests/{contest_id}"
        f"/tasks/{problem_id}"
    )


def is_cpp_language(language: str) -> bool:
    return language.strip().casefold().startswith("c++")


def recommendation_language_allowed(language: str) -> bool:
    normalized = language.strip().casefold()
    for excluded in EXCLUDED_RECOMMENDATION_LANGUAGES:
        if excluded == "dc":
            if normalized == "dc" or normalized.startswith("dc "):
                return False
        elif normalized.startswith(excluded):
            return False
    return True


def refresh_recommendations(
    state: dict[str, Any],
    metadata: dict[str, dict[str, str]],
    now_epoch: int,
) -> None:
    day = datetime.fromtimestamp(now_epoch, tz=JST).date().isoformat()

    candidates: list[str] = []
    for problem_id, languages in state["target_problem_languages"].items():
        if (
            not isinstance(problem_id, str)
            or not isinstance(languages, list)
            or not languages
            or not all(
                isinstance(language, str) and is_cpp_language(language)
                for language in languages
            )
        ):
            continue

        current = state["current"].get(problem_id)
        if not isinstance(current, dict):
            continue
        if same_user(current.get("user_id"), TARGET_USER):
            continue

        candidates.append(problem_id)

    seed_text = f"{TARGET_USER}:{day}:shortest-recommended-v1"
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest(), 16)
    random.Random(seed).shuffle(candidates)

    selected: list[str] = []
    scanned = 0
    for problem_id in candidates:
        if len(selected) >= RECOMMENDATION_COUNT:
            break
        if scanned >= RECOMMENDATION_SCAN_LIMIT:
            break
        scanned += 1

        current = state["current"].get(problem_id)
        if not isinstance(current, dict):
            continue
        if not hydrate_snapshot(state, current):
            continue

        language = current.get("language")
        if (
            not isinstance(language, str)
            or not recommendation_language_allowed(language)
        ):
            continue

        if problem_id not in metadata:
            continue
        selected.append(problem_id)

    state["recommendation_day"] = day
    state["recommendations"] = selected


def public_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {
            "submission_id": None,
            "submission_url": "",
            "user_id": None,
            "length": None,
            "epoch_second": None,
            "language": None,
        }
    return {
        **copy_snapshot(snapshot),
        "submission_url": submission_url(snapshot),
    }


def build_public(
    state: dict[str, Any],
    problems: list[dict[str, Any]],
    now_epoch: int,
) -> dict[str, Any]:
    metadata = problem_metadata(problems)
    rows: list[dict[str, Any]] = []

    for problem_id, held in state["ever_held"].items():
        if not isinstance(held, dict):
            continue

        info = metadata.get(problem_id, {})
        current = state["current"].get(problem_id, {})
        contest_id = str(
            (
                current.get("contest_id")
                if isinstance(current, dict)
                else None
            )
            or info.get("contest_id")
            or ""
        )
        target_submission_id = held.get("target_submission_id")
        target_contest_id = str(
            held.get("target_contest_id") or contest_id or ""
        )

        rows.append(
            {
                "problem_id": problem_id,
                "problem_name": info.get("name") or problem_id,
                "problem_title": (
                    info.get("title")
                    or info.get("name")
                    or problem_id
                ),
                "problem_index": info.get("problem_index") or "",
                "problem_url": problem_url(problem_id, contest_id),
                "status": (
                    "holding"
                    if isinstance(current, dict)
                    and same_user(current.get("user_id"), TARGET_USER)
                    else "updated"
                ),
                "first_acquired_epoch": held.get("first_acquired_epoch"),
                "last_acquired_epoch": held.get("last_acquired_epoch"),
                "acquisition_count": held.get("acquisition_count"),
                "target_submission_id": target_submission_id,
                "target_submission_url": (
                    f"https://atcoder.jp/contests/{target_contest_id}"
                    f"/submissions/{target_submission_id}"
                    if target_contest_id and isinstance(target_submission_id, int)
                    else ""
                ),
                "target_epoch_second": held.get("target_epoch_second"),
                "target_length": held.get("target_length"),
                "target_language": held.get("target_language"),
                "current_user_id": (
                    current.get("user_id")
                    if isinstance(current, dict)
                    else None
                ),
                "current_submission_id": (
                    current.get("submission_id")
                    if isinstance(current, dict)
                    else None
                ),
                "current_submission_url": submission_url(current),
                "current_epoch_second": (
                    current.get("epoch_second")
                    if isinstance(current, dict)
                    else None
                ),
                "current_length": (
                    current.get("length")
                    if isinstance(current, dict)
                    else None
                ),
                "current_language": (
                    current.get("language")
                    if isinstance(current, dict)
                    else None
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            int(row.get("last_acquired_epoch") or 0),
            str(row.get("problem_id") or ""),
        ),
        reverse=True,
    )

    # 現在mackerel38が保持していない問題だけを表示する。
    # mackerel38が取り返した時点で自動的にこの一覧から消える。
    active_updates: list[dict[str, Any]] = []
    for row in rows:
        if row["status"] != "updated":
            continue

        before = {
            "submission_id": row.get("target_submission_id"),
            "user_id": TARGET_USER,
            "contest_id": (
                state["ever_held"].get(row["problem_id"], {}).get(
                    "target_contest_id"
                )
                if isinstance(
                    state["ever_held"].get(row["problem_id"]), dict
                )
                else None
            ),
            "length": row.get("target_length"),
            "epoch_second": row.get("target_epoch_second"),
            "language": row.get("target_language"),
        }
        after = state["current"].get(row["problem_id"], {})
        active_updates.append(
            {
                "problem_id": row["problem_id"],
                "problem_name": row["problem_name"],
                "problem_url": row["problem_url"],
                "before": public_snapshot(before),
                "after": public_snapshot(after),
            }
        )

    active_updates.sort(
        key=lambda item: int(
            item.get("after", {}).get("epoch_second") or 0
        ),
        reverse=True,
    )

    update_log: list[dict[str, Any]] = []
    for update in state["updates"]:
        if not isinstance(update, dict):
            continue
        problem_id = update.get("problem_id")
        before = update.get("before")
        after = update.get("after")
        if (
            not isinstance(problem_id, str)
            or not isinstance(before, dict)
            or not isinstance(after, dict)
        ):
            continue

        info = metadata.get(problem_id, {})
        contest_id = str(
            after.get("contest_id")
            or before.get("contest_id")
            or info.get("contest_id")
            or ""
        )
        update_log.append(
            {
                "problem_id": problem_id,
                "problem_name": info.get("name") or problem_id,
                "problem_url": problem_url(problem_id, contest_id),
                "before": public_snapshot(before),
                "after": public_snapshot(after),
            }
        )

    update_log.sort(
        key=lambda item: (
            int(item["after"].get("epoch_second") or 0),
            int(item["after"].get("submission_id") or 0),
        ),
        reverse=True,
    )

    recommendations: list[dict[str, Any]] = []
    for problem_id in state.get("recommendations", []):
        if not isinstance(problem_id, str):
            continue
        current = state["current"].get(problem_id)
        info = metadata.get(problem_id)
        if not isinstance(current, dict) or not isinstance(info, dict):
            continue

        contest_id = str(
            current.get("contest_id")
            or info.get("contest_id")
            or ""
        )
        recommendations.append(
            {
                "problem_id": problem_id,
                "problem_name": info.get("name") or problem_id,
                "problem_url": problem_url(problem_id, contest_id),
                "current": public_snapshot(current),
            }
        )

    holding_count = sum(row["status"] == "holding" for row in rows)

    return {
        "version": 2,
        "target_user": TARGET_USER,
        "generated_epoch": now_epoch,
        "generated_iso": datetime.fromtimestamp(
            now_epoch, tz=timezone.utc
        ).isoformat(),
        "recommendation_day": state.get("recommendation_day"),
        "summary": {
            "ever_held_count": len(rows),
            "holding_count": holding_count,
            "updated_count": len(rows) - holding_count,
            "active_update_count": len(active_updates),
            "update_log_count": len(update_log),
            # 旧フロントエンドとの短い互換期間用
            "lost_count": len(rows) - holding_count,
            "loss_event_count": len(active_updates),
        },
        "problems": rows,
        "active_updates": active_updates,
        "update_log": update_log,
        "recommendations": recommendations,
        "notes": [
            "初回導入時は、その時点で保持していたShortestを登録します。",
            "一度でもmackerel38がShortestを取った問題は、以後、更新者が誰であってもShortestの更新を記録します。",
            "Shortest更新リストは現在mackerel38が保持していない問題だけを表示し、取り返すと自動的に消えます。",
            "Shortest更新ログは、この機能の導入後に検出した更新を記録します。",
            "Recommendedは、mackerel38の提出言語がC++だけで、現在のShortest言語が指定除外言語ではない問題から選びます。",
        ],
    }


def public_content_changed(new_public: dict[str, Any]) -> bool:
    old_public = load_json(PUBLIC_PATH)
    if not isinstance(old_public, dict):
        return True

    ignored = {"generated_epoch", "generated_iso"}
    left = {
        key: value
        for key, value in old_public.items()
        if key not in ignored
    }
    right = {
        key: value
        for key, value in new_public.items()
        if key not in ignored
    }
    return left != right


def main() -> int:
    now_epoch = int(time.time())

    merged = request_json(MERGED_URL)
    problems = request_json(PROBLEMS_URL)
    contests = request_json(CONTESTS_URL)

    if (
        not isinstance(merged, list)
        or not isinstance(problems, list)
        or not isinstance(contests, list)
    ):
        raise TrackerError("AtCoder Problemsのデータ形式が不正です")

    state = load_state(now_epoch)
    if state is None:
        print("初期データを作成します。")
        state = initialize_state(merged, now_epoch)
    else:
        target_details = ensure_target_language_index(state)
        if target_details:
            by_id = {
                submission_id: submission_snapshot(submission)
                for submission_id, submission in target_details.items()
            }
            for problem_id, held in state["ever_held"].items():
                if not isinstance(held, dict):
                    continue
                detail = by_id.get(held.get("target_submission_id"))
                if detail is not None:
                    held["target_contest_id"] = detail.get("contest_id")
                    held["target_length"] = detail.get("length")
                    held["target_epoch_second"] = detail.get(
                        "epoch_second"
                    )
                    held["target_language"] = detail.get("language")

        from_epoch = max(
            0,
            int(state.get("last_checked_epoch") or 0)
            - OVERLAP_SECONDS,
        )
        print(f"提出を取得します: {from_epoch} ～ {now_epoch}")
        submissions = fetch_recent_submissions(from_epoch, now_epoch)

        contest_starts = {
            str(contest["id"]): int(contest["start_epoch_second"])
            for contest in contests
            if isinstance(contest, dict)
            and isinstance(contest.get("id"), str)
            and isinstance(contest.get("start_epoch_second"), int)
        }

        recent_by_id = process_recent(
            state, submissions, contest_starts
        )
        reconcile(state, merged, recent_by_id)
        state["last_checked_epoch"] = now_epoch

    hydrate_tracked_state(state)

    metadata = problem_metadata(problems)
    refresh_recommendations(state, metadata, now_epoch)

    state["update_keys"] = sorted(set(state["update_keys"]))
    state["updates"].sort(
        key=lambda update: (
            int(
                update.get("after", {}).get("epoch_second") or 0
                if isinstance(update, dict)
                and isinstance(update.get("after"), dict)
                else 0
            ),
            int(
                update.get("after", {}).get("submission_id") or 0
                if isinstance(update, dict)
                and isinstance(update.get("after"), dict)
                else 0
            ),
        )
    )

    save_json(STATE_PATH, state)

    public = build_public(state, problems, now_epoch)
    if not public_content_changed(public):
        print("公開内容に変化はありません。")
        return 0

    save_json(PUBLIC_PATH, public)

    summary = public["summary"]
    print(
        "更新完了: "
        f"保持経験 {summary['ever_held_count']} / "
        f"現在保持 {summary['holding_count']} / "
        f"現在更新済み {summary['active_update_count']} / "
        f"更新ログ {summary['update_log_count']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrackerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
