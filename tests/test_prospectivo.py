import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PIL import Image

from book_illustrator import corte, muestras, produccion_imagen, seleccion
from book_illustrator.coleccion import ROOT, atomic_write, corpus, encode, public_paragraphs
from book_illustrator.imagenes import IMAGE_OPTIONS, png_sha256, sha256
from book_illustrator.render_prompt import PUBLIC_COMPOSITION_VERSION, PUBLIC_PROMPT_VERSION


def context(future, extra=()):
    texts = ["Había " + "polvo " * 1095, "El sobre seguía cerrado.", future, *extra]
    rows = [dict(id=f"P{i:04d}", text=text, page=i, chapter=future, source_xhtml=future)
            for i, text in enumerate(texts, 1)]
    return dict(paragraphs=rows, position={row["id"]: i for i, row in enumerate(rows)},
                pages=list(range(1, len(rows) + 1)), segment_at={i: "prueba" for i in range(len(rows))},
                boundaries=corte.prefix_boundaries(rows), prospective=True)


def opportunity(quote_id="P0002", quote="sobre seguía cerrado"):
    return {"motif": "El sobre cerrado", "reason": "Hace reconocible el objeto.", "value": 2,
            "evidence": [dict(id=quote_id, quote=quote)], "omission": {"text": "", "evidence": []}}


