"""Render the first selected scene of each book and its e-ink proof."""

import fcntl
from hashlib import sha256
import json

from book_illustrator.coleccion import LOCAL_SELECTION, PUBLIC_SELECTION, atomic_write, encode, public_catalog
from book_illustrator.epub import eink_jpeg
from book_illustrator.imagenes import IMAGE_OPTIONS, image_attempts_by_cut, png_sha256, run, terminal_content_failure
from book_illustrator.render_prompt import (
    DEFAULT_STYLE, PROMPT_VERSION, PUBLIC_COMPOSITION_VERSION, PUBLIC_PROMPT_VERSION,
    PUBLIC_STYLES, make_prompt, make_public_prompt,
)
from book_illustrator.seleccion import PROSPECTIVE_VERSION, citation_check
from book_illustrator.runtime import read_json

# These art directions carry over after the reader approves the samples.
def public_sample_version(output, slug):
    pins = output / "versiones.json"
    bound = read_json(pins) if pins.is_file() else {}
    manifest = output / "manifest.json"
    durable = read_json(manifest) if manifest.is_file() else {}
    receipt = next((row for row in [*durable.get("books", []), *durable.get("omitted", [])]
                    if row["slug"] == slug), None)
    prior, unconfirmed = image_attempts_by_cut(output / "color")
    finished_path = output / "color" / f"{slug}.png.json"
    old = (receipt["prompt_version"] if receipt else
           PUBLIC_PROMPT_VERSION if slug in prior or slug in unconfirmed or finished_path.exists() else None)
    pinned = bound.get(slug)
    if receipt and pinned and old != pinned:
        raise ValueError(f"{slug}: el manifiesto y la versión reservada difieren.")
    return pinned or old or PUBLIC_COMPOSITION_VERSION


def sample_info(slug, number, *, collection=LOCAL_SELECTION, edition=None, prompt_version=None):
    folder = collection / slug
    result = read_json(folder / "contenido/resultados.json")
    corpus_path = folder / "corpus.json"
    if result["book"] != slug or result["corpus_sha256"] != sha256(corpus_path.read_bytes()).hexdigest():
        raise ValueError(f"{slug}: la selección pertenece a otro corpus.")
    paragraphs = read_json(corpus_path)["paragraphs"]
    positions = {r["id"]: i for i, r in enumerate(paragraphs)}
    [variant] = [v for v in result["variants"] if v["id"] == "contenido"]
    if not variant["picks"]:
        raise ValueError(f"{slug}: no hay fichas aprobadas para una muestra.")
    pick = min(variant["picks"], key=lambda p: positions[p["id"]])
    if pick["status"] != "aprobada" or paragraphs[positions[pick["id"]]]["page"] != pick["page"]:
        raise ValueError(f"{slug}: la primera ficha no coincide con el corte aprobado.")
    if result.get("reading_scope") != PROSPECTIVE_VERSION:
        raise ValueError(f"{slug}: se requiere una selección prospectiva.")
    valid, invalid = citation_check(pick["evidence"], {"position": positions, "paragraphs": paragraphs},
                                    0, positions[pick["id"]], "imagen")
    if not valid or invalid:
        raise ValueError(f"{slug}: la muestra tiene evidencia inválida para el corte.")
    corpus = read_json(corpus_path)
    if edition is not None:
        if corpus["source_sha256"] != edition["sha256"]:
            raise ValueError(f"{slug}: fuente distinta de la edición admitida.")
        title, author = corpus["title"], corpus["author"]
        style, art = PUBLIC_STYLES[edition["work_id"]]
        version = prompt_version or PUBLIC_PROMPT_VERSION
        prompt = make_public_prompt(edition["work_id"], pick, version=version)
    else:
        title, author, style = corpus["title"], corpus["author"], "Graphite and gray wash"
        art, version, prompt = DEFAULT_STYLE, PROMPT_VERSION, make_prompt(slug, pick)
    return {
        "number": number, "slug": slug, "title": title, "author": author, "style": style,
        "art_direction": art, "prompt_version": version,
        "title_scene": pick["title"], "brief": pick["brief"], "framing": "", "framing_sources": [],
        "cut": pick["id"], "page": pick["page"], "picks": len(variant["picks"]), "pages": result["pages"],
        "draft_call": pick["draft_call"], "decision_call": pick["decision_call"],
        "version": pick["version"], "corpus_sha256": result["corpus_sha256"],
        "image": f"color/{slug}.png", "eink_image": f"boox/{slug}.jpg",
    }, prompt


