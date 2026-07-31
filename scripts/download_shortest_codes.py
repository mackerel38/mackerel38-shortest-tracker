#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

DEFAULT_DELAY = 1.5
DEFAULT_MAX_ITEMS = 200


class DownloadError(RuntimeError):
    pass


class SubmissionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_source = False
        self.source_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() != "pre":
            return
        attributes = {key.casefold(): value for key, value in attrs}
        if attributes.get("id") == "submission-code":
            self.in_source = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "pre" and self.in_source:
            self.in_source = False

    def handle_data(self, data: str) -> None:
        if self.in_source:
            self.source_parts.append(data)

    @property
    def source(self) -> str | None:
        if not self.source_parts:
            return None
        return "".join(self.source_parts)


def strip_tags(fragment: str) -> str:
    return " ".join(
        html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split()
    )


def parse_language(page: str) -> str | None:
    pattern = (
        r"<th[^>]*>\s*(?:Language|言語)\s*</th>"
        r"\s*<td[^>]*>(.*?)</td>"
    )
    match = re.search(pattern, page, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    value = strip_tags(match.group(1))
    return value or None


def parse_submission(page: str) -> tuple[str, str]:
    parser = SubmissionParser()
    parser.feed(page)
    source = parser.source
    language = parse_language(page)

    if source is None:
        raise DownloadError("ソースコードが提出ページに見つかりません")
    return source, language or "Unknown"


def request_text(url: str) -> str:
    last_error: Exception | None = None

    for attempt in range(3):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "mackerel38-shortest-code-downloader/1.0 "
                    "(personal archive tool)"
                ),
                "Accept": "text/html,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                if response.status != 200:
                    raise DownloadError(
                        f"HTTP {response.status}: {url}"
                    )
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)

    raise DownloadError(f"提出ページを取得できません: {last_error}")


def extension_for_language(language: str) -> str | None:
    normalized = language.strip().casefold()

    mappings = [
        (("c++",), "cpp"),
        (("c#",), "cs"),
        (("objective-c++",), "mm"),
        (("objective-c",), "m"),
        (("python", "pypy"), "py"),
        (("javascript", "node.js"), "js"),
        (("typescript", "deno"), "ts"),
        (("kotlin",), "kt"),
        (("java",), "java"),
        (("rust",), "rs"),
        (("golang", "go "), "go"),
        (("ruby",), "rb"),
        (("perl",), "pl"),
        (("php",), "php"),
        (("swift",), "swift"),
        (("haskell",), "hs"),
        (("ocaml",), "ml"),
        (("f#",), "fs"),
        (("visual basic",), "vb"),
        (("scala",), "scala"),
        (("dart",), "dart"),
        (("lua",), "lua"),
        (("julia",), "jl"),
        (("nim",), "nim"),
        (("crystal",), "cr"),
        (("zig",), "zig"),
        (("d ", "dmd", "ldc"), "d"),
        (("raku",), "raku"),
        (("scheme",), "scm"),
        (("common lisp",), "lisp"),
        (("clojure",), "clj"),
        (("erlang",), "erl"),
        (("elixir",), "ex"),
        (("fortran",), "f90"),
        (("cobol",), "cob"),
        (("pascal",), "pas"),
        (("prolog",), "pl"),
        (("bash",), "sh"),
        (("zsh",), "zsh"),
        (("awk",), "awk"),
        (("sed",), "sed"),
        (("octave",), "m"),
        (("matlab",), "m"),
        (("r ", "gnu r"), "r"),
        (("apl",), "apl"),
        (("a言語",), "a"),
        (("clay",), "clay"),
        (("dc",), "dc"),
        (("uiua",), "ua"),
        (("brainfuck",), "bf"),
        (("nibbles",), "nbl"),
        (("text",), "txt"),
    ]

    for prefixes, extension in mappings:
        if any(
            normalized == prefix.strip()
            or normalized.startswith(prefix)
            for prefix in prefixes
        ):
            return extension

    if normalized == "c" or normalized.startswith("c ("):
        return "c"
    return None


def safe_component(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.+-]+", "_", value).strip("._")
    return cleaned[:80] or fallback


