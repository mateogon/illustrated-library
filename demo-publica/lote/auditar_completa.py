"""Review completed catalog image manifests as PNGs become available."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import fcntl
import json
import subprocess
import sys
import time

from book_illustrator.coleccion import ROOT, PUBLIC_SELECTION, atomic_write, encode, public_catalog

OUT = ROOT / "outputs/demo-publica/completa"


def read(path):
    return json.loads(path.read_bytes())


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


def validate(slug, raw):
    [source] = read(raw)["editions"]
    approved_file = raw.parent / "escenas-auditadas.json"
    approved_books = read(approved_file)["editions"]
    decisions = read(raw.parent / "revision-visual.json")
    if source["id"] != slug or any(r["edition"] != slug for r in decisions):
        raise ValueError(f"Revisión de otra edición en {slug}")
    original = [scene["id"] for scene in source["scenes"]]
    if len(set(original)) != len(original) or [r["cut"] for r in decisions] != original:
        raise ValueError(f"Revisión incompleta en {slug}")
    passed = [r["cut"] for r in decisions if r["status"] == "approved"]
    if not passed or any(row["status"] not in {"approved", "withheld"} for row in decisions):
        raise ValueError(f"Falta un dictamen final o todas las ilustraciones fueron retenidas en {slug}")
    [approved] = approved_books
    if (approved["id"] != slug or approved["source_sha256"] != source["source_sha256"]
            or [r["id"] for r in approved["scenes"]] != passed):
        raise ValueError(f"El conjunto aprobado difiere de los dictámenes de {slug}")
    return dict(id=slug, manifest=str(approved_file), sha256=fingerprint(approved_file),
                raw_sha256=fingerprint(raw), generated=len(original), approved=len(passed))


def run_book(slug, path):
    directory = OUT / "logs"
    directory.mkdir(exist_ok=True, parents=True)
    with (directory / f"audit-{slug}.log").open("a") as log:
        subprocess.run([sys.executable, "-Bu", "-m", "book_illustrator", "audit", str(path)],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    result = validate(slug, path)
    atomic_write(OUT / "auditadas" / f"{slug}.json", encode(result))
    print(f"{slug}: {result['approved']}/{result['generated']} aprobadas", flush=True)


def main():
    parser = argparse.ArgumentParser(prog=globals().get("CLI_PROGRAM"), description=__doc__)
    parser.add_argument("--wait", "--esperar", dest="esperar", action="store_true", help="Wait for editions still generating images.")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True, parents=True)
    with (OUT / ".auditoria.lock").open("a") as lock, ThreadPoolExecutor(max_workers=3) as pool:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        remaining = set(public_catalog())
        active = {}
        errors = {}
        while remaining or active:
            for slug in sorted(remaining.copy()):
                path = PUBLIC_SELECTION / slug / "imagenes-finales/escenas.json"
                if not path.is_file():
                    continue
                marker = OUT / "auditadas" / f"{slug}.json"
                remaining.remove(slug)
                if marker.is_file() and read(marker) == validate(slug, path):
                    continue
                active[slug] = pool.submit(run_book, slug, path)
            for slug, job in active.copy().items():
                if job.done():
                    del active[slug]
                    if exc := job.exception():
                        errors[slug] = str(exc)
                        print(f"{slug}: ver log: {exc}", flush=True)
            if not active and (not remaining or not args.esperar):
                break
            time.sleep(10)
        if errors:
            raise SystemExit(f"Revisiones pendientes: {', '.join(sorted(errors))}")


if __name__ == "__main__":
    main()
