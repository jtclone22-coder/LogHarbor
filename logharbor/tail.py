"""Follow log files like `tail -f`: several files, folders and globs; survives rotation and truncation."""
from __future__ import annotations

import glob
import os
import threading
from pathlib import Path

from .parse import is_continuation, parse_line
from .store import Store

PATTERNS = ("*.log", "*.jsonl", "*.ndjson", "*.txt", "*.out", "*.err")


def expand(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            for pattern in PATTERNS:
                files += sorted(path.glob(pattern))
        elif any(ch in target for ch in "*?["):
            files += [Path(p) for p in sorted(glob.glob(target))]
        else:
            files.append(path)
    return list(dict.fromkeys(files))


class Follower:
    """Polls files for new complete lines. from_start=False skips what was already there."""

    def __init__(self, store: Store, targets: list[str], from_start: bool = False, poll: float = 0.5):
        self.store, self.targets, self.from_start, self.poll = store, targets, from_start, poll
        self.offsets: dict[Path, int] = {}
        self.partial: dict[Path, str] = {}
        self.stop = threading.Event()

    def _source(self, path: Path) -> str:
        return path.name

    def step(self) -> int:
        """Read whatever is new in every file once. Returns the number of records added."""
        added = 0
        for path in expand(self.targets):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if path not in self.offsets:
                self.offsets[path] = 0 if self.from_start else size
            offset = self.offsets[path]
            if size < offset:  # truncated or rotated in place: start over
                offset = 0
                self.partial.pop(path, None)
            if size == offset:
                continue
            try:
                with open(path, "rb") as handle:
                    handle.seek(offset)
                    data = handle.read(min(size - offset, 8 << 20))
            except OSError:
                continue  # locked by its writer or removed just now: try again on the next poll
            self.offsets[path] = offset + len(data)
            text = self.partial.pop(path, "") + data.decode("utf-8", errors="replace")
            lines = text.split("\n")
            if not text.endswith("\n"):
                self.partial[path] = lines.pop()  # incomplete last line: wait for the rest
            records = []
            source = self._source(path)
            for line in lines:
                if not line.strip():
                    continue
                if is_continuation(line):
                    if records:
                        records[-1]["message"] += "\n" + line.rstrip("\r")
                    else:  # the record it belongs to was stored in an earlier read
                        self.store.append_to_last(source, line.rstrip("\r"))
                    continue
                records.append(parse_line(line, source))
            added += self.store.add_logs(records)
        return added

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.step()
            except Exception:  # one bad moment (a file, a record) must never stop following every file
                pass
            self.stop.wait(self.poll)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="logharbor-tail", daemon=True)
        thread.start()
        return thread
