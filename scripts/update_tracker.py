#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TARGET_USER = os.environ.get("TARGET_USER", "mackerel38")
TARGET_LOWER = TARGET_USER.lower()

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


class TrackerError(RuntimeError):
    pass


_last_request_at = 0.0


def request_json(url: str) -> Any:
    global _last_request_at

    wait = REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mackerel38-shortest-tracker/1.0 "
            "(https://github.com/mackerel38/mackerel38-shortest-tracker)",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            if response.status != 200:
                raise TrackerError(f"HTTP {response.status}: {url}")
            payload = response.read()
    except Exception as exc:
        raise TrackerError(f"取得に失敗しました: {url}: {exc}") from exc
    finally:
        _last_request_at = time.monotonic()

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TrackerError(f"JSONの解析に失敗しました: {url}") from exc


def load_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    with STATE_PATH.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise TrackerError("state/tracker-state.json の形式が不正です")
    return data


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
    temporary.replace(path)


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


def same_user(left: str | None, right: str | None) -> bool:
    return isinstance(left, str) and isinstance(right, str) and left.lower() == right.lower()


def event_key(event_type: str, problem_id: str, submission_id: int) -> str:
    return f"{event_type}:{problem_id}:{submission_id}"


def add_event(
    state: dict[str, Any],
    *,
    event_type: str,
    problem_id: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    detected_by_reconcile: bool = False,
) -> None:
    submission_id = int(current["submission_id"])
    key = event_key(event_type, problem_id, submission_id)
    if key in state["event_keys"]:
        return

    state["event_keys"].append(key)
    state["events"].append(
        {
            "event_type": event_type,
            "problem_id": problem_id,
            "epoch_second": current.get("epoch_second"),
            "submission_id": submission_id,
            "previous_user_id": previous.get("user_id") if previous else None,
            "previous_submission_id": previous.get("submission_id") if previous else None,
            "previous_length": previous.get("length") if previous else None,
            "new_user_id": current.get("user_id"),
            "new_length": current.get("length"),
            "language": current.get("language"),
            "contest_id": current.get("contest_id"),
            "detected_by_reconcile": detected_by_reconcile,
        }
    )


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
            "target_length": snapshot.get("length"),
            "target_language": snapshot.get("language"),
            "target_epoch_second": snapshot.get("epoch_second"),
            "acquisition_count": 1,
        }
        records[problem_id] = record
        return

    record["last_acquired_epoch"] = snapshot.get("epoch_second")
    record["target_submission_id"] = snapshot.get("submission_id")
    record["target_length"] = snapshot.get("length")
    record["target_language"] = snapshot.get("language")
    record["target_epoch_second"] = snapshot.get("epoch_second")
    record["acquisition_count"] = int(record.get("acquisition_count") or 0) + 1

    if record.get("first_acquired_epoch") is None:
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
    record["target_length"] = snapshot.get("length")
    record["target_language"] = snapshot.get("language")
    record["target_epoch_second"] = snapshot.get("epoch_second")


def process_transition(
    state: dict[str, Any],
    problem_id: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    detected_by_reconcile: bool = False,
) -> None:
    previous_target = previous is not None and same_user(previous.get("user_id"), TARGET_USER)
    current_target = same_user(current.get("user_id"), TARGET_USER)

    if not previous_target and current_target:
        add_event(
            state,
            event_type="acquired",
            problem_id=problem_id,
            previous=previous,
            current=current,
            detected_by_reconcile=detected_by_reconcile,
        )
        remember_target_hold(state, problem_id, current)
    elif previous_target and not current_target:
        add_event(
            state,
            event_type="lost",
            problem_id=problem_id,
            previous=previous,
            current=current,
            detected_by_reconcile=detected_by_reconcile,
        )
    elif previous_target and current_target:
        add_event(
            state,
            event_type="improved",
            problem_id=problem_id,
            previous=previous,
            current=current,
            detected_by_reconcile=detected_by_reconcile,
        )
        update_target_best(state, problem_id, current)