def build(slugs, *, collection=LOCAL_SELECTION, books=()):
    selected = set(slugs) if slugs else set(books)
    if unknown := selected.difference(books):
        raise ValueError(f"Fuera del lote activo: {', '.join(sorted(unknown))}")
    output = collection / "muestras"
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".muestras.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        publish(selected, collection=collection, books=books, output=output)


def publish(selected, *, collection, books, output):
    entries = {}
    jobs = {}
    catalog = public_catalog() if collection == PUBLIC_SELECTION else {}
    versions_path = output / "versiones.json"
    versions = read_json(versions_path) if versions_path.is_file() else {}
    for number, slug in enumerate(books, 1):
        if slug in selected:
            if catalog:
                versions[slug] = public_sample_version(output, slug)
            entry, prompt = sample_info(slug, number, collection=collection, edition=catalog.get(slug),
                                        prompt_version=versions.get(slug))
            entries[slug] = entry
            jobs[f"{slug}.png"] = prompt
    if catalog:
        atomic_write(versions_path, encode(versions))
    failed = run(jobs, output / "color", omit_terminal=bool(catalog))
    (output / "boox").mkdir(exist_ok=True)
    verified = {}
    omitted = {}
    attempts, unconfirmed = image_attempts_by_cut(output / "color") if catalog else ({}, set())
    for slug, entry in entries.items():
        original = output / entry["image"]
        if not original.exists():
            attempt = attempts.get(slug, {})
            prompt_digest = sha256(jobs[original.name].encode()).hexdigest()
            if slug not in unconfirmed and terminal_content_failure(attempt, prompt_digest):
                omitted[slug] = {**entry, "prompt_sha256": prompt_digest, "image_failure": attempt}
            continue
        receipt = read_json(original.with_suffix(".png.json"))
        data = original.read_bytes()
        if png_sha256(data) != receipt["sha256"]:
            raise ValueError(f"{slug}: el PNG no coincide con el recibo.")
        atomic_write(output / entry["eink_image"], eink_jpeg(data))
        entry.update(sha256=receipt["sha256"], prompt_sha256=receipt["prompt_sha256"])
        verified[slug] = entry

    # Partial batches publish only verified images. Rebuilding never drops an earlier book.
    manifest_path = output / "manifest.json"
    existing = read_json(manifest_path) if manifest_path.exists() else {}
    published = {r["slug"]: r for r in existing.get("books", [])}
    unavailable = {r["slug"]: r for r in existing.get("omitted", [])}
    published.update(verified)
    unavailable.update(omitted)
    for slug in verified:
        unavailable.pop(slug, None)
    prompts_path = output / "prompts.jsonl"
    prompts = {r["out"]: r["prompt"] for r in map(json.loads, prompts_path.read_text().splitlines())} if prompts_path.exists() else {}
    prompts.update({f"{s}.png": jobs[f"{s}.png"] for s in verified.keys() | omitted.keys()})
    rows = [json.dumps({"out": name, "prompt": prompts[name]}, ensure_ascii=False)
            for slug in books if (name := f"{slug}.png") in prompts]
    atomic_write(prompts_path, ("\n".join(rows) + "\n").encode())
    manifest = {"image_options": IMAGE_OPTIONS, "books": [published[s] for s in books if s in published]}
    if catalog:
        manifest["omitted"] = [unavailable[s] for s in books if s in unavailable]
    atomic_write(manifest_path, encode(manifest))
    if failed and selected - (verified.keys() | omitted.keys()):
        raise SystemExit("Se publicaron las muestras verificadas. Revisa las solicitudes fallidas antes de reintentar.")
