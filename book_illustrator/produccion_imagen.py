"""Generate the selected illustrations and publish each completed book atomically."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import fcntl
from functools import partial
from hashlib import sha256
from itertools import zip_longest
import json
import os
from threading import Event

from book_illustrator import corte, seleccion
from book_illustrator.coleccion import LOCAL_SELECTION, PUBLIC_SELECTION, atomic_write, encode, public_catalog
from book_illustrator.imagenes import (
    IMAGE_OPTIONS, image_attempts_by_cut, journal, plan, png_sha256, previous_attempts, submit_image,
    terminal_content_failure,
)
from book_illustrator.muestras import public_sample_version
from book_illustrator.render_prompt import PUBLIC_PROMPT_VERSION, PUBLIC_STYLES, PROMPT_VERSION, make_prompt, make_public_prompt
from book_illustrator.runtime import api_key, read_json

PNG_RECEIPT = ".png.json"
PARTIAL_MIN_IMAGES = 60
PARTIAL_INTERVAL = 10


def reuse_sample(folder, sample, prompt, samples):
    cut = sample["cut"]
    path = folder / f"{cut}.png"
    src = samples / sample["image"]
    receipt = read_json(src.with_suffix(PNG_RECEIPT))
    digest = png_sha256(src.read_bytes())
    if (digest != sample["sha256"] or digest != receipt["sha256"] or
            receipt["prompt_sha256"] != sha256(prompt.encode()).hexdigest() or
            any(receipt[k] != v for k, v in IMAGE_OPTIONS.items())):
        raise ValueError(f"{src}: muestra o recibo incompatibles")
    if path.exists():
        if png_sha256(path.read_bytes()) != digest:
            raise ValueError(f"{path}: muestra modificada")
    else:
        os.link(src, path)
    entry = {**receipt, "file": path.name, "origin": "muestra", "cut": cut}
    target = path.with_suffix(PNG_RECEIPT)
    if target.exists():
        if read_json(target) != entry:
            raise ValueError(f"{target}: recibo incompatible")
    else:
        atomic_write(target, encode(entry))
        journal(folder, "reuse_sample", cut, request_id=entry["request_id"],
                usage=entry["usage"], sha256=digest, origin="muestra")


def selection_context(directory):
    result = read_json(directory / "contenido/resultados.json")
    ctx = seleccion.work_context(directory / "corpus.json")
    ctx.update(prospective=True, boundaries=corte.prefix_boundaries(ctx["paragraphs"]))
    if (result.get("reading_scope") != seleccion.PROSPECTIVE_VERSION or result["book"] != directory.name or
            result["corpus_sha256"] != sha256(ctx["snapshot"]).hexdigest()):
        raise ValueError(f"{directory.name}: se requiere una selección prospectiva del corpus actual.")
    return result, ctx


def verify_pick(ctx, pick):
    start, cutoff = seleccion.read_scope(ctx, pick["id"])
    valid, invalid = seleccion.citation_check(pick["evidence"], ctx, start, cutoff - 1, "imagen")
    if not valid or invalid or pick["page"] != ctx["pages"][cutoff - 1]:
        raise ValueError(f"{pick['id']}: evidencia de imagen inválida para el corte.")


def prompt_profile(slug, edition, *, prompt_version=None):
    if edition is not None:
        work = edition["work_id"]
        version = prompt_version or PUBLIC_PROMPT_VERSION
        return partial(make_public_prompt, work, version=version), version, PUBLIC_STYLES[work][0]
    return partial(make_prompt, slug), PROMPT_VERSION, "Graphite and gray wash"


def prepare(slug, sample, first_prompt, *, collection=LOCAL_SELECTION, edition=None):
    directory = collection / slug
    folder = directory / "imagenes-finales"
    result, ctx = selection_context(directory)
    corpus_hash = result["corpus_sha256"]
    render, version, _ = prompt_profile(slug, edition, prompt_version=sample.get("prompt_version"))
    if edition is not None and read_json(directory / "corpus.json")["source_sha256"] != edition["sha256"]:
        raise ValueError(f"{slug}: la selección tiene otra huella de fuente.")
    [variant] = [v for v in result["variants"] if v["id"] == "contenido"]
    picks = variant["picks"]
    if (not picks or sample["corpus_sha256"] != corpus_hash or picks[0]["id"] != sample["cut"] or
            sample.get("prompt_version") != version or first_prompt != render(picks[0]) or
            sample["image"] != f"color/{slug}.png" or
            len({p["id"] for p in picks}) != len(picks) or
            any(p["status"] != "aprobada" for p in picks) or
            (picks[0]["draft_call"], picks[0]["decision_call"]) !=
            (sample["draft_call"], sample["decision_call"])):
        raise ValueError(f"{slug}: la selección no coincide con la muestra aprobada")
    for pick in picks:
        verify_pick(ctx, pick)
    jobs = {p["id"] + ".png": render(p) for p in picks}
    prompts = [{"id": p["id"], "out": name, "prompt": prompt}
               for p, (name, prompt) in zip(picks, jobs.items())]
    path = folder / "prompts.jsonl"
    data = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in prompts).encode()
    if path.exists() and path.read_bytes() != data:
        raise ValueError(f"{path}: el lote ya preparado tiene otros prompts")
    revised_picks = []
    for p in picks:
        alternative = folder / "adaptaciones" / (p["id"] + ".json")
        if alternative.is_file():
            record = read_json(alternative)
            if (not record["decision"]["eligible"] or record["source_decision_call"] != p["decision_call"] or
                    record.get("prompt_version") != version or
                    record["image_prompt"] != render(record["card"])):
                raise ValueError(f"{alternative}: adaptación sin aprobación aplicable")
            initial_cut = p["id"]
            if record.get("replacement"):
                jobs.pop(initial_cut + ".png")
                p = record["replacement"]
            p = {**p, **{k: record["card"][k] for k in ("brief", "core", "title", "evidence")},
                 "version": "adapted_content", "image_adaptation": {
                     "reason": "Bloqueo de contenido durante la generación",
                     "source_cut": initial_cut,
                     "new_cut": record.get("new_cut", False),
                     "draft_call": record["draft_call"], "decision_call": record["decision"]["call"],
                     "evidence": record["card"]["evidence"]}}
            verify_pick(ctx, p)
            jobs[p["id"] + ".png"] = render(p)
        revised_picks.append(p)
    if len(jobs) != len(revised_picks):
        raise ValueError(f"{slug}: dos imágenes usarían el mismo corte.")
    sample_failure = sample.get("image_failure")
    if sample_failure is not None:
        attempts, unconfirmed = image_attempts_by_cut(collection / "muestras/color")
        if (edition is None or slug in unconfirmed or attempts.get(slug) != sample_failure or
                not terminal_content_failure(sample_failure, sha256(first_prompt.encode()).hexdigest())):
            raise ValueError(f"{slug}: falta la evidencia del rechazo definitivo de muestra.")
    else:
        reuse_sample(folder, sample, first_prompt, collection / "muestras")
    atomic_write(path, data)
    pending = plan(jobs, folder, dry_run=False)
    return folder, revised_picks, jobs, pending, corpus_hash


def verified_image(folder, pick, jobs):
    path = folder / (pick["id"] + ".png")
    rec = read_json(path.with_suffix(PNG_RECEIPT))
    if (png_sha256(path.read_bytes()) != rec["sha256"] or
            sha256(jobs[path.name].encode()).hexdigest() != rec["prompt_sha256"] or
            not rec.get("request_id") or not all(rec[k] == v for k, v in IMAGE_OPTIONS.items())):
        raise ValueError(f"{path}: no coincide con el recibo")
    image = {**{k: pick[k] for k in ("id", "page", "title", "brief", "version", "draft_call", "decision_call")},
             **{k: rec[k] for k in ("file", "sha256", "prompt_sha256", "origin")}}
    if pick.get("image_adaptation"):
        image["image_adaptation"] = pick["image_adaptation"]
    return image


def public_scene(edition, paragraph, pick, image):
    scene = {**paragraph, "title": pick["title"], "description": pick["brief"],
             "file": image["file"], "sha256": image["sha256"], "evidence": pick["evidence"],
             "prompt_sha256": image["prompt_sha256"]}
    if pick.get("content_label") or pick.get("image_adaptation"):
        scene["content_label"] = ("Scene reframed for content" if seleccion.english_metadata(edition)
                                  else seleccion.content_gate.LABEL)
    return scene


def write_public_scenes(path, edition, scenes, *, omissions=(), complete=True):
    atomic_write(path, encode({"complete": complete, "editions": [dict(
        id=edition["id"], source_sha256=edition["sha256"], scenes=scenes, omissions=omissions)]}))


def publish(slug, folder, picks, jobs, corpus_hash, *, edition=None, omissions=(), prompt_version=None):
    images = [verified_image(folder, pick, jobs) for pick in picks]
    _, version, style = prompt_profile(slug, edition, prompt_version=prompt_version)
    manifest = dict(book=slug, model=IMAGE_OPTIONS["model"], style=style, prompt_version=version,
                    corpus_sha256=corpus_hash, images=images)
    lines = [json.dumps({"id": p["id"], "out": p["id"] + ".png", "prompt": jobs[p["id"] + ".png"]},
                        ensure_ascii=False) for p in picks]
    if edition is not None:
        manifest["omissions"] = omissions
    atomic_write(folder / "prompts-finales.jsonl", ("\n".join(lines) + "\n").encode())
    atomic_write(folder / "ilustraciones.json", encode(manifest))
    if edition is not None:
        corpus = read_json(folder.parent / "corpus.json")
        paragraphs = {row["id"]: row for row in corpus["paragraphs"]}
        scenes = [public_scene(edition, paragraphs[pick["id"]], pick, image)
                  for pick, image in zip(picks, images, strict=True)]
        write_public_scenes(folder / "escenas.json", edition, scenes, omissions=omissions)
        print(f"{slug}: {len(images)} PNG con recibos, {len(omissions)} omisiones terminales.", flush=True)
    else:
        print(f"{slug}: {len(images)} PNG con recibos.", flush=True)


def public_book_status(slug, state, edition, sample):
    folder, picks, jobs, corpus_hash = state
    attempts, unconfirmed = image_attempts_by_cut(folder)
    delivered, omitted, pending = [], [], []
    for pick in picks:
        pid = pick["id"]
        name = f"{pid}.png"
        attempt = sample.get("image_failure", {}) if pid == sample["cut"] else {}
        source = "../../muestras/color/intentos.jsonl" if attempt else "intentos.jsonl"
        attempt = attempt or attempts.get(pid, {})
        prompt_sha = sha256(jobs[name].encode()).hexdigest()
        if (folder / name).is_file():
            delivered.append(pick)
        elif pid not in unconfirmed and terminal_content_failure(attempt, prompt_sha):
            omitted.append({"id": pid, "draft_call": pick["draft_call"], "decision_call": pick["decision_call"],
                            "journal": source, "attempt": attempt})
        else:
            pending.append({"id": pid, "attempt": attempt, "unconfirmed": pid in unconfirmed})
    status = {"edition": slug, "source_sha256": edition["sha256"], "expected": len(picks),
              "delivered": len(delivered), "omissions": omitted, "pending": pending,
              "status": "pendiente" if pending else "completa"}
    if not pending:
        publish(slug, folder, delivered, jobs, corpus_hash, edition=edition, omissions=omitted,
                prompt_version=sample.get("prompt_version"))
    atomic_write(folder / "estado-generacion.json", encode(status))
    return bool(pending)


class PartialScenes:
    def __init__(self, state, edition):
        self.folder, picks, self.jobs, _ = state
        self.edition = edition
        self.picks = {pick["id"]: pick for pick in picks}
        corpus = read_json(self.folder.parent / "corpus.json")
        self.paragraphs = {p["id"]: p for p in corpus["paragraphs"]}
        self.scenes = {}
        for pid in self.picks:
            if (self.folder / (pid + ".png")).is_file():
                self.add(pid)
        self.flush()

    def add(self, pid):
        pick = self.picks[pid]
        image = verified_image(self.folder, pick, self.jobs)
        self.scenes[pid] = public_scene(self.edition, self.paragraphs[pid], pick, image)

    def flush(self):
        scenes = [self.scenes[pid] for pid in self.picks if pid in self.scenes]
        write_public_scenes(self.folder / "escenas-parciales.json", self.edition, scenes, complete=False)


def execute(slugs, workers, *, collection=LOCAL_SELECTION):
    sample_folder = collection / "muestras"
    manifest = read_json(sample_folder / "manifest.json")
    catalog = public_catalog() if collection == PUBLIC_SELECTION else {}
    samples = {b["slug"]: b for b in [*manifest["books"], *manifest.get("omitted", [])]}
    first_prompts = {row["out"]: row["prompt"] for row in
                     map(json.loads, (sample_folder / "prompts.jsonl").read_text().splitlines())}
    with ExitStack() as stack:
        states = {}
        queue = []
        remaining = {}
        partials = {}
        for slug in sorted(slugs):
            folder = collection / slug / "imagenes-finales"
            folder.mkdir(exist_ok=True)
            lock = stack.enter_context((folder / ".generacion.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            sample = samples[slug]
            state = prepare(slug, sample, first_prompts[slug + ".png"], collection=collection, edition=catalog.get(slug))
            folder, picks, jobs, pending, corpus_hash = state
            attempts, unconfirmed_cuts = image_attempts_by_cut(folder)
            eligible = []
            for job in pending:
                if catalog and (job[1].stem == sample["cut"] and sample.get("image_failure") or
                                terminal_content_failure(attempts.get(job[1].stem, {}), job[-1]["prompt_sha256"])):
                    continue
                last = attempts.get(job[1].stem, {})
                if job[1].stem in unconfirmed_cuts:
                    print(f"Pendiente confirmación de respuesta: {slug}/{job[1].stem}", flush=True)
                elif (last.get("event") == "failed" and not last.get("retryable") and
                      not last.get("billing_unknown") and (
                          not (folder / "adaptaciones" / (job[1].stem + ".json")).exists() or
                          last.get("prompt_sha256") == job[-1]["prompt_sha256"])):
                    print(f"Fallo terminal registrado: {slug}/{job[1].stem}", flush=True)
                else:
                    eligible.append((slug, job))
            queue.append(eligible)
            remaining[slug] = len(eligible) if catalog else len(pending)
            states[slug] = (folder, picks, jobs, corpus_hash)
            if catalog and len(picks) >= PARTIAL_MIN_IMAGES:
                partials[slug] = PartialScenes(states[slug], catalog[slug])
            if catalog and not eligible:
                remaining[slug] = int(public_book_status(slug, states[slug], catalog[slug], sample))
            elif not pending:
                publish(slug, *states[slug])
        count = sum(map(len, queue))
        print(f"{count} solicitudes previstas, {workers} en paralelo.", flush=True)
        if count:
            from openai import OpenAI

            client = stack.enter_context(OpenAI(api_key=api_key(), max_retries=0, timeout=300))
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=workers))
            cancel = Event()
            futures = {pool.submit(submit_image, client, states[slug][0], job, cancel, min_free_bytes=250 * 1024**2): (slug, job[1].stem)
                       for row in zip_longest(*queue) for task in row if task is not None
                       for slug, job in [task]}
            successes = 0
            for future in as_completed(futures):
                slug, pid = futures[future]
                try:
                    received = future.result()
                except (OSError, TypeError, ValueError) as exc:
                    if not catalog:
                        raise
                    received = False
                    print(f"{slug}/{pid}: {exc}", flush=True)
                if received:
                    successes += 1
                    if partial := partials.get(slug):
                        partial.add(pid)
                        if len(partial.scenes) % PARTIAL_INTERVAL == 0:
                            partial.flush()
                    if successes % 20 == 0:
                        print(f"Imágenes recibidas: {successes}/{count}", flush=True)
                elif not catalog:
                    continue
                remaining[slug] -= 1
                if not remaining[slug]:
                    if catalog:
                        if partial := partials.get(slug):
                            partial.flush()
                        remaining[slug] = int(public_book_status(slug, states[slug], catalog[slug], samples[slug]))
                    else:
                        publish(slug, *states[slug])
        return int(any(remaining.values()))


def reframe(slug, pid, *, collection=LOCAL_SELECTION):
    """After an image-service rejection, audit a genuinely different visual subject."""
    directory = collection / slug
    folder = directory / "imagenes-finales"
    destination = folder / "adaptaciones" / (pid + ".json")
    if destination.is_file():
        return True
    edition = public_catalog()[slug] if collection == PUBLIC_SELECTION else None
    prompt_version = public_sample_version(collection / "muestras", slug) if edition else None
    render, prompt_version, _ = prompt_profile(slug, edition, prompt_version=prompt_version)
    result, ctx = selection_context(directory)
    [variant] = [v for v in result["variants"] if v["id"] == "contenido"]
    pick = next((p for p in variant["picks"] if p["id"] == pid), None)
    if pick is None:
        return True  # The failed frame was already a substitute; choose its next cut once.
    root = destination.parent / slug / "contenido"
    (root / "llamadas").mkdir(parents=True, exist_ok=True)
    purpose = (f"\n\nAl terminar {pid}, la edición necesita otra imagen. La composición original, "
               f"ya descartada, es: {pick['brief']}\nElige otro sujeto físico y reconocible: un espacio, "
               "un detalle arquitectónico, un objeto cotidiano o un paisaje descritos aquí. Excluye por "
               "completo los sujetos y acciones de la primera propuesta. El nuevo encuadre ha de aportar "
               "algo comprensible incluso si el lector nunca ve la ilustración anterior.")
    verify_pick(ctx, pick)
    inputs = seleccion.cut_input(ctx, pid, purpose)
    key = api_key()
    draft = seleccion.phase_call(root, key, "production_reframe", inputs, schema=seleccion.DRAFT_SCHEMA,
                                 rules=seleccion.adapt_rules(ctx), anonymous=True)
    if draft["status"] != "completed":
        return False
    card = seleccion.make_card(draft["data"], ctx, pid)
    card["id"] = pid
    if not card["brief"] or card["core"]["text"] == pick["core"]["text"]:
        atomic_write(destination.with_suffix(".rechazada.json"), encode({
            "cut": pid, "draft_call": draft["fingerprint"], "card": card,
            "reason": "Sin un motivo visual nuevo respaldado por el pasaje."}))
        print(f"{slug}/{pid}: no se obtuvo otro motivo sustentado.", flush=True)
        return False
    decision, _ = seleccion.audit_card(root, key, ctx, {"id": pid, **card}, "production_reaudit")
    record = dict(cut=pid, source_decision_call=pick["decision_call"], original_brief=pick["brief"],
                  draft_call=draft["fingerprint"], card=card, decision=decision, prompt_version=prompt_version,
                  image_prompt=render(card))
    if not decision["eligible"]:
        atomic_write(destination.with_suffix(".rechazada.json"), encode(record))
        print(f"{slug}/{pid}: ficha alternativa rechazada: {decision['checks']}", flush=True)
        return False
    atomic_write(destination, encode(record))
    print(f"{slug}/{pid}: encuadre distinto aprobado {card['title']}", flush=True)
    return True


def neighboring_cuts(slug, *, collection=LOCAL_SELECTION):
    """Use another audited cut when the original and its reframe cannot be published."""
    directory = collection / slug
    folder = directory / "imagenes-finales"
    edition = public_catalog()[slug] if collection == PUBLIC_SELECTION else None
    prompt_version = public_sample_version(collection / "muestras", slug) if edition else None
    render, prompt_version, _ = prompt_profile(slug, edition, prompt_version=prompt_version)
    attempts = previous_attempts(folder)
    result, _ = selection_context(directory)
    [variant] = [v for v in result["variants"] if v["id"] == "contenido"]
    original = variant["picks"]
    actual = list(original)
    for i, source in enumerate(original):
        record_path = folder / "adaptaciones" / (source["id"] + ".json")
        if record_path.exists():
            actual[i] = read_json(record_path).get("replacement", source)
    pool = seleccion.variant_pool(result["cards"], "contenido")
    conflicts = {frozenset(d["ids"]) for d in result["duplicates"]}
    ready = True
    for i, source in enumerate(original):
        pid = source["id"]
        record_path = folder / "adaptaciones" / (pid + ".json")
        current = actual[i]
        failure = attempts.get(current["id"], {})
        if failure.get("code") != "moderation_blocked":
            continue
        record = read_json(record_path) if record_path.exists() else None
        if record and not record.get("replacement"):
            if failure.get("prompt_sha256") != sha256(record["image_prompt"].encode()).hexdigest():
                continue  # This new, approved composition still needs its first image request.
        if not record and not record_path.with_suffix(".rechazada.json").exists():
            continue
        neighbors = actual[:i] + actual[i+1:]
        lower = actual[i-1]["page"] + 4 if i else 1
        upper = actual[i+1]["page"] - 4 if i + 1 < len(actual) else result["pages"]
        candidates = [c for c in pool if lower <= c["page"] <= upper and
                      c["id"] not in attempts and c["status"] == "aprobada" and
                      not any(c["id"] == n["id"] or frozenset([c["id"], n["id"]]) in conflicts for n in neighbors)]
        decision = None
        fresh = False
        if candidates:
            selected = min(candidates, key=lambda c: (abs(c["page"] - source["page"]),
                                                       -c["scores"]["aporte"], c["id"]))
        else:
            selected, decision = fresh_cut(slug, pid, lower, upper, attempts, original, neighbors, collection=collection)
            fresh = True
        if not selected:
            print(f"{slug}/{pid}: sin otro corte aprobado entre páginas {lower}–{upper}.", flush=True)
            ready = False
            continue
        alternate = dict(cut=pid, replacement=selected, card=selected, original_brief=source["brief"],
                         source_decision_call=source["decision_call"], draft_call=selected["draft_call"],
                         new_cut=fresh, decision=decision or {"call": selected["decision_call"],
                                                              "scores": selected["scores"], "eligible": True},
                         prompt_version=prompt_version, image_prompt=render(selected))
        if record:
            archive = record_path.with_name(f"{pid}-{current['id']}-{record['draft_call'][:10]}.anterior.json")
            atomic_write(archive, encode(record))
        atomic_write(record_path, encode(alternate))
        actual[i] = selected
        print(f"{slug}/{pid}: otro corte aprobado {selected['id']} en página {selected['page']}.", flush=True)
    return ready


def fresh_cut(slug, original_id, lower, upper, attempts, original, neighbors, *, collection=LOCAL_SELECTION):
    directory = collection / slug
    result, ctx = selection_context(directory)
    root = directory / "imagenes-finales" / "adaptaciones" / slug / "contenido"
    (root / "llamadas").mkdir(parents=True, exist_ok=True)
    excluded = (set(attempts) | {p["id"] for p in [*original, *neighbors]} |
                {b["cut"] for b in result["blocks"] if "cut" in b})
    area = [r for r in ctx["paragraphs"] if lower <= r["page"] <= upper]
    allowed = {r["id"] for r in area} - excluded
    if not allowed:
        return None, None
    key = api_key()
    block = {"id": original_id, "core_start": area[0]["id"], "core_end": area[-1]["id"],
             "context_start": area[0]["id"], "context_end": area[-1]["id"]}
    search, _ = seleccion.detector(root, key, ctx, block, allowed=allowed)
    proposals = [p for p in search["proposals"] if p["first"]["status"] == "valido"]
    if not proposals:
        return None, None
    proposal = proposals[0]
    pid = proposal["first"]["id"]
    draft, _ = seleccion.draft_card(root, key, ctx, pid, [proposal], phase="production_neighbor_draft")
    if draft["brief"]:
        decision, _ = seleccion.audit_card(root, key, ctx, draft, "production_neighbor_audit")
        if decision["eligible"] and draft["brief"] not in {n["brief"] for n in neighbors}:
            return seleccion.describe_pick(draft, draft, decision, "original"), decision
    return None, None
