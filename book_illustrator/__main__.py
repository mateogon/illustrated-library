"""Prepare and illustrate a checked EPUB edition from the demo catalog."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import runpy
import sys

from book_illustrator.coleccion import (
    PUBLIC_SELECTION, prepare_public, public_catalog, public_source,
)
from book_illustrator.runtime import ROOT, api_key


TOOLS = {
    "export": ("exportar.py", "Package and validate Color and BOOX EPUBs."),
    "audit-batch": ("auditar_completa.py", "Audit generated catalog editions and create completion markers."),
    "recover": ("recuperar_cobertura.py", "Use audited candidates to fill selected long gaps."),
}


def main():
    parser = argparse.ArgumentParser(prog="python -m book_illustrator", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("catalog", help="List supported edition IDs.")
    commands.add_parser("prepare", help="Extract the narrative and preserve its EPUB anchors.")
    commands.add_parser("select", help="Find cuts and audit text-backed scene descriptions.")
    commands.add_parser("sample", help="Generate the first approved illustration.")
    commands.add_parser("generate", help="Generate the remaining approved illustrations.")
    commands.add_parser("audit", help="Review both image profiles and bind approvals to their bytes.")
    for name, (_, description) in TOOLS.items():
        commands.add_parser(name, help=description)
    command = parser.parse_args(sys.argv[1:2]).command
    arguments = sys.argv[2:]
    if command in TOOLS:
        script, _ = TOOLS[command]
        sys.argv = [f"book-illustrator {command}", *arguments]
        scripts = ROOT / "demo-publica/lote"
        sys.path.insert(0, str(scripts))
        runpy.run_path(str(scripts / script), run_name="__main__",
                       init_globals={"CLI_PROGRAM": f"book-illustrator {command}"})
        return

    stage = argparse.ArgumentParser(prog=f"book-illustrator {command}")
    if command == "audit":
        from book_illustrator.auditoria_visual import main as audit

        audit(arguments, program=stage.prog)
        return
    if command == "catalog":
        stage.parse_args(arguments)
        for edition in public_catalog().values():
            print(f"{edition['id']}\t{edition['language']}\t{edition['title']}")
        return

    stage.add_argument("edition", help="Edition ID from the catalog command.")
    if command in {"select", "generate"}:
        stage.add_argument("--workers", type=int, choices=range(1, 33), metavar="1–32",
                           default=8 if command == "select" else 12)
    if command == "select":
        stage.add_argument("--interval", type=int, choices=range(4, 9), default=4,
                           help="Target gap in virtual pages (default 4).")
        stage.add_argument("--cache-only", "--solo-cache", action="store_true",
                           help="Replay matching text receipts without API calls.")
    if command == "generate":
        mode = stage.add_mutually_exclusive_group()
        mode.add_argument("--reframe-blocked", action="store_true",
                          help="Reaudit a different supported motif after a definitive content refusal.")
        mode.add_argument("--find-alternatives", action="store_true",
                          help="Look for an approved neighboring cut after a reframe also fails.")
    args = stage.parse_args(arguments)
    catalog = public_catalog()
    if args.edition not in catalog:
        stage.error("Unknown edition ID. Run the catalog command to list them.")
    edition = catalog[args.edition]
    if command in {"prepare", "select"}:
        entry = prepare_public(args.edition)
        print(f"{args.edition}: {entry['units']} narrative units, {entry['page_count']} virtual pages", flush=True)
        if command == "select":
            from book_illustrator.seleccion import run

            run(args.edition, None if args.cache_only else api_key(), args.workers,
                content_mode=True, output=PUBLIC_SELECTION, interval=args.interval, prospective=True)
        return
    public_source(edition)
    if command == "sample":
        from book_illustrator.muestras import build

        build([args.edition], collection=PUBLIC_SELECTION, books=tuple(catalog))
        return

    from book_illustrator import produccion_imagen
    from book_illustrator.imagenes import previous_attempts

    if args.reframe_blocked:
        folder = PUBLIC_SELECTION / args.edition / "imagenes-finales"
        blocked = [cut for cut, row in previous_attempts(folder).items() if row.get("code") == "moderation_blocked"]
        with ThreadPoolExecutor(max_workers=min(6, args.workers)) as pool:
            outcomes = list(pool.map(lambda cut: produccion_imagen.reframe(args.edition, cut, collection=PUBLIC_SELECTION), blocked))
        raise SystemExit(int(not all(outcomes)))
    if args.find_alternatives:
        raise SystemExit(int(not produccion_imagen.neighboring_cuts(args.edition, collection=PUBLIC_SELECTION)))
    raise SystemExit(produccion_imagen.execute([args.edition], args.workers, collection=PUBLIC_SELECTION))


if __name__ == "__main__":
    main()
