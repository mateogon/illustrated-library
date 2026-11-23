"""Generate PNG illustrations from the project's audited prompts."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
from threading import Event, Lock
import time
from uuid import uuid4

from book_illustrator.api import ambiguous_http_status

from book_illustrator.runtime import api_key, read_json, save_json, utc_now


IMAGE_MODEL = "gpt-image-2.6-sunburst"
IMAGE_SIZE = "1200x800"
IMAGE_QUALITY = "medium"
CONCURRENCY = 3
IMAGE_OPTIONS = {
    "model": IMAGE_MODEL, "size": IMAGE_SIZE, "quality": IMAGE_QUALITY,
    "output_format": "png", "n": 1,
}
IMAGE_DIMENSIONS = tuple(map(int, IMAGE_SIZE.split("x")))
print_lock = Lock()


def load_jobs(source):
    jobs = {}
    names = set()
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{source}:{number}: se esperaba un objeto JSON.")
        name, prompt = row.get("out"), row.get("prompt")
        if (not isinstance(name, str) or not name.isprintable()
                or Path(name).name != name or not name.endswith(".png")):
            raise ValueError(f"{source}:{number}: out debe ser el nombre de un archivo PNG.")
        if name.casefold() in names or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{source}:{number}: salida duplicada o prompt vacío.")
        names.add(name.casefold())
        jobs[name] = prompt
    if not jobs:
        raise ValueError(f"{source}: el lote está vacío.")
    return jobs


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def png_sha256(data):
    if (len(data) < 45 or data[:16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
            or struct.unpack(">II", data[16:24]) != IMAGE_DIMENSIONS
            or data[-12:] != b"\0\0\0\0IEND\xaeB`\x82"):
        raise ValueError(f"la imagen recibida debe ser un PNG de {IMAGE_SIZE}.")
    return sha256(data)


def plan(jobs, folder, dry_run):
    pending = []
    for name, prompt in jobs.items():
        path = folder / name
        receipt = path.with_suffix(".png.json")
        staged = path.with_suffix(".png.pending")
        expected = {**IMAGE_OPTIONS, "file": name, "prompt_sha256": sha256(prompt.encode("utf-8"))}
        if any(file.is_symlink() for file in (path, receipt, staged)):
            raise ValueError(f"{path}: la salida o su recibo es un enlace simbólico.")
        if path.exists() or receipt.exists() or staged.exists():
            data = read_json(receipt) if receipt.is_file() else {}
            image = path if path.is_file() else staged
            if (not all(data.get(key) == value for key, value in expected.items())
                    or not image.is_file() or png_sha256(image.read_bytes()) != data.get("sha256")):
                raise ValueError(f"{path}: salida existente sin recibo compatible y verificable.")
            if image == staged and not dry_run:
                staged.rename(path)
            print(f"Verificado {path}", flush=True)
            continue
        pending.append((prompt, path, receipt, staged, expected))
    return pending


def journal(folder, event, cut, **fields):
    data = {"event": event, "cut": cut, "at": utc_now(), **fields}
    with (folder / "intentos.jsonl").open("a", encoding="utf-8") as out:
        fcntl.flock(out, fcntl.LOCK_EX)
        out.write(json.dumps(data, ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())


def image_attempt_events(folder):
    path = folder / "intentos.jsonl"
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            lines = stream.read().splitlines()
        for line in lines:
            row = json.loads(line)
            if "attempt_id" in row:
                yield row


def previous_attempts(folder):
    return {row["cut"]: row for row in image_attempt_events(folder)}


def image_attempts_by_cut(folder):
    latest, attempts = {}, {}
    for row in image_attempt_events(folder):
        latest[row["cut"]] = row
        attempts[row["attempt_id"]] = row
    unconfirmed = {row["cut"] for row in attempts.values() if image_attempt_unconfirmed(row)}
    return latest, unconfirmed


def image_attempt_unconfirmed(row):
    if row["event"] in {"started", "received"}:
        return True
    return row["event"] == "failed" and (row.get("billing_unknown") or
                                         ambiguous_http_status(row.get("http_status")))


def terminal_content_failure(row, prompt_sha256):
    return (row.get("event") == "failed" and row.get("http_status") in {400, 422} and
            row.get("code") in {"moderation_blocked", "content_policy_violation"} and
            not row.get("billing_unknown") and row.get("prompt_sha256") == prompt_sha256)


def store_response(path, receipt_path, staged, image, receipt):
    # Keep a paid response in memory during a transient disk shortage.
    staged_payload = False
    for attempt in range(30):
        try:
            if not receipt_path.is_file():
                save_json(receipt_path, receipt)
            if not staged_payload:
                staged.write_bytes(image)
                staged_payload = True
            staged.replace(path)
            return
        except OSError as exc:
            if exc.errno != errno.ENOSPC or attempt == 29:
                raise
            time.sleep(10)


def submit_image(client, folder, job, cancel, *, min_free_bytes=0, max_attempts=3):
    from openai import APIError

    prompt, path, receipt_path, staged, expected = job
    cut = path.stem
    for number in range(max_attempts):
        if cancel.is_set():
            return False
        if min_free_bytes and shutil.disk_usage(folder).free < min_free_bytes:
            cancel.set()
            raise OSError("Se requieren 250 MiB de espacio libre para completar las entregas en curso.")
        attempt_id = uuid4().hex
        journal(folder, "started", cut, attempt_id=attempt_id, prompt_sha256=expected["prompt_sha256"])
        received = {}
        try:
            response = client.images.generate(prompt=prompt, **IMAGE_OPTIONS)
            received = {"request_id": response._request_id,
                        "usage": response.usage.model_dump(exclude_none=True) if response.usage else None}
            journal(folder, "received", cut, attempt_id=attempt_id, **received)
            [item] = response.data
            image = base64.b64decode(item.b64_json, validate=True)
            receipt = {**expected, **received, "origin": "nueva", "cut": cut, "attempt_id": attempt_id,
                       "sha256": png_sha256(image), "created_at": utc_now(), "endpoint": "/v1/images/generations"}
            store_response(path, receipt_path, staged, image, receipt)
            journal(folder, "completed", cut, attempt_id=attempt_id, request_id=receipt["request_id"],
                    usage=receipt["usage"], sha256=receipt["sha256"], origin="nueva")
            return True
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            code = getattr(exc, "code", None)
            request_id = getattr(exc, "request_id", None)
            body = getattr(exc, "body", None)
            usage = body.get("usage") if isinstance(body, dict) else None
            retry = status == 429 and code == "rate_limit_exceeded" and not usage
            retry_wait = [15, 45, 90][number]
            if getattr(exc, "response", None) is not None:
                header = exc.response.headers.get("retry-after", "")
                if header.isdigit():
                    retry_wait = max(retry_wait, min(300, int(header)))
            journal(folder, "failed", cut, attempt_id=attempt_id, request_id=request_id,
                    error_type=type(exc).__name__, http_status=status, code=code,
                    prompt_sha256=expected["prompt_sha256"],
                    retryable=retry, billing_unknown=ambiguous_http_status(status) and not usage, usage=usage)
            with print_lock:
                print(f"{folder.parent.name}/{cut}: HTTP {status} {type(exc).__name__} {code}", flush=True)
            if status in {401, 403} or code == "insufficient_quota":
                cancel.set()
            if not retry or number == max_attempts - 1 or cancel.wait(retry_wait):
                return False
        except Exception as exc:
            journal(folder, "failed", cut, attempt_id=attempt_id, error_type=type(exc).__name__,
                    errno=getattr(exc, "errno", None), code=None, http_status=None,
                    billing_unknown=not bool(received.get("usage")), **received)
            cancel.set()
            raise


def run(jobs, folder, *, omit_terminal=False):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".generacion.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = plan(jobs, folder, dry_run=False)
        if not pending:
            return 0
        latest, unconfirmed = image_attempts_by_cut(folder)
        unconfirmed = {cut.casefold() for cut in unconfirmed}
        eligible = []
        for job in pending:
            path = job[1]
            if path.stem.casefold() in unconfirmed:
                print(f"Pendiente confirmación de respuesta: {path}", flush=True)
            elif omit_terminal and terminal_content_failure(latest.get(path.stem, {}), job[-1]["prompt_sha256"]):
                print(f"Rechazo definitivo de contenido: {path}", flush=True)
            else:
                eligible.append(job)
        if not eligible:
            return 1

        from openai import OpenAI

        success = len(eligible) == len(pending)
        cancel = Event()
        with OpenAI(api_key=api_key(), max_retries=0, timeout=300) as client, ThreadPoolExecutor(CONCURRENCY) as pool:
            tasks = {pool.submit(submit_image, client, folder, job, cancel): job[1] for job in eligible}
            for task in as_completed(tasks):
                try:
                    if task.result():
                        print(f"Generado {tasks[task]}", flush=True)
                    else:
                        success = False
                except (OSError, TypeError, ValueError) as exc:
                    success = False
                    print(f"{tasks[task]}: {exc}", flush=True)
        return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        jobs = load_jobs(args.input)
        if args.dry_run:
            pending = plan(jobs, args.out_dir, dry_run=True)
            print(f"{IMAGE_MODEL}, {IMAGE_SIZE}, {IMAGE_QUALITY}, {CONCURRENCY} simultáneas")
            for _, path, _, _, _ in pending:
                print(f"Pendiente {path}")
            return 0
        return run(jobs, args.out_dir)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