def fetch_target_submissions(needed_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not needed_ids:
        return {}

    found: dict[int, dict[str, Any]] = {}
    cursor = 0

    while needed_ids - found.keys():
        query = urllib.parse.urlencode(
            {"user": TARGET_USER, "from_second": cursor}
        )
        payload = request_json(f"{USER_API}?{query}")
        if not isinstance(payload, list):
            raise TrackerError("ユーザー提出APIの形式が不正です")
        if not payload:
            break

        for submission in payload:
            if not isinstance(submission, dict):
                continue
            submission_id = submission.get("id")
            if isinstance(submission_id, int) and submission_id in needed_ids:
                found[submission_id] = submission

        if len(payload) < USER_PAGE_LIMIT:
            break

        epochs = [
            item.get("epoch_second")
            for item in payload
            if isinstance(item, dict) and isinstance(item.get("epoch_second"), int)
        ]
        if not epochs:
            break

        next_cursor = max(epochs) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    return found


def fetch_recent_submissions(from_epoch: int, to_epoch: int) -> list[dict[str, Any]]:
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


def initialize(
    merged: list[dict[str, Any]],
    now_epoch: int,
) -> dict[str, Any]:
    current: dict[str, dict[str, Any]] = {}
    needed_target_ids: set[int] = set()

    for problem in merged:
        if not isinstance(problem, dict) or not isinstance(problem.get("id"), str):
            continue

        snapshot = compact_shortest(problem)
        if snapshot is None:
            continue

        problem_id = problem["id"]
        current[problem_id] = snapshot
        if same_user(snapshot["user_id"], TARGET_USER):
            needed_target_ids.add(int(snapshot["submission_id"]))

    details = fetch_target_submissions(needed_target_ids)

    state: dict[str, Any] = {
        "version": 1,
        "target_user": TARGET_USER,
        "initialized_at": now_epoch,
        "last_checked_epoch": max(0, now_epoch - OVERLAP_SECONDS),
        "current": current,
        "ever_held": {},
        "events": [],
        "event_keys": [],
    }

    for problem_id, snapshot in current.items():
        if not same_user(snapshot["user_id"], TARGET_USER):
            continue

        detail = details.get(int(snapshot["submission_id"]))
        if detail is not None:
            snapshot.update(submission_snapshot(detail))

        remember_target_hold(state, problem_id, snapshot)
        add_event(
            state,
            event_type="baseline",
            problem_id=problem_id,
            previous=None,
            current=snapshot,
        )

    return state


def process_recent(
    state: dict[str, Any],
    submissions: list[dict[str, Any]],
    contest_starts: dict[str, int],
) -> dict[int, dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}

    for submission in submissions:
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

        previous = state["current"].get(problem_id)
        if previous is not None:
            previous_length = previous.get("length")
            if isinstance(previous_length, int) and length >= previous_length:
                continue

        current = submission_snapshot(submission)
        process_transition(state, problem_id, previous, current)
        state["current"][problem_id] = current

    return by_id


def reconcile(
    state: dict[str, Any],
    merged: list[dict[str, Any]],
    recent_by_id: dict[int, dict[str, Any]],
) -> None:
    latest_problem_ids: set[str] = set()

    for problem in merged:
        if not isinstance(problem, dict):
            continue
        problem_id = problem.get("id")
        if not isinstance(problem_id, str):
            continue

        latest_problem_ids.add(problem_id)
        authoritative = compact_shortest(problem)
        if authoritative is None:
            continue

        previous = state["current"].get(problem_id)
        if previous is not None and previous.get("submission_id") == authoritative["submission_id"]:
            authoritative["epoch_second"] = previous.get("epoch_second")
            authoritative["language"] = previous.get("language")
            state["current"][problem_id] = authoritative
            continue

        detail = recent_by_id.get(int(authoritative["submission_id"]))
        if detail is not None:
            authoritative.update(submission_snapshot(detail))

        process_transition(
            state,
            problem_id,
            previous,
            authoritative,
            detected_by_reconcile=True,
        )
        state["current"][problem_id] = authoritative

    # Problems are not normally removed, but retaining old entries is safer than
    # deleting history if the upstream dataset temporarily omits an item.


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
            "title": str(problem.get("title") or problem.get("name") or problem_id),
        }
    return result


def enrich_event(
    event: dict[str, Any],
    metadata: dict[str, dict[str, str]],
) -> dict[str, Any]:
    problem_id = str(event["problem_id"])
    info = metadata.get(problem_id, {})
    contest_id = str(event.get("contest_id") or info.get("contest_id") or "")
    return {
        **event,
        "problem_name": info.get("name") or problem_id,
        "problem_title": info.get("title") or info.get("name") or problem_id,
        "problem_index": info.get("problem_index") or "",
        "problem_url": (
            f"https://atcoder.jp/contests/{contest_id}/tasks/{problem_id}"
            if contest_id
            else ""
        ),
        "submission_url": (
            f"https://atcoder.jp/contests/{contest_id}/submissions/{event['submission_id']}"
            if contest_id and event.get("submission_id")
            else ""
        ),
    }


