"""Export Color and BOOX EPUBs from checked sources and approved scenes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import re
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from PIL import Image, ImageDraw

from book_illustrator.coleccion import ROOT, atomic_write, encode
from book_illustrator.coleccion_epub import elements, package_editions, read_xhtml, text_nodes
from book_illustrator.epub import OPF, XHTML, package_navigation, read_package, resolve
from book_illustrator.imagenes import IMAGE_DIMENSIONS, png_sha256, sha256


CATALOG = ROOT / "demo-publica/catalogo.json"
PUBLIC_OUT = ROOT / "outputs/demo-publica"
COMPLETE_OUT = PUBLIC_OUT / "completa"


def fixture_row(source, cut, internal):
    def paragraphs(node, path=()):
        if node.namespaceURI == XHTML and node.localName == "p":
            yield list(path), node
        for index, child in enumerate(elements(node)):
            yield from paragraphs(child, (*path, index))

    with ZipFile(source) as book:
        opf, package = read_package(book)
        [spine] = package.getElementsByTagNameNS(OPF, "spine")
        reading = {node.getAttribute("idref") for node in elements(spine) if node.getAttribute("linear") != "no"}
        nav, ncx = package_navigation(opf, package)
        for item in package.getElementsByTagNameNS(OPF, "item"):
            chapter = resolve(opf, item.getAttribute("href"))[0]
            if item.getAttribute("id") not in reading or chapter in {nav, ncx}:
                continue
            if item.getAttribute("media-type") != "application/xhtml+xml":
                continue
            [body] = read_xhtml(book.read(chapter)).getElementsByTagNameNS(XHTML, "body")
            for locator, node in paragraphs(body):
                prose = " ".join("".join(part for _, part in text_nodes(node)).split())
                if len(prose.split()) < 12:
                    continue
                if node.parentNode.localName not in {"body", "section", "div", "blockquote"}:
                    continue
                end = list(re.finditer(r"\S+", prose))[8].end() if internal else len(prose)
                return dict(id=cut, source_xhtml=chapter, xhtml_path=locator, xhtml_tag="p",
                            text_start=0, text_end=end, xhtml_text_length=len(prose), text=prose[:end])
    raise ValueError(f"{source.name}: no hay un párrafo para la prueba.")


def fixtures(catalog, folder):
    entries = []
    for index, item in enumerate(catalog.values(), 1):
        row = fixture_row(ROOT / item["path"], f"P{index:04d}", internal=index % 2 == 0)
        path = folder / (row["id"] + ".png")
        image = Image.new("RGB", IMAGE_DIMENSIONS, "#ebd6af")
        draw = ImageDraw.Draw(image)
        draw.rectangle((45, 45, 1155, 755), fill="#355975")
        draw.polygon([(45, 710), (210 + index * 11, 245), (620, 540), (805, 322), (1155, 695),
                      (1155, 755), (45, 755)], fill="#5c786a")
        draw.ellipse((727 - index * 4, 105, 892 - index * 4, 270), fill="#e4b664")
        english = item["language"] == "en"
        title, description = ("Test image", "Geometric fixture drawn locally.")
        if not english:
            title, description = ("Imagen de prueba", "Figura geométrica dibujada localmente.")
        draw.text((80, 75), title + " " + row["id"], fill="#ffffff")
        image.save(path, format="PNG")
        scene = dict(row, title=title, description=description, file=path.name,
                     sha256=png_sha256(path.read_bytes()))
        entries.append(dict(id=item["id"], source_sha256=item["sha256"], scenes=[scene]))
    return entries


def source_info(path):
    with ZipFile(path) as source:
        opf, package = read_package(source)
        nav, ncx = package_navigation(opf, package)
        count = sum(item.getAttribute("media-type").startswith("image/")
                    for item in package.getElementsByTagNameNS(OPF, "item"))
        return dict(version=package.documentElement.getAttribute("version"), xhtml_nav=bool(nav),
                    ncx=bool(ncx), original_images=count)


def stage_editions(catalog, entries, base, stage, *, fixture, single_edition=False):
    if not fixture:
        from book_illustrator.auditoria_visual import require_approved

    base = base.resolve()
    seen = set()
    for entry in entries:
        edition_id = entry["id"]
        item = catalog[edition_id]
        if edition_id in seen or item["sha256"] != entry["source_sha256"]:
            raise ValueError(f"{edition_id}: edición duplicada o huella fuera del catálogo.")
        seen.add(edition_id)
        source = ROOT / item["path"]
        book = dict(source=source, source_sha256=item["sha256"], language=item["language"],
                    title=item["title"], slug=edition_id)
        details = []
        scenes = []
        for scene in entry["scenes"]:
            png = (base / scene["file"]).resolve()
            if not png.is_relative_to(base) or png.suffix.lower() != ".png":
                raise ValueError(f"{edition_id}: ruta PNG fuera del lote.")
            if not all(isinstance(scene.get(key), str) and scene[key].strip() for key in ("title", "description")):
                raise ValueError(f"{edition_id}: faltan los metadatos de una escena.")
            note = {key: scene[key] for key in ("title", "description", "file", "sha256")}
            if "content_label" in scene:
                note["content_label"] = scene["content_label"]
            detail = {key: scene[key] for key in ("id", "source_xhtml", "xhtml_path", "sha256",
                                                   "text_start", "text_end", "xhtml_text_length")}
            detail["internal_cut"] = scene["text_end"] < scene["xhtml_text_length"]
            if not fixture:
                audit = require_approved(scene, edition_id, item["sha256"], png, base)
                if len(audit["profile_jpeg_sha256"]) != 2:
                    raise ValueError(f"{edition_id}: faltan los dos perfiles en el dictamen visual.")
                hashes = dict(zip(("Color", "BOOX"), audit["profile_jpeg_sha256"], strict=True))
                note["profile_jpeg_sha256"] = hashes
                detail["visual_review"] = scene["visual_review"]
                detail["profile_jpeg_sha256"] = hashes
            details.append(detail)
            scenes.append((scene, note))
        result = package_editions(book, scenes, base, stage if single_edition else stage / edition_id)
        context = dict(id=edition_id, language=item["language"], source_sha256=item["sha256"],
                       source=source_info(source), scenes=details)
        for profile, path in zip(("Color", "BOOX"), result, strict=True):
            yield path, dict(context, profile=profile, path=str(path.relative_to(stage)),
                             bytes=path.stat().st_size, sha256=sha256(path.read_bytes()))


def run(catalog, destination, command, *, manifest_path=None, edition_id=None, manifest_sha256=None):
    fixture = manifest_path is None
    if fixture and edition_id:
        raise ValueError("Una entrega por edición requiere un manifiesto auditado.")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f"{destination}: el destino para un nuevo lote debe estar vacío.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output(command + ["--version"], text=True).strip()
    with TemporaryDirectory(prefix=".exportar-demo-", dir=destination.parent) as work:
        workspace = Path(work)
        stage = workspace / "epubs"
        if fixture:
            base = workspace / "fixtures"
            base.mkdir()
            entries = fixtures(catalog, base)
        else:
            base = manifest_path.resolve().parent
            manifest_bytes = manifest_path.read_bytes()
            if manifest_sha256 and sha256(manifest_bytes) != manifest_sha256:
                raise ValueError("El manifiesto difiere del marcador final.")
            entries = json.loads(manifest_bytes)["editions"]
        if not entries:
            raise ValueError("No hay ediciones con escenas para exportar.")
        if edition_id and [entry["id"] for entry in entries] != [edition_id]:
            raise ValueError(f"El manifiesto final debe corresponder solo a {edition_id}.")
        staged = list(stage_editions(catalog, entries, base, stage, fixture=fixture,
                                    single_edition=edition_id is not None))

        def check(staged_edition):
            path, record = staged_edition
            result = subprocess.run(command + ["--quiet", "--failonwarnings", str(path)],
                                    capture_output=True, text=True)
            if result.returncode:
                raise ValueError(f"{record['path']}\n{result.stdout}{result.stderr}")
            if sha256(path.read_bytes()) != record["sha256"]:
                raise ValueError(f"{record['path']}: cambió durante la validación.")
            return record

        with ThreadPoolExecutor(max_workers=3) as pool:
            records = list(pool.map(check, staged))
        for edition_id in {row["id"] for row in records}:
            item = catalog[edition_id]
            if sha256((ROOT / item["path"]).read_bytes()) != item["sha256"]:
                raise ValueError(f"{edition_id}: la fuente cambió durante la validación.")
        report = dict(mode="fixtures" if fixture else "audited_scenes", validator=version,
                      fail_on_warnings=True, images_requested=0, api_cost_usd=0,
                      catalog_sha256=sha256(CATALOG.read_bytes()), passed=records)
        if not fixture:
            if manifest_path.read_bytes() != manifest_bytes:
                raise ValueError("El manifiesto de escenas cambió durante la validación.")
            report["scene_manifest_sha256"] = sha256(manifest_bytes)
        atomic_write(stage / "validacion-exportacion.json", encode(report))
        stage.replace(destination)
        verify_delivery(destination, catalog, report)
        print(f"{len(records)} EPUB verificados en {destination}", flush=True)
        return report


def verify_delivery(destination, catalog, report):
    if json.loads((destination / "validacion-exportacion.json").read_bytes()) != report:
        raise ValueError(f"{destination}: cambió el informe instalado.")
    paths = {str(path.relative_to(destination)) for path in destination.rglob("*.epub")}
    if paths != {row["path"] for row in report["passed"]}:
        raise ValueError(f"{destination}: los EPUB instalados difieren del informe.")
    for row in report["passed"]:
        payload = (destination / row["path"]).read_bytes()
        if len(payload) != row["bytes"] or sha256(payload) != row["sha256"]:
            raise ValueError(f"{row['path']}: cambió el EPUB instalado.")
    for item_id in {row["id"] for row in report["passed"]}:
        item = catalog[item_id]
        declared = {row["source_sha256"] for row in report["passed"] if row["id"] == item_id}
        if declared != {item["sha256"]} or sha256((ROOT / item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError(f"{item_id}: cambió la fuente admitida.")


def marked_entry(marker, catalog, manifest):
    edition_id = marker["id"]
    named = Path(marker["manifest"])
    if not named.is_absolute() or named.resolve() != manifest.resolve():
        raise ValueError(f"{edition_id}: el marcador apunta a otro manifiesto.")
    raw = manifest.read_bytes()
    if sha256(raw) != marker["sha256"]:
        raise ValueError(f"{edition_id}: cambió el manifiesto del marcador final.")
    [entry] = json.loads(raw)["editions"]
    cuts = [scene["id"] for scene in entry["scenes"]]
    if (entry["id"] != edition_id or entry["source_sha256"] != catalog[edition_id]["sha256"]
            or type(marker["approved"]) is not int or type(marker["generated"]) is not int
            or not 0 < len(cuts) == marker["approved"] <= marker["generated"]
            or len(set(cuts)) != len(cuts)):
        raise ValueError(f"{edition_id}: la edición o el recuento no coinciden con el marcador final.")
    return entry


def recovered_manifest(marker, catalog, base_manifest):
    edition_id = marker["id"]
    base_marker = json.loads((COMPLETE_OUT / "auditadas" / f"{edition_id}.json").read_bytes())
    if (base_marker["id"] != edition_id or marker["raw_sha256"] != base_marker["raw_sha256"]
            or marker["base_sha256"] != base_marker["sha256"]):
        raise ValueError(f"{edition_id}: el cierre de recuperación no corresponde a la edición base.")
    independent = COMPLETE_OUT / "revision-lectura" / f"{edition_id}.json"
    if sha256(independent.read_bytes()) != marker["independent_review_sha256"]:
        raise ValueError(f"{edition_id}: cambió la revisión de lectura que vincula el cierre.")
    base = marked_entry(base_marker, catalog, base_manifest)
    registry = COMPLETE_OUT / "recuperacion" / "vetos.json"
    listed = json.loads(registry.read_bytes())["vetos"] if registry.is_file() else []
    vetos = [row for row in listed if row["edition"] == edition_id]
    base_scenes = {scene["id"]: scene for scene in base["scenes"]}
    vetoes_by_cut = {row["cut"]: row for row in vetos}
    if len(vetoes_by_cut) != len(vetos) or any(
        not row["reason"].strip() or not row["evidence"].strip()
        or base_scenes.get(cut, {}).get("sha256") != row["sha256"]
        for cut, row in vetoes_by_cut.items()
    ):
        raise ValueError(f"{edition_id}: el registro contiene vetos duplicados o ajenos a la base.")
    extra_generated, extra_approved = marker["extra_generated"], marker["extra_approved"]
    withheld = marker["base_withheld"]
    if (type(extra_generated) is not int or type(extra_approved) is not int
            or type(withheld) is not int or withheld != len(vetos)
            or len(marker["vetos"]) != len(vetos) or {row["cut"]: row for row in marker["vetos"]} != vetoes_by_cut
            or type(marker["necesita_exportacion"]) is not bool
            or marker["necesita_exportacion"] != bool(extra_approved or withheld)
            or not 0 <= extra_approved <= extra_generated
            or marker["generated"] != base_marker["generated"] + extra_generated
            or marker["approved"] != base_marker["approved"] - withheld + extra_approved):
        raise ValueError(f"{edition_id}: recuento de recuperación incompatible con la edición base.")
    if not marker["necesita_exportacion"]:
        if marker["source"] != "base" or marker["sha256"] != base_marker["sha256"]:
            raise ValueError(f"{edition_id}: un cierre sin aprobaciones debe conservar el manifiesto base.")
        marked_entry(marker, catalog, base_manifest)
        return None
    if marker["source"] != "recovered":
        raise ValueError(f"{edition_id}: el cierre declara aprobaciones sin manifiesto recuperado.")
    recovered = COMPLETE_OUT / "recuperacion" / edition_id / "escenas-auditadas.json"
    final = marked_entry(marker, catalog, recovered)
    final_scenes = {scene["id"]: scene for scene in final["scenes"]}
    registered = final.get("editorial_vetoes", [])
    if (len(registered) != len(vetos) or {row["cut"]: row for row in registered} != vetoes_by_cut
            or vetoes_by_cut.keys() & final_scenes.keys()):
        raise ValueError(f"{edition_id}: el manifiesto recuperado no aplicó todos los vetos registrados.")
    preserved = ("source_xhtml", "xhtml_path", "xhtml_tag", "source_parts", "text_start", "text_end",
                 "xhtml_text_length", "text", "sha256", "title", "description", "content_label")
    for cut in base_scenes.keys() - vetoes_by_cut.keys():
        scene = base_scenes[cut]
        current = final_scenes.get(cut, {})
        if any(current.get(field) != scene.get(field) for field in preserved):
            raise ValueError(f"{edition_id}: la recuperación modificó la escena base {scene['id']}.")
    return recovered


def existing_delivery(destination, catalog, marker, cuts):
    edition_id = marker["id"]
    report = json.loads((destination / "validacion-exportacion.json").read_bytes())
    outputs = report["passed"]
    if (report["mode"] != "audited_scenes" or not report["fail_on_warnings"]
            or report["scene_manifest_sha256"] != marker["sha256"]
            or len(outputs) != 2 or {row["profile"] for row in outputs} != {"Color", "BOOX"}
            or any(row["id"] != edition_id or [s["id"] for s in row["scenes"]] != cuts for row in outputs)):
        raise ValueError(f"{edition_id}: el destino existente corresponde a otro lote.")
    verify_delivery(destination, catalog, report)
    return report


def export_marker(marker_path, catalog, command, destination=None):
    marker = json.loads(marker_path.read_bytes())
    edition_id = marker["id"]
    recovery = marker_path.name == "cierre.json"
    named_id = marker_path.parent.name if recovery else marker_path.stem
    if named_id != edition_id or edition_id not in catalog:
        raise ValueError(f"{marker_path.name}: el marcador no identifica una edición admitida.")
    manifest = PUBLIC_OUT / "seleccion" / edition_id / "imagenes-finales" / "escenas-auditadas.json"
    if recovery:
        manifest = recovered_manifest(marker, catalog, manifest)
        if manifest is None:
            print(f"{edition_id} · cierre sin cambios. Se conserva la entrega base.", flush=True)
            return None
    entry = marked_entry(marker, catalog, manifest)
    cuts = [scene["id"] for scene in entry["scenes"]]
    destination = destination or COMPLETE_OUT / ("epubs-recuperados" if recovery else "epubs") / edition_id
    if recovery and destination.resolve() == (COMPLETE_OUT / "epubs" / edition_id).resolve():
        raise ValueError(f"{edition_id}: la recuperación requiere un destino separado de la entrega base.")
    if destination.is_dir() and any(destination.iterdir()):
        report = existing_delivery(destination, catalog, marker, cuts)
        print(f"{edition_id} · entrega existente verificada", flush=True)
        return report
    return run(catalog, destination, command, manifest_path=manifest, edition_id=edition_id,
               manifest_sha256=marker["sha256"])


def export_postreading(edition_id, catalog, command):
    if edition_id not in catalog:
        raise ValueError(f"{edition_id}: la edición no está admitida.")
    work = COMPLETE_OUT / "recuperacion" / edition_id
    closure = work / "cierre.json"
    closure_bytes = closure.read_bytes()
    marker = json.loads(closure_bytes)
    base_manifest = PUBLIC_OUT / "seleccion" / edition_id / "imagenes-finales" / "escenas-auditadas.json"
    if marker["id"] != edition_id:
        raise ValueError(f"{edition_id}: el cierre corresponde a otra edición.")
    source = recovered_manifest(marker, catalog, base_manifest)
    if source is None:
        raise ValueError(f"{edition_id}: no hay escenas de recuperación que revisar.")
    entry = marked_entry(marker, catalog, source)
    original = COMPLETE_OUT / "epubs-recuperados" / edition_id
    original_report = existing_delivery(original, catalog, marker, [scene["id"] for scene in entry["scenes"]])
    base_marker = json.loads((COMPLETE_OUT / "auditadas" / f"{edition_id}.json").read_bytes())
    base = marked_entry(base_marker, catalog, base_manifest)
    base_cuts = {scene["id"] for scene in base["scenes"]}
    recovery_cuts = {scene["id"]: scene["sha256"] for scene in entry["scenes"]}
    veto_file = COMPLETE_OUT / "revision-lectura" / "vetos-recuperacion.json"

    def read_vetos():
        return [row for row in json.loads(veto_file.read_bytes())["vetos"] if row["edition"] == edition_id]

    vetos = read_vetos()
    excluded = {row["cut"] for row in vetos}
    if not vetos or len(excluded) != len(vetos) or any(
        row["cut"] in base_cuts or recovery_cuts.get(row["cut"]) != row["sha256"]
        or not row["reason"].strip() or not row["evidence"].strip()
        for row in vetos
    ):
        raise ValueError(f"{edition_id}: el registro debe identificar escenas extra por corte y huella.")
    kept = [scene for scene in entry["scenes"] if scene["id"] not in excluded]
    manifest = work / "escenas-poslectura.json"
    origin = dict(manifest=str(source), sha256=marker["sha256"], cierre=str(closure),
                  cierre_sha256=sha256(closure_bytes))
    data = encode(dict(complete=True, origen=origin, editions=[
        dict(entry, scenes=kept, review_vetoes=vetos),
    ]))
    final = dict(id=edition_id, manifest=str(manifest), sha256=sha256(data),
                 generated=marker["generated"], approved=len(kept), origen=origin, vetos=vetos)
    marker_path = COMPLETE_OUT / "revisadas" / f"{edition_id}.json"
    if ((manifest.exists() and manifest.read_bytes() != data)
            or (marker_path.exists() and marker_path.read_bytes() != encode(final))):
        raise ValueError(f"{edition_id}: la revisión existente corresponde a otro conjunto de vetos.")
    if not manifest.exists():
        atomic_write(manifest, data)
    destination = COMPLETE_OUT / "epubs-revisados" / edition_id
    if destination.is_dir() and any(destination.iterdir()):
        report = existing_delivery(destination, catalog, final, [scene["id"] for scene in kept])
    else:
        report = run(catalog, destination, command, manifest_path=manifest, edition_id=edition_id,
                     manifest_sha256=final["sha256"])
    for output in report["passed"]:
        with ZipFile(destination / output["path"]) as epub:
            filenames = {Path(name).name for name in epub.namelist()}
            if filenames & {f"{cut}.jpg" for cut in excluded}:
                raise ValueError(f"{edition_id}: reapareció una escena vetada en el EPUB.")
    if (closure.read_bytes() != closure_bytes or sha256(source.read_bytes()) != marker["sha256"]
            or read_vetos() != vetos):
        raise ValueError(f"{edition_id}: cambió el origen o el veto durante la validación.")
    verify_delivery(original, catalog, original_report)
    if not marker_path.exists():
        atomic_write(marker_path, encode(final))
    print(f"{edition_id} · par de poslectura verificado", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(prog=globals().get("CLI_PROGRAM"), description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixtures", action="store_true", help="Use locally drawn images to test the packager.")
    mode.add_argument("--scenes", "--escenas", dest="escenas", metavar="MANIFEST", type=Path, help="Manifest of visually approved scenes.")
    mode.add_argument("--completion-marker", "--marcador-final", dest="marcador_final", metavar="MARKER", type=Path, help="Audit or recovery completion marker for one edition.")
    mode.add_argument("--post-review", "--poslectura", dest="poslectura", metavar="EDITION", help="Apply recorded visual vetoes to a recovered edition.")
    parser.add_argument("--edition", "--edicion", dest="edicion", metavar="EDITION", help="Limit fixture mode or a scene manifest to one edition.")
    parser.add_argument("--output", "--salida", dest="salida", metavar="DIR", type=Path)
    parser.add_argument("--epubcheck", required=True, type=Path)
    parser.add_argument("--java", default="java")
    args = parser.parse_args()
    if args.poslectura and args.salida:
        parser.error("--post-review installs the EPUBs in epubs-revisados and does not accept --output.")
    if args.edicion and (not (args.escenas or args.fixtures) or not args.salida):
        parser.error("--edition requires --scenes or --fixtures, and --output.")
    catalog = {item["id"]: item for item in json.loads(CATALOG.read_bytes())["editions"]}
    if args.edicion and args.edicion not in catalog:
        parser.error("Unknown edition ID. Run the catalog command to list them.")
    if args.fixtures and args.edicion:
        catalog = {args.edicion: catalog[args.edicion]}
    command = [args.java, "-jar", str(args.epubcheck.resolve())]
    if args.poslectura:
        export_postreading(args.poslectura, catalog, command)
    elif args.marcador_final:
        export_marker(args.marcador_final.resolve(), catalog, command,
                      args.salida.resolve() if args.salida else None)
    else:
        destination = args.salida or PUBLIC_OUT / ("fixtures-exportacion" if args.fixtures else "epubs")
        run(catalog, destination.resolve(), command, manifest_path=args.escenas.resolve() if args.escenas else None,
            edition_id=None if args.fixtures else args.edicion)


if __name__ == "__main__":
    main()
