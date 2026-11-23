"""Paths, credentials, JSON utilities, and production prompt-cache input."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parents[1]


def api_key():
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    dotenv = ROOT / ".env"
    for line in dotenv.read_text().splitlines() if dotenv.is_file() else ():
        assignment = re.match(r"\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*)", line)
        if assignment:
            value = shlex.split(assignment[1], comments=True)
            if len(value) == 1 and value[0]:
                return value[0]
    raise ValueError("OPENAI_API_KEY is missing from the environment and .env.")


def save_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text())


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def numbered(paragraphs):
    return "\n\n".join(f"[{p['id']}] {p['text']}" for p in paragraphs)


WRITER = "gpt-6-luna"


CONTROL = "gpt-5.4-mini"


RATES = {CONTROL: (0.75, 0.075, 0.75, 4.50), WRITER: (0.10, 0.01, 0.125, 0.50)}


def digest(value):
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def segmented_input(paragraphs, cutoff, boundaries, prompt):
    parts = []
    start = 0
    for stop in [*(n for n in boundaries if n < cutoff), cutoff]:
        part = {
            "type": "input_text",
            "text": ("\n\n" if start else "") + numbered(paragraphs[start:stop]),
        }
        if stop in boundaries:
            part["prompt_cache_breakpoint"] = {"mode": "explicit"}
        parts.append(part)
        start = stop
    parts.append({"type": "input_text", "text": prompt})
    assert (
        "".join(part["text"] for part in parts)
        == numbered(paragraphs[:cutoff]) + prompt
    )
    return [{"role": "user", "content": parts}]