def filename_for(
    problem_id: str,
    language: str,
    used: set[str],
) -> str:
    safe_problem = safe_component(problem_id, fallback="problem")
    extension = extension_for_language(language)

    if extension is not None:
        candidate = f"{safe_problem}.{extension}"
    else:
        language_name = safe_component(
            language.split("(", 1)[0],
            fallback="unknown",
        )
        candidate = f"{safe_problem}_{language_name}.txt"

    if candidate not in used:
        used.add(candidate)
        return candidate

    stem = Path(candidate).stem
    suffix = Path(candidate).suffix
    index = 2
    while f"{stem}_{index}{suffix}" in used:
        index += 1
    candidate = f"{stem}_{index}{suffix}"
    used.add(candidate)
    return candidate


def load_selection(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"選択ファイルを読めません: {exc}") from exc

    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise DownloadError("選択ファイルに items 配列がありません")

    result: list[dict[str, Any]] = []
    seen: set[int] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        problem_id = item.get("problem_id")
        submission_id = item.get("submission_id")
        submission_url = item.get("submission_url")
        user_id = item.get("user_id")
        expected_length = item.get("length")

        if (
            not isinstance(problem_id, str)
            or not isinstance(submission_id, int)
            or not isinstance(submission_url, str)
            or not submission_url.startswith("https://atcoder.jp/")
            or submission_id in seen
        ):
            continue

        seen.add(submission_id)
        result.append(
            {
                "problem_id": problem_id,
                "submission_id": submission_id,
                "submission_url": submission_url,
                "user_id": user_id if isinstance(user_id, str) else "",
                "expected_length": (
                    expected_length
                    if isinstance(expected_length, int)
                    else None
                ),
            }
        )

    if not result:
        raise DownloadError("有効な提出が選択されていません")
    return result


def default_output_path() -> Path:
    directory = Path.home() / "shortest-code-downloads"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return directory / f"shortest-codes-{stamp}.zip"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="選択したAtCoder ShortestコードをZIPにまとめます。"
    )
    parser.add_argument("selection", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_ITEMS,
    )
    args = parser.parse_args()

    items = load_selection(args.selection)
    if len(items) > args.max_items:
        raise DownloadError(
            f"{len(items)}件が選択されています。"
            f"1回の上限は{args.max_items}件です。"
            "複数回に分けてください。"
        )

    output = (args.output or default_output_path()).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    used_names: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="shortest-codes-") as tmp:
        directory = Path(tmp)
        code_directory = directory / "codes"
        code_directory.mkdir()

        for index, item in enumerate(items, start=1):
            if index > 1:
                time.sleep(max(0.0, args.delay))

            problem_id = item["problem_id"]
            submission_id = item["submission_id"]
            print(f"[{index}/{len(items)}] {problem_id} / {submission_id}")

            try:
                page = request_text(item["submission_url"])
                source, language = parse_submission(page)
                filename = filename_for(problem_id, language, used_names)
                (code_directory / filename).write_text(
                    source,
                    encoding="utf-8",
                    newline="",
                )
                manifest.append(
                    {
                        **item,
                        "language": language,
                        "filename": f"codes/{filename}",
                        "actual_characters": len(source),
                    }
                )
            except Exception as exc:
                message = str(exc)
                failures.append({**item, "error": message})
                print(f"  ERROR: {message}", file=sys.stderr)

        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().isoformat(),
                    "success_count": len(manifest),
                    "failure_count": len(failures),
                    "items": manifest,
                    "failures": failures,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        with (directory / "manifest.csv").open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "problem_id",
                    "submission_id",
                    "user_id",
                    "language",
                    "expected_length",
                    "actual_characters",
                    "filename",
                    "submission_url",
                ],
            )
            writer.writeheader()
            writer.writerows(manifest)

        if failures:
            with (directory / "failures.txt").open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as file:
                for failure in failures:
                    file.write(
                        f"{failure['problem_id']}\t"
                        f"{failure['submission_id']}\t"
                        f"{failure['submission_url']}\t"
                        f"{failure['error']}\n"
                    )

        temporary_zip = output.with_suffix(output.suffix + ".tmp")
        if temporary_zip.exists():
            temporary_zip.unlink()

        with zipfile.ZipFile(
            temporary_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(directory))

        temporary_zip.replace(output)

    print()
    print(f"成功: {len(manifest)}件")
    print(f"失敗: {len(failures)}件")
    print(f"出力: {output}")
    return 0 if manifest else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DownloadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
