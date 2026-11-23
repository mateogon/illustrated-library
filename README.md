# Illustrated Library · Book Illustrator

**[Open the illustrated library](https://mateogon.github.io/illustrated-library/)** to read 10 classics across 16 English and Spanish editions. Select an edition to read it by chapter, open the image tour, or download the Color or e-ink EPUB. The collection includes 557 newly generated illustrations and 42 historical illustrations by John Tenniel in the English edition of *Alice*.

[Download all editions](https://github.com/mateogon/illustrated-library/releases/tag/library-v1) · [Run the website locally and rebuild it](docs/PUBLISHING.md) · [How repeatable is scene selection?](docs/REPEATABILITY.md)

Book Illustrator adds scene illustrations to EPUBs at the point where the reader has enough context to see them. It keeps the reading text and existing assets, inserts each image at its paragraph anchor, and links the image to a short scene note and back to the passage. It exports a color edition and a grayscale BOOX edition.

This source snapshot contains the production path used for the 16 English and Spanish editions in [the catalog](demo-publica/catalogo.json). Each edition has a pinned input and a reproducible source recipe. EPUBs, generated images, and API receipts are built locally.

## Setup

Use Python 3.13 on macOS or Linux with POSIX file locking, and install the [Python dependencies](requirements.txt) in a virtual environment. Source recipes may use `curl`. Export also needs Java and the [EPUBCheck 5.4.0 JAR](https://github.com/w3c/epubcheck/releases/tag/v5.4.0). The snapshot was tested with Python 3.13.12 and Java 21 on macOS.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m book_illustrator catalog
```

Use `uv run --with-requirements requirements.txt python` in place of `python` for an environment managed by `uv`. The command examples below run from the repository root.

## Try an export without an API key

Reconstruct the catalog edition of *Treasure Island*, build its narrative corpus, and generate a local geometric fixture. Use a new output directory for this export.

```sh
python -B demo-publica/en/reconstruir.py treasure-island-en
python -B -m book_illustrator prepare treasure-island-en
python -B -m book_illustrator export --fixtures --edition treasure-island-en \
  --output outputs/fixture-treasure-island \
  --epubcheck /path/to/epubcheck.jar
```

Add `--java /path/to/java` if Java is outside your `PATH`. The export command checks both EPUBs with EPUBCheck, treats warnings as failures, and writes `validacion-exportacion.json` with the input and output hashes. Omitting `--edition` runs this fixture export against the whole catalog. Prepare its source EPUBs first using the [source recipes](PROVENANCE.md).

## Supported inputs

The CLI accepts an **edition ID** printed by `catalog`. It reads the corresponding EPUB from the path in the catalog and verifies its size and SHA-256 before preparing or illustrating it. A filename, URL, or arbitrary EPUB path is outside this CLI's supported input interface. The catalog recipes reconstruct the 16 supported editions. The underlying packager works with EPUB structure and paragraph anchors, but this snapshot provides no import command for a new book.

## Illustrate an edition

Set `OPENAI_API_KEY` in your environment or in a local `.env` file. The account must have access to `gpt-6-luna` in Responses and Decisions, and `gpt-image-2.6-sunburst` in the Images API. The pipeline requests landscape images at 1200 × 800 in medium quality. Selection, generation, visual audit, and recovery can send paid requests. Use an EPUB whose text you are authorized to submit to the provider.

```sh
edition=treasure-island-en
python -B -m book_illustrator select "$edition" --workers 4
python -B -m book_illustrator sample "$edition"
python -B -m book_illustrator generate "$edition" --workers 4
scene_dir="outputs/demo-publica/seleccion/$edition/imagenes-finales"
python -B -m book_illustrator audit "$scene_dir/escenas.json"
python -B -m book_illustrator export \
  --scenes "$scene_dir/escenas-auditadas.json" \
  --edition "$edition" --output "outputs/illustrated-$edition" \
  --epubcheck /path/to/epubcheck.jar
```

- **Select.** The detector evaluates fixed cuts. The writer receives a window from the reading prefix ending at each cut. Decisions checks visibility, early disclosure, editorial value, and content risk. The selection aims for one image every four virtual pages of roughly 300 words each, with room for longer gaps when the evidence warrants it. Add `--cache-only` to replay matching text receipts offline.
- **Generate.** The first sample and the remaining PNGs carry receipts that bind the image bytes to their prompts and settings. A request with an uncertain outcome stays pending for inspection. A terminal content refusal may leave an omission in the scene manifest. `generate --reframe-blocked` and `generate --find-alternatives` are explicit follow-ups that can use the API.
- **Audit.** The reviewer inspects color and grayscale images and checks the title and description against the text available at the anchor. Later text is used during the final review to spot potential disclosures. An approval binds to the source, image, scene metadata, and both rendering profiles.
- **Export.** The packager verifies the audit receipts, retains the reading text and original resources, and validates both EPUBs before installing the pair. Model checks are fallible. Review the actual images and notes before distributing the edition.

Command help is in English. The detection, drafting and Decisions prompts, the public palettes, and the visual review instructions keep their tested wording. Most editorial instructions are in Spanish. The blind image inventory prompt is in English. Existing JSON keys, status values and internal output filenames remain the same for interoperability. Drafting requests English titles and scene descriptions for English editions. Book text and existing notes retain their edition language.

## Batch review and gap recovery

`python -B -m book_illustrator audit-batch` audits complete image manifests and writes a completion marker for each edition under `outputs/demo-publica/completa/auditadas/`. Add `--wait` when other processes will finish generating more editions. `export --completion-marker PATH --epubcheck ...` verifies and exports one marked edition.

`python -B -m book_illustrator recover --plan EDITION` inspects available completion markers without calling the API. Recovery ranks already-approved candidates for long gaps, keeps each candidate at a distinct cut, and audits any new images. A reading review is required to close recovery. Store a JSON object with the edition `id`, a nonempty list `cuts` of base cut IDs you reviewed, `estado` set to `revisada` or `veto`, and a `vetos` list of vetoed IDs in `outputs/demo-publica/completa/revision-lectura/EDITION.json`. A veto also needs a record bound to the approved PNG in `outputs/demo-publica/completa/recuperacion/vetos.json`. Its `vetos` array holds rows with `edition`, `cut`, `sha256`, `reason`, and `evidence`.

Run `python -B -m book_illustrator recover EDITION` after reading review. Pass the resulting `outputs/demo-publica/completa/recuperacion/EDITION/cierre.json` to `export --completion-marker` to validate and install the recovered pair. The base and recovered editions have separate paths. Use `export --help` for the post-review veto command.

## Tests and distribution

```sh
python -B -m unittest discover -s tests -v
```

The suite uses synthetic text, EPUB fragments, and mocked provider responses. It exercises prospective cuts, citations, audit bindings, resumable requests, packaging and recovery. EPUBCheck 5.4.0 passed all 32 fixture exports for the 16 pinned editions. A separate run rebuilt *Treasure Island* from its upstream source. The single-edition fixture and a replay using one previously audited scene both passed export validation without an API key. This snapshot was tested with mocked provider responses and zero new paid requests. The website uses finished publications. Running the production pipeline creates its own local outputs. See [Provenance and licensing](PROVENANCE.md) for edition rights, asset terms, and the status of the code license.
