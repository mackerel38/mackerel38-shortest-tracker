#!/usr/bin/env python3
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

MERGED_URL = "https://kenkoooo.com/atcoder/resources/merged-problems.json"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "docs/data/all-shortest.json"


class UpdateError(RuntimeError):
    pass


def request_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "mackerel38-shortest-tracker/2.1 "
                "(https://github.com/mackerel38/"
                "mackerel38-shortest-tracker)"
            ),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            if response.status != 200:
                raise UpdateError(f"HTTP {response.status}: {url}")
            return json.loads(response.read())
    except Exception as exc:
        raise UpdateError(f"全Shortest一覧の取得に失敗しました: {exc}") from exc


def load_old() -> dict[str, Any] | None:
    if not OUTPUT_PATH.exists():
        return None
    try:
        with OUTPUT_PATH.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_json(value: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, ensure_ascii=False, separators=(",", ":"))
        file.write("\n")
    temporary.replace(OUTPUT_PATH)


def build_rows(merged: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for problem in merged:
        if not isinstance(problem, dict):
            continue

        problem_id = problem.get("id")
        contest_id = problem.get("contest_id")
        shortest_contest_id = problem.get("shortest_contest_id")
        submission_id = problem.get("shortest_submission_id")
        user_id = problem.get("shortest_user_id")
        length = problem.get("source_code_length")

        if (
            not isinstance(problem_id, str)
            or not isinstance(contest_id, str)
            or not isinstance(shortest_contest_id, str)
            or not isinstance(submission_id, int)
            or not isinstance(user_id, str)
            or not isinstance(length, int)
        ):
            continue

        name = str(problem.get("name") or problem_id)
        title = str(problem.get("title") or name)

        rows.append(
            {
                "problem_id": problem_id,
                "contest_id": contest_id,
                "problem_name": name,
                "problem_title": title,
                "problem_url": (
                    f"https://atcoder.jp/contests/{contest_id}"
                    f"/tasks/{problem_id}"
                ),
                "submission_id": submission_id,
                "submission_contest_id": shortest_contest_id,
                "submission_url": (
                    f"https://atcoder.jp/contests/{shortest_contest_id}"
                    f"/submissions/{submission_id}"
                ),
                "user_id": user_id,
                "length": length,
            }
        )

    rows.sort(key=lambda row: row["problem_id"])
    return rows


def main() -> int:
    payload = request_json(MERGED_URL)
    if not isinstance(payload, list):
        raise UpdateError("merged-problems.json の形式が不正です")

    rows = build_rows(payload)
    old = load_old()
    if isinstance(old, dict) and old.get("problems") == rows:
        print(f"全Shortest一覧に変化はありません: {len(rows)} 問")
        return 0

    output = {
        "version": 1,
        "generated_epoch": int(time.time()),
        "count": len(rows),
        "problems": rows,
    }
    save_json(output)
    print(f"全Shortest一覧を更新しました: {len(rows)} 問")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
