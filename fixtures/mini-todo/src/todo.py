#!/usr/bin/env python3
import json
import sys
from pathlib import Path

STORE = Path("todo.json")


def load() -> list[dict]:
    if STORE.exists():
        return json.loads(STORE.read_text())
    return []


def save(tasks: list[dict]) -> None:
    STORE.write_text(json.dumps(tasks, indent=2))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd, *rest = argv
    tasks = load()
    if cmd == "add":
        text = " ".join(rest)
        if not text:
            print("usage: add <text>", file=sys.stderr)
            return 1
        tasks.append({"id": len(tasks) + 1, "text": text, "done": False})
        save(tasks)
        print(f"added task {len(tasks)}")
    elif cmd == "list":
        for t in tasks:
            mark = "x" if t["done"] else " "
            print(f"[{mark}] {t['id']}. {t['text']}")
    elif cmd == "done":
        tid = int(rest[0])
        for t in tasks:
            if t["id"] == tid:
                t["done"] = True
        save(tasks)
    else:
        print(f"unknown command {cmd!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
