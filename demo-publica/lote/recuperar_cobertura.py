"""Fill selected gaps using previously approved scene descriptions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from difflib import SequenceMatcher
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import re
import signal
from threading import Event, Lock

from auditar_completa import OUT, read, validate
from book_illustrator import auditoria_visual as visual, imagenes, seleccion
from book_illustrator.coleccion import PUBLIC_SELECTION, atomic_write, encode, public_catalog, public_source
from book_illustrator.costos_coleccion import priced_image
from book_illustrator.muestras import public_sample_version
from book_illustrator.produccion_imagen import public_scene, selection_context, verified_image, verify_pick
from book_illustrator.render_prompt import make_public_prompt
from book_illustrator.pacing import MAX_GAP, MIN_GAP


RECOVERY = OUT / "recuperacion"


def digest(data):
    return sha256(data).hexdigest()


def safe_file(folder, relative):
    path = folder / relative
    if Path(relative).is_absolute() or path.resolve() != path or not path.is_file():
        raise ValueError(f"Archivo fuera de su ruta admitida: {path}")
    return path


def link(source, destination, expected):
    if destination.resolve() != destination:
        raise ValueError(f"Ruta de destino no canónica: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        os.link(source, destination)
    if digest(destination.read_bytes()) != expected:
        raise ValueError(f"Los bytes enlazados difieren: {destination}")


def long_gaps(scenes, pages):
    ordered = sorted(scenes, key=lambda s: (s["page"], s["id"]))
    bounds = [(None, 1), *((s["id"], s["page"]) for s in ordered), (None, pages)]
    return [
        {"left": left, "right": right, "first": a, "last": b,
         "pages": b - a if left or right else pages}
        for (left, a), (right, b) in zip(bounds, bounds[1:])
        if (b - a if left or right else pages) > MAX_GAP
    ]


def source_intervals(paragraph):
    return {(p["source_xhtml"], tuple(p["xhtml_path"]), p["text_start"], p["text_end"])
            for p in paragraph.get("source_parts", [paragraph])}


def overlaps(left, right):
    return any((doc, path) == (other_doc, other_path) and start < stop_other and start_other < stop
               for doc, path, start, stop in left for other_doc, other_path, start_other, stop_other in right)


def footprint(pick, paragraphs):
    support = {e["id"] for e in pick["evidence"]} | {pick["id"]}
    intervals = set().union(*(source_intervals(paragraphs[pid]) for pid in support))
    return ((seleccion.normalized(pick["core"]["text"]), seleccion.normalized(pick["brief"])),
            set(pick["source_proposals"]), support, intervals)


def conflicts_with(candidate, other, blacklist, footprints):
    a, b = candidate["id"], other["id"]
    if a == b or frozenset((a, b)) in blacklist:
        return True
    text, proposals, support, intervals = footprints[a]
    other_text, other_proposals, other_support, other_intervals = footprints[b]
    if proposals & other_proposals or support & other_support or overlaps(intervals, other_intervals):
        return True
    if candidate["chapter"] == other["chapter"] and abs(candidate["page"] - other["page"]) <= 2:
        meaningful = {"with", "from", "into", "that", "while", "their", "sobre", "desde", "entre", "ante",
                      "esta", "este", "ella", "unas", "unos", "está", "como"}
        left = {w for w in text[0].split() if len(w) >= 4 and w not in meaningful}
        right = {w for w in other_text[0].split() if len(w) >= 4 and w not in meaningful}
        if len(left & right) >= max(2, (min(len(left), len(right)) + 1) // 2):
            return True
    return any(x == y or (min(len(x), len(y)) >= 20 and
                          SequenceMatcher(None, x, y, autojunk=False).ratio() >= 0.76)
               for x, y in zip(text, other_text))


def choose(result, paragraphs, existing, base_picks, excluded, limit):
    [variant] = [v for v in result["variants"] if v["id"] == "contenido"]
    pool = seleccion.variant_pool(result["cards"], variant["id"])
    footprints = {p["id"]: footprint(p, paragraphs) for p in [*pool, *base_picks]}
    blacklist = {frozenset(row["ids"]) for row in result["duplicates"]}
    references = list(base_picks)
    trials = []
    ordered = sorted(long_gaps(existing, result["pages"]), key=lambda g: (-g["pages"], g["first"]))
    for gap in ordered:
        a, b = gap["first"], gap["last"]
        lower = a + MIN_GAP if gap["left"] else a
        upper = b - MIN_GAP if gap["right"] else b
        candidates = [
            p for p in pool if p["id"] not in excluded and p["status"] == "aprobada"
            and max(a + 1, lower) <= p["page"] <= min(b - 1, upper)
            and not any(abs(p["page"] - q["page"]) < MIN_GAP for q in existing)
            and not any(conflicts_with(p, q, blacklist, footprints) for q in references)
        ]
        if not candidates or len(references) - len(base_picks) >= limit:
            trials.append({**gap, "cut": None, "reason": "limite" if candidates else "sin_ficha_distinta_elegible"})
            continue
        candidate = min(candidates, key=lambda p: (
            max(p["page"] - a, b - p["page"]), -p["scores"]["aporte"], -p["value"], p["id"]))
        references.append(candidate)
        trials.append({**gap, "cut": candidate["id"], "page": candidate["page"], "pick": candidate})
    return trials


def cited_decision(directory, ctx, pick):
    if re.fullmatch(r"P[0-9]{4,}", pick["id"]) is None:
        raise ValueError("Identificador de corte fuera del formato del corpus.")
    verify_pick(ctx, pick)
    root = directory / "contenido"
    selected = root / "llamadas"
    def receipt(fingerprint):
        if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError(f"{pick['id']}: huella de recibo inválida.")
        return read(safe_file(selected, fingerprint + ".json"))

    draft, decision = receipt(pick["draft_call"]), receipt(pick["decision_call"])
    if (draft["fingerprint"] != pick["draft_call"] or draft["endpoint"] != "responses" or
            draft["status"] != "completed" or decision["endpoint"] != "decisions" or
            decision["status"] != "completed" or decision["model_requested"] != seleccion.WRITER):
        raise ValueError(f"{pick['id']}: faltan recibos finales de la ficha.")
    recomputed, cached = seleccion.audit_card(root, None, ctx, pick, decision["phase"])
    if cached != decision or recomputed["call"] != pick["decision_call"] or not recomputed["eligible"]:
        raise ValueError(f"{pick['id']}: Decisions no aprueba los datos exactos de esta ficha.")


def editorial_overlaps(slug, chosen_ids):
    path = RECOVERY / "equivalencias.json"
    if not path.is_file():
        return []
    rows = [row for row in read(path)["equivalencias"] if row["edition"] == slug]
    if len({r["cut"] for r in rows}) != len(rows) or any(
        r["base_cut"] not in chosen_ids or r["cut"] in chosen_ids or not r["reason"].strip() for r in rows
    ):
        raise ValueError(f"{slug}: equivalencia editorial incompatible con la selección base.")
    return sorted(rows, key=lambda row: row["cut"])


def vetoes(slug, bases):
    path = RECOVERY / "vetos.json"
    if not path.exists():
        return []
    rows = [v for v in read(path)["vetos"] if v["edition"] == slug]
    by_cut = {s["id"]: s["sha256"] for s in bases}
    if len({v["cut"] for v in rows}) != len(rows) or any(
        by_cut.get(v["cut"]) != v["sha256"] or not v["reason"].strip() or not v["evidence"].strip()
        for v in rows
    ):
        raise ValueError(f"{slug}: veto de otro PNG, duplicado o sin justificación.")
    return sorted(rows, key=lambda v: v["cut"])


def independent_review(slug, base, current_vetoes):
    path = Path("revision-lectura") / (slug + ".json")
    if not (OUT / path).exists():
        return None
    marker = safe_file(OUT, path)
    row = read(marker)
    if (row["id"] != slug or row["estado"] not in {"revisada", "veto"} or
            not row["cuts"] or not set(row["cuts"]) <= {s["id"] for s in base} or
            not set(row["vetos"]) <= {v["cut"] for v in current_vetoes}):
        raise ValueError(f"{slug}: revisión independiente incompatible con la base o los vetos.")
    return digest(marker.read_bytes())


def review_files(folder, slug, source_hash, scene, row):
    path = safe_file(folder, scene["file"])
    review = safe_file(folder, row["review"])
    expected = visual.binding(scene, slug, source_hash, path)
    if review != folder / "auditoria-visual" / expected / "dictamen.json":
        raise ValueError(f"{slug}/{scene['id']}: ruta del dictamen incompatible.")
    record = read(review)
    if (record["binding"] != expected or record["edition"] != slug or record["cut"] != scene["id"] or
            record["status"] != row["status"]):
        raise ValueError(f"{slug}/{scene['id']}: dictamen de otra escena.")
    accepted = visual.accepted(record["decision"]) and record["quote_found"] is True
    if accepted != (row["status"] == "approved"):
        raise ValueError(f"{slug}/{scene['id']}: estado del dictamen incompatible.")
    stages = record["receipts"]
    if set(stages) not in ({"blind", "review"}, {"blind", "review", "citation"}):
        raise ValueError(f"{slug}/{scene['id']}: faltan recibos del dictamen.")
    files = [(review, digest(review.read_bytes())), (path, scene["sha256"])]
    for name, sha in stages.items():
        stage = safe_file(folder, review.with_name(name + ".json").relative_to(folder))
        if digest(stage.read_bytes()) != sha or read(stage)["status"] != "completed":
            raise ValueError(f"{slug}/{scene['id']}: recibo visual modificado.")
        files.append((stage, sha))
    if accepted:
        visual.require_approved({**scene, "visual_review": row["review"]}, slug, source_hash, path, folder)
    return files


def load_base(edition):
    slug = edition["id"]
    directory = PUBLIC_SELECTION / slug
    folder = directory / "imagenes-finales"
    marker = read(safe_file(OUT, Path("auditadas") / (slug + ".json")))
    raw = safe_file(folder, "escenas.json")
    if marker != validate(slug, raw) or Path(marker["manifest"]) != safe_file(folder, "escenas-auditadas.json"):
        raise ValueError(f"{slug}: marcador base no válido.")
    [all_scenes] = read(raw)["editions"]
    [approved] = read(Path(marker["manifest"]))["editions"]
    if all_scenes["source_sha256"] != edition["sha256"]:
        raise ValueError(f"{slug}: fuente fuera del catálogo.")
    rows = read(safe_file(folder, "revision-visual.json"))
    for scene, row in zip(all_scenes["scenes"], rows, strict=True):
        review_files(folder, slug, edition["sha256"], scene, row)
    return marker, directory, all_scenes, approved, rows


def plan_for(edition, marker, directory, raw, approved, reviewed):
    slug = edition["id"]
    result, ctx = selection_context(directory)
    if read(directory / "corpus.json")["source_sha256"] != edition["sha256"]:
        raise ValueError(f"{slug}: corpus de otra fuente.")
    paragraphs = {r["id"]: r for r in ctx["paragraphs"]}
    [variant] = [v for v in result["variants"] if v["id"] == "contenido"]
    base_picks = variant["picks"]
    known = {pick["id"] for pick in base_picks}
    rendered = {scene["id"] for scene in raw["scenes"]}
    omitted = {item["id"] for item in raw["omissions"]}
    if known != rendered | omitted:
        raise ValueError(f"{slug}: los resultados de generación no cubren la selección de base.")
    keep = [s for s in approved["scenes"] if s["id"] not in {v["cut"] for v in reviewed}]
    version = public_sample_version(PUBLIC_SELECTION / "muestras", slug)
    if version != read(directory / "imagenes-finales/ilustraciones.json")["prompt_version"]:
        raise ValueError(f"{slug}: la versión del prompt no coincide con la base.")
    limit = (marker["generated"] + 3) // 4
    base_prompts = {s["prompt_sha256"] for s in raw["scenes"]}
    base_prompts.update(o["attempt"]["prompt_sha256"] for o in raw["omissions"])
    exclusions = editorial_overlaps(slug, known)
    trials = choose(result, paragraphs, keep, base_picks, known | {r["cut"] for r in exclusions}, limit)
    for trial in trials:
        if "pick" in trial:
            pick = trial["pick"]
            cited_decision(directory, ctx, pick)
            trial["prompt_sha256"] = digest(make_public_prompt(edition["work_id"], pick, version=version).encode())
            if trial["prompt_sha256"] in base_prompts:
                del trial["pick"]
                trial.update(cut=None, reason="prompt_ya_probado")
            else:
                base_prompts.add(trial["prompt_sha256"])
    plan = {"id": slug, "base_sha256": marker["sha256"], "raw_sha256": marker["raw_sha256"],
            "selection_sha256": digest((directory / "contenido/resultados.json").read_bytes()),
            "corpus_sha256": result["corpus_sha256"], "pages": result["pages"], "limit": limit,
            "prompt_version": version, "initial_vetoes": reviewed, "trials": trials}
    if exclusions:
        plan["editorial_overlaps"] = exclusions
    return plan, paragraphs


def render_trial(folder, edition, version, trial, paragraph, client):
    pick, pid = trial["pick"], trial["cut"]
    name = pid + ".png"
    prompt = make_public_prompt(edition["work_id"], pick, version=version)
    jobs = {name: prompt}
    if digest(prompt.encode()) != trial["prompt_sha256"]:
        raise ValueError(f"{pid}: cambió el prompt fijado.")
    attempt = imagenes.previous_attempts(folder).get(pid)
    try:
        pending = imagenes.plan(jobs, folder, dry_run=False)
    except ValueError:
        if attempt and not (folder / name).exists():
            return None, None, {"status": "render_sin_png_conciliable", "attempt": attempt}
        raise
    if pending:
        if attempt is None:
            try:
                imagenes.submit_image(client(), folder, pending[0], Event(), max_attempts=1)
            except (OSError, TypeError, ValueError):
                if not imagenes.previous_attempts(folder).get(pid):
                    raise
            attempt = imagenes.previous_attempts(folder).get(pid)
        if not (folder / name).is_file():
            return None, None, {"status": "render_sin_png_conciliable", "attempt": attempt}
    image = verified_image(folder, pick, jobs)
    return image, public_scene(edition, paragraph, pick, image), None


def audit_trial(folder, edition, source, scene, client):
    pid = scene["id"]
    bound = visual.binding(scene, edition["id"], edition["sha256"], safe_file(folder, scene["file"]))
    relative = f"auditoria-visual/{bound}/dictamen.json"
    target = folder / relative
    if target.exists():
        status = read(safe_file(folder, relative))["status"]
    else:
        try:
            entry = {"id": edition["id"], "source_sha256": edition["sha256"]}
            _, status = visual.review_scene(client(), folder, entry, source, scene)
        except Exception as exc:
            stages = [read(p) for p in target.parent.glob("*.json") if p.stem in {"blind", "review", "citation"}]
            if not any(row["status"] in {"unconfirmed", "rate_limited"} for row in stages):
                raise
            return None, {"status": "auditoria_sin_dictamen", "error_type": type(exc).__name__,
                          "visual_receipts": str(target.parent.relative_to(folder))}
    summary = {"edition": edition["id"], "cut": pid, "status": status, "review": relative}
    review_files(folder, edition["id"], edition["sha256"], scene, summary)
    return summary, None


def run_trial(folder, edition, source, plan, trial, paragraphs, client):
    row = {k: v for k, v in trial.items() if k != "pick"}
    pid = row["cut"]
    if pid is None:
        return {**row, "status": "sin_solicitud"}, None, None
    _, scene, failure = render_trial(folder, edition, plan["prompt_version"], trial, paragraphs[pid], client)
    if failure:
        return {**row, **failure}, None, None
    row["generated"] = True
    review, failure = audit_trial(folder, edition, source, scene, client)
    if failure:
        return {**row, **failure}, None, None
    row.update(status=review["status"], review=review["review"],
               visual_receipts=str(Path(review["review"]).parent))
    passed = {**scene, "visual_review": review["review"]} if review["status"] == "approved" else None
    return row, passed, review


def costs(folder, outcomes):
    measured = {"render": Decimal(0), "visual": Decimal(0)}
    uncertain = {"render": 0, "visual": 0}
    attempts = {}
    for row in imagenes.image_attempt_events(folder):
        attempt = attempts.setdefault(row["attempt_id"], {})
        attempt.update({key: value for key, value in row.items() if value is not None})
    for row in attempts.values():
        if row.get("usage"):
            measured["render"] += priced_image({**row, "model": imagenes.IMAGE_MODEL})
        elif row["event"] in {"started", "received", "completed"} or imagenes.image_attempt_unconfirmed(row):
            uncertain["render"] += 1
    audit_count, seen = 0, set()
    folders = {row["visual_receipts"] for row in outcomes if "visual_receipts" in row}
    for relative in sorted(folders):
        for rank in ("blind", "review", "citation"):
            path = Path(relative) / (rank + ".json")
            if (folder / path).is_file():
                row = read(safe_file(folder, path))
                identity = row.get("request_id") or row["fingerprint"]
                if identity in seen:
                    continue
                seen.add(identity)
                audit_count += len(row.get("rate_limit_events", [])) + (row["status"] != "rate_limited")
                if row.get("usage"):
                    measured["visual"] += seleccion.priced_text_attempt(row)
                elif row["status"] != "rate_limited":
                    uncertain["visual"] += 1
    return {"render_attempts": len(attempts), "visual_requests": audit_count,
            "usd_render": str(measured["render"]), "usd_visual": str(measured["visual"]),
            "usd_measured": str(sum(measured.values())), "without_usage": uncertain}


def close(folder, edition, base, plan, outcomes, extra, reviews, review_sha):
    marker, directory, raw, approved, base_reviews = base
    slug = edition["id"]
    final_vetoes = vetoes(slug, approved["scenes"])
    final_base = [s for s in approved["scenes"] if s["id"] not in {v["cut"] for v in final_vetoes}]
    final_scenes = sorted([*final_base, *extra], key=lambda s: (s["page"], s["id"]))
    if any(right["page"] - left["page"] < MIN_GAP for left, right in zip(final_scenes, final_scenes[1:])):
        raise ValueError(f"{slug}: el manifiesto combinado tiene cortes demasiado próximos.")
    extra_generated = sum(o.get("generated", False) for o in outcomes)
    needs_export = bool(extra or final_vetoes)
    manifest = Path(marker["manifest"])
    if needs_export:
        for scene, row in zip(raw["scenes"], base_reviews, strict=True):
            source_folder = directory / "imagenes-finales"
            files = review_files(source_folder, slug, edition["sha256"], scene, row)
            receipt = safe_file(source_folder, Path(scene["file"]).with_suffix(".png.json"))
            if read(receipt)["sha256"] != scene["sha256"]:
                raise ValueError(f"{slug}/{scene['id']}: el recibo no corresponde al PNG de base.")
            files.append((receipt, digest(receipt.read_bytes())))
            for source, sha in files:
                link(source, folder / source.relative_to(source_folder), sha)
        manifest = folder / "escenas-auditadas.json"
        entry = {**approved, "scenes": final_scenes, "editorial_vetoes": final_vetoes}
        atomic_write(manifest, encode({"complete": True, "editions": [entry]}))
    atomic_write(folder / "revision-visual.json", encode([*base_reviews, *reviews] if needs_export else reviews))
    accounting = costs(folder, outcomes)
    if accounting["render_attempts"] > plan["limit"]:
        raise ValueError(f"{slug}: demasiados intentos para el plan.")
    result = {**marker, "manifest": str(manifest), "sha256": digest(manifest.read_bytes()),
              "source": "recovered" if needs_export else "base", "base_sha256": marker["sha256"],
              "independent_review_sha256": review_sha,
              "base_withheld": len(final_vetoes), "necesita_exportacion": needs_export,
              "generated": marker["generated"] + extra_generated, "approved": len(final_scenes),
              "extra_generated": extra_generated, "extra_approved": len(extra), "extra_limit": plan["limit"],
              "vetos": final_vetoes, "trials": outcomes, "costs": accounting,
              "gaps_before": long_gaps(final_base, plan["pages"]), "gaps_after": long_gaps(final_scenes, plan["pages"])}
    atomic_write(folder / "cierre.json", encode(result))
    return result


def update_plan(folder, before, current):
    if before == current:
        return
    unchanged = set(before) | set(current)
    unchanged -= {"trials", "editorial_overlaps"}
    original = {t["cut"]: t for t in before["trials"] if t["cut"]}
    proposed = {t["cut"]: t for t in current["trials"] if t["cut"]}
    attempts = imagenes.previous_attempts(folder)
    original_overlaps = before.get("editorial_overlaps", [])
    current_overlaps = current.get("editorial_overlaps", [])
    if ((folder / "cierre.json").exists() or original_overlaps == current_overlaps or
            any(before.get(key) != current.get(key) for key in unchanged) or
            any(original.get(cut) is None or original[cut] != proposed.get(cut) for cut in attempts)):
        raise ValueError(f"{current['id']}: cambiaron fuentes o un corte ya solicitado.")
    archive = folder / ("plan-" + digest(encode(before))[:12] + ".json")
    atomic_write(archive, encode(before))
    atomic_write(folder / "plan.json", encode(current))


def recover_one(edition, *, dry_run=False):
    from contextlib import ExitStack

    slug = edition["id"]
    marker_path = OUT / "auditadas" / (slug + ".json")
    if not marker_path.exists():
        return False
    base = load_base(edition)
    marker, directory, raw, approved, _ = base
    reviewed = vetoes(slug, approved["scenes"])
    folder = RECOVERY / slug
    if dry_run:
        plan, _ = plan_for(edition, marker, directory, raw, approved, reviewed)
        print(slug, [(t["pages"], t["cut"], t.get("reason")) for t in plan["trials"]], flush=True)
        return True
    if folder.resolve() != folder:
        raise ValueError(f"Ruta de recuperación no canónica: {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock, ExitStack() as stack:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        review_sha = independent_review(slug, approved["scenes"], reviewed)
        plan_path = folder / "plan.json"
        if plan_path.is_file():
            before = read(safe_file(folder, "plan.json"))
            plan, paragraphs = plan_for(edition, marker, directory, raw, approved, before["initial_vetoes"])
            update_plan(folder, before, plan)
        else:
            if any(imagenes.image_attempt_events(folder)):
                raise ValueError(f"{slug}: hay solicitudes sin plan verificable.")
            plan, paragraphs = plan_for(edition, marker, directory, raw, approved, reviewed)
            atomic_write(plan_path, encode(plan))
        done = folder / "cierre.json"
        if done.is_file():
            previous = read(safe_file(folder, "cierre.json"))
            if previous["vetos"] == reviewed and previous["independent_review_sha256"] == review_sha:
                if (digest(Path(previous["manifest"]).read_bytes()) != previous["sha256"] or
                        previous["base_sha256"] != marker["sha256"]):
                    raise ValueError(f"{slug}: cambió un manifiesto del cierre.")
                return True

        client_cache = {}
        client_lock = Lock()
        def client():
            with client_lock:
                if "instance" not in client_cache:
                    from openai import OpenAI
                    client_cache["instance"] = stack.enter_context(
                        OpenAI(api_key=visual.api_key(), max_retries=0, timeout=300))
            return client_cache["instance"]

        source = public_source(edition)
        candidates = sum(trial["cut"] is not None for trial in plan["trials"])
        workers = 4 if candidates > 12 else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            processed = list(pool.map(lambda trial: run_trial(folder, edition, source, plan, trial, paragraphs, client),
                                      plan["trials"]))
        outcomes = [row for row, _, _ in processed]
        extra = [passed for _, passed, _ in processed if passed is not None]
        reviews = [review for _, _, review in processed if review is not None]
        atomic_write(folder / "avance.json", encode({"id": slug, "trials": outcomes, "costs": costs(folder, outcomes)}))
        if review_sha is None:
            return False
        result = close(folder, edition, base, plan, outcomes, extra, reviews, review_sha)
        print(f"{slug}: {result['extra_approved']}/{result['extra_generated']} extras, "
              f"{len(result['gaps_before'])} → {len(result['gaps_after'])} huecos", flush=True)
        return True


def main():
    parser = argparse.ArgumentParser(prog=globals().get("CLI_PROGRAM"), description=__doc__)
    parser.add_argument("ediciones", nargs="*", metavar="EDITION")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--wait", "--esperar", dest="esperar", action="store_true", help="Wait for the selected editions to complete audit and reading review.")
    action.add_argument("--plan", action="store_true", help="Print the plan from available markers without API calls.")
    args = parser.parse_args()
    catalog = public_catalog()
    remaining = set(args.ediciones or catalog)
    if remaining - catalog.keys():
        parser.error("Edición fuera del catálogo.")
    stopping = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    errors = {}
    while remaining and not stopping.is_set():
        ready = [s for s in sorted(remaining) if (OUT / "auditadas" / (s + ".json")).is_file()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = {s: pool.submit(recover_one, catalog[s], dry_run=args.plan) for s in ready}
            for slug, task in running.items():
                try:
                    if task.result():
                        remaining.remove(slug)
                except Exception as exc:
                    errors[slug] = f"{type(exc).__name__}: {exc}"
                    remaining.remove(slug)
                    print(f"{slug}: {errors[slug]}", flush=True)
        if remaining and args.esperar:
            stopping.wait(15)
        else:
            break
    if errors:
        raise SystemExit(f"Recuperación pendiente en {', '.join(sorted(errors))}")


if __name__ == "__main__":
    main()
