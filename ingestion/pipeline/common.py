"""Small helpers shared by the extraction steps."""

import hashlib
import json
import re


def require(condition, message):
    # A missing legal paragraph should stop the build, even under python -O.
    if not condition:
        raise ValueError(message)


def compact(text):
    return re.sub(r"\s+", "", text)


def normalize_line(text):
    return re.sub(r"[ \t]+", " ", text).strip()


def join_lines(lines):
    return "\n".join(line["t"] for line in lines).strip()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data_hash(data):
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
