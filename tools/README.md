# Library data builder

Use Python 3.9 or later. Download the 32 EPUB assets from the `library-v1` release into `releases/`, or pass their local directory with `--release-dir`. The site data and illustrations are built from those EPUBs.

```sh
python3 tools/build_library.py
```

The maintainers can pass `--source-catalog /path/to/biblioteca.json` to prepare release EPUBs from the pinned archival files. The builder checks the archival and reading copies against their recorded hashes. It checks EPUB members, illustration notes, internal backlinks, JPEG metadata, and the order of reading documents. The chapter XVI illustration `P0682` in *La isla del tesoro* is held because it was generated with the wrong narrator. The reading text stays intact.

`library_sources.json` records the text sources and the terms for their included layers. It distinguishes the historical text and illustrations from digital contributions and the new illustrations. Chilean and U.S. rights notes cover the specific text or translation in each edition. The relevant transcription attribution stays in the reading documents, including Wikisource’s CC BY-SA material.

## Output

- `site/data/library.json` contains the catalogue, public source credits, and each download’s release URL, byte count, and SHA-256.
- `site/data/books/<edition-id>.json` contains the reading spine in order. HTML blocks use a fixed set of semantic tags. Links between reading sections expose `data-section-id` and, where present, `data-fragment`. New illustrations appear as blocks linked to the reading cut. The English *Alice* additionally includes the historical John Tenniel images.
- `site/images/` contains the same image bytes as the Color EPUBs. New images are JPEGs. The 42 historical Tenniel images are SVGs supplied by Standard Ebooks under CC0 and tracked separately.
- `releases/` contains a Color and a BOOX EPUB for each edition. The site files and Release assets have separate output directories.