def build_public(
    state: dict[str, Any],
    problems: list[dict[str, Any]],
    now_epoch: int,
) -> dict[str, Any]:
    metadata = problem_metadata(problems)
    rows: list[dict[str, Any]] = []

    for problem_id, held in state["ever_held"].items():
        info = metadata.get(problem_id, {})
        current = state["current"].get(problem_id, {})
        contest_id = str(current.get("contest_id") or info.get("contest_id") or "")
        current_submission_id = current.get("submission_id")
        target_submission_id = held.get("target_submission_id")

        rows.append(
            {
                "problem_id": problem_id,
                "problem_name": info.get("name") or problem_id,
                "problem_title": info.get("title") or info.get("name") or problem_id,
                "problem_index": info.get("problem_index") or "",
                "problem_url": (
                    f"https://atcoder.jp/contests/{contest_id}/tasks/{problem_id}"
                    if contest_id
                    else ""
                ),
                "status": (
                    "holding"
                    if same_user(current.get("user_id"), TARGET_USER)
                    else "lost"
                ),
                "first_acquired_epoch": held.get("first_acquired_epoch"),
                "last_acquired_epoch": held.get("last_acquired_epoch"),
                "acquisition_count": held.get("acquisition_count"),
                "target_submission_id": target_submission_id,
                "target_submission_url": (
                    f"https://atcoder.jp/contests/{contest_id}/submissions/{target_submission_id}"
                    if contest_id and target_submission_id
                    else ""
                ),
                "target_epoch_second": held.get("target_epoch_second"),
                "target_length": held.get("target_length"),
                "target_language": held.get("target_language"),
                "current_user_id": current.get("user_id"),
                "current_submission_id": current_submission_id,
                "current_submission_url": (
                    f"https://atcoder.jp/contests/{contest_id}/submissions/{current_submission_id}"
                    if contest_id and current_submission_id
                    else ""
                ),
                "current_epoch_second": current.get("epoch_second"),
                "current_length": current.get("length"),
                "current_language": current.get("language"),
            }
        )

    rows.sort(
        key=lambda row: (
            int(row.get("last_acquired_epoch") or 0),
            str(row.get("problem_id") or ""),
        ),
        reverse=True,
    )

    losses = [
        enrich_event(event, metadata)
        for event in state["events"]
        if event.get("event_type") == "lost"
    ]
    losses.sort(
        key=lambda event: (
            int(event.get("epoch_second") or 0),
            int(event.get("submission_id") or 0),
        ),
        reverse=True,
    )

    acquisitions = [
        enrich_event(event, metadata)
        for event in state["events"]
        if event.get("event_type") in {"acquired", "baseline"}
    ]
    acquisitions.sort(
        key=lambda event: (
            int(event.get("epoch_second") or 0),
            int(event.get("submission_id") or 0),
        ),
        reverse=True,
    )

    holding_count = sum(row["status"] == "holding" for row in rows)

    return {
        "version": 1,
        "target_user": TARGET_USER,
        "generated_epoch": now_epoch,
        "generated_iso": datetime.fromtimestamp(
            now_epoch, tz=timezone.utc
        ).isoformat(),
        "summary": {
            "ever_held_count": len(rows),
            "holding_count": holding_count,
            "lost_count": len(rows) - holding_count,
            "loss_event_count": len(losses),
        },
        "problems": rows,
        "losses": losses,
        "acquisitions": acquisitions,
        "notes": [
            "初回導入時は、その時点で保持していたShortestを登録します。",
            "導入後の獲得・自己更新・奪取は提出時刻順に追跡します。",
            "導入以前にすでに奪われた履歴は、後続の過去履歴復元機能で追加予定です。",
        ],
    }


def main() -> int:
    now_epoch = int(time.time())

    merged = request_json(MERGED_URL)
    problems = request_json(PROBLEMS_URL)
    contests = request_json(CONTESTS_URL)

    if not isinstance(merged, list) or not isinstance(problems, list) or not isinstance(contests, list):
        raise TrackerError("AtCoder Problemsのデータ形式が不正です")

    state = load_state()
    initial_run = state is None
    original_state = None if state is None else json.loads(json.dumps(state))

    if state is None:
        print("初期データを作成します。")
        state = initialize(merged, now_epoch)
    else:
        from_epoch = max(
            0,
            int(state.get("last_checked_epoch") or 0) - OVERLAP_SECONDS,
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

        recent_by_id = process_recent(state, submissions, contest_starts)
        reconcile(state, merged, recent_by_id)
        state["last_checked_epoch"] = now_epoch

    # Keep event lookup compact and deterministic.
    state["event_keys"] = sorted(set(state.get("event_keys", [])))
    state["events"].sort(
        key=lambda event: (
            int(event.get("epoch_second") or 0),
            int(event.get("submission_id") or 0),
            str(event.get("event_type") or ""),
        )
    )

    comparable_state = json.loads(json.dumps(state))
    comparable_state.pop("last_checked_epoch", None)

    comparable_original = None
    if original_state is not None:
        comparable_original = json.loads(json.dumps(original_state))
        comparable_original.pop("last_checked_epoch", None)

    tracker_changed = initial_run or comparable_state != comparable_original

    # 最終確認時刻を含む内部状態は毎回保存する。
    # GitHub Actions側でキャッシュに退避し、Shortestに変化がない場合は
    # リポジトリへコミットしない。
    save_json(STATE_PATH, state)

    if not tracker_changed:
        print("Shortestの変化はありません。公開データは更新しません。")
        return 0

    public = build_public(state, problems, now_epoch)
    save_json(PUBLIC_PATH, public)

    summary = public["summary"]
    print(
        "更新完了: "
        f"保持経験 {summary['ever_held_count']} / "
        f"現在保持 {summary['holding_count']} / "
        f"奪取イベント {summary['loss_event_count']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrackerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