class ProspectiveCutTest(TestCase):
    def test_interior_cut_and_focal_search_receive_the_same_past_in_two_futures(self):
        received = []

        def responses(key, model, rules, inputs, schema, *, cache_key):
            received.append((model, rules, inputs, schema, cache_key))
            return {"data": opportunity()}

        with TemporaryDirectory() as temp, patch.object(seleccion.api, "structured", side_effect=responses) as api:
            ids = []
            for name, ctx in (
                ("el-mapa-de-ambar", context("El contenido era un mapa de ámbar.")),
                ("la-flor-de-cobre", context("El contenido era una flor de cobre.", ["Algo pasó." * 300])),
            ):
                root = Path(temp) / name / "contenido"
                (root / "llamadas").mkdir(parents=True)
                proposal, _, first = seleccion.prospective_detector(root, "fake", ctx, "P0002")
                self.assertEqual(proposal["first"]["status"], "valido")
                _, _, replay = seleccion.prospective_detector(root, None, ctx, "P0002")
                self.assertEqual(first, replay)
                block = {"id": "r001", "core_start": "P0001", "core_end": "P0003",
                         "context_start": "P0001", "context_end": "P0003"}
                detected, recovery = seleccion.detector(root, "fake", ctx, block, allowed={"P0001", "P0002", "P0003"})
                self.assertEqual(detected["context_end"], "P0002")
                self.assertEqual(detected["proposals"][0]["first"]["id"], "P0002")
                ids.append((first["fingerprint"], recovery["fingerprint"]))
            self.assertEqual(api.call_count, 4)
        self.assertEqual(ids[0], ids[1])
        self.assertTrue(all(request == received[0] for request in received))
        payload = json.dumps(received, ensure_ascii=False)
        self.assertIn("El sobre seguía cerrado.", payload)
        self.assertNotIn("P0003", payload)
        self.assertNotIn("ámbar", payload)
        self.assertNotIn("cobre", payload)

    def test_rejects_future_support_for_an_exact_cut(self):
        ctx = context("El contenido era un mapa de ámbar.")
        row, _ = seleccion.validate_prospective_detection(opportunity("P0003", "mapa de ámbar"), ctx, "P0002")
        self.assertFalse(row["first"]["evidence"])
        self.assertEqual(row["first"]["status"], "soporte_no_retrospectivo_o_no_literal")

    def test_recovers_missing_detector_reason_only_with_support_and_no_explicit_omission(self):
        ctx = context("Mapa revelado.")
        reply = {"status": "completed", "data": {**opportunity(), "reason": ""}}
        with patch.object(seleccion, "phase_call", return_value=reply):
            row, _, _ = seleccion.prospective_detector(Path("contenido"), None, ctx, "P0002")
            self.assertEqual(row["first"]["status"], "valido")
            self.assertEqual(seleccion.detection_outcome({"status": "completed", "omission": None, "proposals": [row]}),
                             "reason_vacio_aporte_pendiente_de_ficha_y_decisions")
            reply["data"]["omission"]["text"] = "Reflexión sin gesto visible."
            row, _, _ = seleccion.prospective_detector(Path("contenido"), None, ctx, "P0002")
            self.assertEqual(row["first"]["status"], "propuesta_y_omision_incompatibles")

    def test_public_spine_headings_and_formal_prompt_exclude_unread_metadata(self):
        body = ET.fromstring('''<body xmlns:epub="http://www.idpf.org/2007/ops">
            <h1>El mapa de ámbar</h1><p epub:type="title">La flor de cobre</p>
            <p>El sobre seguía <em>cerrado</em>.</p>
            <aside epub:type="footnotes"><p>Revelación reservada.</p></aside>
        </body>''')
        self.assertEqual(list(public_paragraphs(body)), [((2,), "El sobre seguía cerrado.")])
        pick = {"brief": "Un sobre cerrado en la mesa.", "core": {"text": "Sobre cerrado"}}
        injected = {**pick, "title": "El mapa de ámbar", "author": "La flor de cobre",
                    "art_direction": "Revelación reservada", "references": ["futuro.png"]}
        self.assertEqual(produccion_imagen.make_prompt("obra-desconocida-a", pick),
                         produccion_imagen.make_prompt("obra-desconocida-b", injected))
        en = produccion_imagen.prompt_profile("don-quijote-en", {"work_id": "don-quijote"})[0]
        es = produccion_imagen.prompt_profile("don-quijote-es", {"work_id": "don-quijote"})[0]
        subterra = produccion_imagen.prompt_profile("sub-terra-es", {"work_id": "sub-terra"})[0]
        self.assertEqual(en(pick), es(injected))
        self.assertNotEqual(en(pick), subterra(pick))
        self.assertNotIn("don-quijote", en(pick))
        tightened = produccion_imagen.prompt_profile("don-quijote-en", {"work_id": "don-quijote"},
                                                     prompt_version=PUBLIC_COMPOSITION_VERSION)[0]
        self.assertTrue(tightened(injected).startswith(en(pick)))
        self.assertIn("para dejar su cantidad fuera de campo", tightened(pick))
        self.assertNotIn("El mapa de ámbar", tightened(injected))

    def test_public_sample_keeps_recorded_version_and_reserves_new_editions(self):
        with TemporaryDirectory() as temp:
            output = Path(temp)
            atomic_write(output / "color/intentos.jsonl", b'{"event":"started","cut":"anterior","attempt_id":"a"}\n')
            self.assertEqual(muestras.public_sample_version(output, "anterior"), PUBLIC_PROMPT_VERSION)
            self.assertEqual(muestras.public_sample_version(output, "nuevo"), PUBLIC_COMPOSITION_VERSION)
            atomic_write(output / "versiones.json", encode({"nuevo": PUBLIC_COMPOSITION_VERSION}))
            with (output / "color/intentos.jsonl").open("ab") as attempts:
                attempts.write(b'{"event":"started","cut":"nuevo","attempt_id":"b"}\n')
            self.assertEqual(muestras.public_sample_version(output, "nuevo"), PUBLIC_COMPOSITION_VERSION)

    def test_catalog_flow_reuses_canonical_sample_and_emits_unaudited_scenes(self):
        slug = "prueba-en"
        rows = [dict(id=f"P{i:04d}", page=i, chapter="Reveal in the heading", source_xhtml="novela.xhtml",
                     xhtml_path=[i - 1], xhtml_tag="p", text_start=0, text_end=18, xhtml_text_length=18,
                     text="The box is closed. " + "Closed. " * 287) for i in range(1, 11)]
        fixture = corpus("Private Title", ROOT / "fixture.epub", "source-hash", rows, [(slug, 0, len(rows))],
                         exclusions=[], language="en-GB", author="Private Author")
        catalog = {slug: {"id": slug, "work_id": "treasure-island", "sha256": "source-hash"}}

        def responses(key, model, rules, inputs, schema, *, cache_key):
            parts = inputs[-1]["content"]
            pid = re.search(r"P\d+", parts[-1]["text"])[0]
            claim = {"text": "A closed box", "evidence": [dict(id=pid, quote="The box is closed.")]}
            self.assertNotIn("Reveal in the heading", "".join(p["text"] for p in parts))
            if schema == seleccion.DRAFT_SCHEMA:
                self.assertIn("Escribe la ficha en inglés", rules)
                return {"data": dict(omit=False, omission={"text": "", "evidence": []}, title="Closed Box",
                                     core=claim, contribution="Clarifies the box's condition.", accessories=[])}
            if pid == "P0001":
                return {"data": {**opportunity(pid, "The box is closed."), "reason": ""}}
            return {"data": dict(motif="", reason="", value=1, evidence=[], omission=claim)}

        def decisions(key, model, inputs, questions, *, cache_key):
            return {"answers": [dict(name=q["name"], type="predicate",
                                     probability=0.9 if q["name"] in {"visible", "aporte"} else 0.1)
                                for q in questions]}

        def sample_provider(jobs, folder, *, omit_terminal):
            self.assertTrue(omit_terminal)
            folder.mkdir()
            for name, prompt in jobs.items():
                self.assertNotIn("Private Title", prompt)
                self.assertNotIn("Private Author", prompt)
                path = folder / name
                Image.new("RGB", (1200, 800), "ivory").save(path)
                atomic_write(path.with_suffix(".png.json"), encode({**IMAGE_OPTIONS, "request_id": "fake",
                    "file": name, "sha256": png_sha256(path.read_bytes()), "usage": None,
                    "prompt_sha256": sha256(prompt.encode())}))
            return 0

        with TemporaryDirectory() as temp:
            public = Path(temp)
            atomic_write(public / slug / "corpus.json", encode(fixture))
            with patch.object(seleccion.api, "structured", side_effect=responses), patch.object(
                seleccion.api, "decisions", side_effect=decisions,
            ):
                seleccion.run(slug, "fake", 1, output=public, interval=4, content_mode=True, prospective=True)
            with patch.object(muestras, "run", side_effect=sample_provider), patch.object(
                muestras, "PUBLIC_SELECTION", public,
            ), patch.object(muestras, "public_catalog", return_value=catalog):
                muestras.build([slug], collection=public, books=[slug])
            with patch.object(produccion_imagen, "PUBLIC_SELECTION", public), patch.object(
                produccion_imagen, "public_catalog", return_value=catalog,
            ), patch.object(
                produccion_imagen, "PARTIAL_MIN_IMAGES", 1,
            ), patch.object(
                produccion_imagen, "api_key", side_effect=AssertionError("No external image request expected"),
            ):
                self.assertEqual(produccion_imagen.execute([slug], 1, collection=public), 0)
            manifest = json.loads((public / slug / "imagenes-finales/escenas.json").read_bytes())
            partial = json.loads((public / slug / "imagenes-finales/escenas-parciales.json").read_bytes())
            self.assertFalse(partial["complete"])
            self.assertTrue(manifest["complete"])
            self.assertEqual(partial["editions"][0]["scenes"], manifest["editions"][0]["scenes"])
            [scene] = manifest["editions"][0]["scenes"]
            self.assertEqual((scene["id"], scene["title"], scene["description"]),
                             ("P0001", "Closed Box", "A closed box"))
            self.assertNotIn("visual_review", scene)

    def test_public_image_status_omits_only_definitive_content_rejections(self):
        pick = {"id": "P0001", "draft_call": "draft", "decision_call": "audit"}
        jobs = {"P0001.png": "prompt"}
        edition = {"work_id": "don-quijote", "sha256": "source"}
        with TemporaryDirectory() as temp, patch.object(produccion_imagen, "publish") as publish:
            folder = Path(temp)
            state = (folder, [pick], jobs, "corpus")
            invalid = {"event": "failed", "http_status": 400, "code": "moderation_blocked",
                       "prompt_sha256": sha256(b"old prompt")}
            terminal = {**invalid, "prompt_sha256": sha256(b"prompt")}
            for attempt, unconfirmed, pending in ((invalid, set(), True), (terminal, {"P0001"}, True),
                                                  (terminal, set(), False)):
                with patch.object(produccion_imagen, "image_attempts_by_cut",
                                  return_value=({"P0001": attempt}, unconfirmed)):
                    self.assertEqual(produccion_imagen.public_book_status("prueba", state, edition, pick | {"cut": "P0000"}), pending)
            publish.assert_called_once()
            self.assertEqual(publish.call_args.kwargs["omissions"][0]["attempt"], terminal)
