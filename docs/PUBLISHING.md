# Run the library locally

The public site is static. Reading a book, browsing the artwork and downloading an EPUB do not require an OpenAI key, a login or a build step.

From a checkout, run:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open [the local bookshelf](http://127.0.0.1:8000/). Each edition has a chapter reader and a separate illustration tour. The tour can show scenes from late in the story. The interface is in English. Books, translated passages and scene captions use the edition's language.

## Rebuild from the published EPUBs

The tagged release contains both downloadable formats for all 16 editions. Color uses the original color illustrations. The BOOX profile uses grayscale images prepared for e-ink. The site uses the Color editions for on-screen reading.

```sh
gh release download library-v1 --repo mateogon/illustrated-library \
  --dir releases --pattern '*.epub' --pattern 'SHA256SUMS.txt'
(cd releases && shasum -a 256 --check SHA256SUMS.txt)
python3 tools/build_library.py --release-dir releases --site site
```

The builder reads the EPUB spine in order, extracts the illustrations and writes the section data and catalog consumed by the reader. The [edition records](../tools/library_sources.json) identify the source edition, translation, rights evidence and any withheld material. The same files can be downloaded by hand from [Releases](https://github.com/mateogon/illustrated-library/releases/tag/library-v1).

## Hosting

The source, static UI and prepared reading data are on the `main` branch. The EPUB binaries and their checksums are release assets. The [Pages workflow](../.github/workflows/pages.yml) deploys only the `site/` directory. A push that changes that directory triggers publication. The workflow can also be run manually from GitHub Actions. Edition records contain the versioned release downloads so an edition links to the exact files verified for it.
