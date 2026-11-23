import errno
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError
import zlib

from book_illustrator import api, costos_coleccion, imagenes, produccion_imagen, seleccion


def http_error(status, code):
    return HTTPError("https://api.openai.com/v1/test", status, code, {"x-request-id": "req_test"},
                     BytesIO(json.dumps({"error": {"code": code}}).encode()))


def png_fixture():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

    width, height = imagenes.IMAGE_DIMENSIONS
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes((width + 1) * height))) + chunk(b"IEND", b""))


class TextReceiptsTest(unittest.TestCase):
    def test_ambiguous_post_is_sent_once_and_blocks_peer_requests(self):
        failures = (http_error(503, "server_error"), http_error(408, "timeout"),
                    URLError("lost connection"), BytesIO(b"partial json"))
        for failure in failures:
            with self.subTest(failure=failure), TemporaryDirectory() as tmp:
                book = Path(tmp) / "novela"
                content = book / "contenido"
                for root in (book, content):
                    (root / "llamadas").mkdir(parents=True)
                with patch.object(api, "urlopen", return_value=failure) as post:
                    if isinstance(failure, Exception):
                        post.side_effect = failure

                    def draft(root, key):
                        return seleccion.phase_call(root, key, "draft", "texto", schema=seleccion.DRAFT_SCHEMA)

                    first = draft(content, "test")
                    self.assertTrue(first["billing_unknown"])
                    self.assertFalse(first["retryable"])
                    self.assertEqual(first, draft(content, "test"))
                    self.assertEqual(first, draft(book, "test"))
                    self.assertEqual(first, draft(content, None))
                    self.assertEqual(first, draft(book, None))
                    post.assert_called_once()

    def test_open_decisions_reservation_survives_interruption(self):
        with TemporaryDirectory() as tmp, patch.object(api, "urlopen", side_effect=KeyboardInterrupt) as post:
            root = Path(tmp)
            (root / "llamadas").mkdir()
            with self.assertRaises(KeyboardInterrupt):
                seleccion.phase_call(root, "test", "audit", "texto")
            offline = seleccion.phase_call(root, None, "audit", "texto")
            self.assertEqual(offline["status"], "unconfirmed")
            self.assertEqual(offline, seleccion.phase_call(root, "test", "audit", "texto"))
            post.assert_called_once()

    def test_paid_but_incomplete_response_replays_across_variants(self):
        body = {"status": "incomplete", "usage": {"input_tokens": 100, "output_tokens": 20}}
        with TemporaryDirectory() as tmp, patch.object(api, "urlopen", return_value=BytesIO(json.dumps(body).encode())) as post:
            book = Path(tmp)
            content = book / "contenido"
            for root in (book, content):
                (root / "llamadas").mkdir(parents=True)
            receipt = seleccion.phase_call(content, "test", "draft", "texto", schema=seleccion.DRAFT_SCHEMA)
            self.assertEqual(receipt["status"], "response_not_completed")
            self.assertEqual(receipt, seleccion.phase_call(book, "test", "draft", "texto", schema=seleccion.DRAFT_SCHEMA))
            post.assert_called_once()

    def test_explicit_rate_limit_retries_and_keeps_both_attempts(self):
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "{}"}]}],
            "usage": {"input_tokens": 100, "output_tokens": 20}, "model": "gpt-6-luna"}
        outcomes = [http_error(429, "rate_limit_exceeded"), BytesIO(json.dumps(response).encode())]
        with TemporaryDirectory() as tmp, patch.object(api, "urlopen", side_effect=outcomes) as post:
            root = Path(tmp)
            (root / "llamadas").mkdir()

            def draft(key):
                return seleccion.phase_call(root, key, "draft", "texto", schema=seleccion.DRAFT_SCHEMA)

            limited = draft("test")
            self.assertEqual(limited, draft(None))
            self.assertTrue(limited["retryable"])
            self.assertEqual(post.call_count, 1)
            completed = draft("test")
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["prior_attempts"], [limited])
            self.assertEqual(completed, draft("test"))
            self.assertEqual(post.call_count, 2)
            totals = seleccion.usage_totals([limited, completed, completed])
            self.assertEqual((totals["requests"], totals["receipts_without_usage"]), (2, 1))


class TextPricingTest(unittest.TestCase):
    def test_luna_threshold_includes_cache_but_decisions_output_is_free(self):
        def receipt(endpoint, tokens):
            return {"model_requested": "gpt-6-luna", "endpoint": endpoint, "usage": {
                "input_tokens": tokens, "output_tokens": 200,
                "input_tokens_details": {"cached_tokens": 200_000, "cache_write_tokens": 60_000}}}

        below = receipt("responses", 272_000)
        response = receipt("responses", 272_001)
        decision = receipt("decisions", 272_001)
        self.assertEqual(seleccion.priced_text_attempt(below), Decimal("0.0108"))
        self.assertEqual(seleccion.priced_text_attempt(response), Decimal("0.0215502"))
        self.assertEqual(seleccion.priced_text_attempt(decision), Decimal("0.0214002"))

    def test_explicit_cache_lifetimes_use_their_own_rates(self):
        usage = api._metadata({"usage": {
            "input_tokens": 12_000, "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 7000,
                                     "cache_write_tokens_details": {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000}}
        }})["usage"]
        receipt = {"model_requested": "gpt-6-luna", "endpoint": "responses", "usage": usage}
        self.assertEqual(seleccion.priced_text_attempt(receipt), Decimal("0.00154"))

    def test_attempts_with_same_fingerprint_are_only_deduplicated_if_copied(self):
        common = {"fingerprint": "same-input", "model_requested": "gpt-6-luna", "endpoint": "responses"}
        first = {**common, "started_at": "first", "usage": {"input_tokens": 1, "output_tokens": 2}}
        second = {**common, "started_at": "second", "usage": {"input_tokens": 3, "output_tokens": 4},
                  "prior_attempts": [first]}
        self.assertEqual(costos_coleccion.priced_text([first, second, second]), Decimal("0.0000034"))


class ImageReceiptsTest(unittest.TestCase):
    class FakeAPIError(Exception):
        status_code = 503
        code = "server_error"
        request_id = "req_test"

    def test_gateway_failure_is_journaled_and_stops_same_run(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            [job] = imagenes.plan({"P0001.png": "prompt"}, folder, dry_run=False)
            client = Mock()
            client.images.generate.side_effect = self.FakeAPIError()
            sdk = SimpleNamespace(APIError=self.FakeAPIError)
            with patch.dict(sys.modules, {"openai": sdk}):
                self.assertFalse(imagenes.submit_image(client, folder, job, Event()))
            client.images.generate.assert_called_once()
            events = list(imagenes.image_attempt_events(folder))
            self.assertEqual([r["event"] for r in events], ["started", "failed"])
            self.assertTrue(imagenes.image_attempt_unconfirmed(events[-1]))
            self.assertFalse(events[-1]["retryable"])

    def test_sample_runner_reuses_its_reservation_after_ambiguous_failure(self):
        sdk = SimpleNamespace(APIError=self.FakeAPIError, OpenAI=MagicMock())
        client = sdk.OpenAI.return_value.__enter__.return_value
        client.images.generate.side_effect = self.FakeAPIError()
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "color"
            with patch.dict(sys.modules, {"openai": sdk}), patch.object(imagenes, "api_key", return_value="test"):
                self.assertEqual(imagenes.run({"sample.png": "prompt"}, folder), 1)
            with patch.dict(sys.modules, {"openai": None}):
                self.assertEqual(imagenes.run({"Sample.png": "prompt"}, folder), 1)
            client.images.generate.assert_called_once()
            self.assertEqual(sdk.OpenAI.call_args.kwargs["max_retries"], 0)
            self.assertEqual(len(list(imagenes.image_attempt_events(folder))), 2)

    def test_sample_runner_does_not_repeat_paid_image_with_invalid_body(self):
        sdk = SimpleNamespace(APIError=self.FakeAPIError, OpenAI=MagicMock())
        client = sdk.OpenAI.return_value.__enter__.return_value
        usage = SimpleNamespace(model_dump=lambda **kwargs: {"input_tokens": 10, "output_tokens": 20})
        client.images.generate.return_value = SimpleNamespace(_request_id="paid", usage=usage, data=[])
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "color"
            jobs = {"sample.png": "prompt"}
            with patch.dict(sys.modules, {"openai": sdk}), patch.object(imagenes, "api_key", return_value="test"):
                self.assertEqual(imagenes.run(jobs, folder), 1)
            with patch.dict(sys.modules, {"openai": None}):
                self.assertEqual(imagenes.run(jobs, folder), 1)
            client.images.generate.assert_called_once()
            self.assertEqual([r["event"] for r in imagenes.image_attempt_events(folder)], ["started", "received", "failed"])

    def test_paid_image_survives_final_rename_failure_and_reopens_offline(self):
        png = png_fixture()
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            jobs = {"P0001.png": "prompt"}
            [(_, path, receipt_path, staged, expected)] = imagenes.plan(jobs, folder, dry_run=False)
            receipt = {**expected, "request_id": "req_test", "sha256": imagenes.png_sha256(png)}
            replace = Path.replace

            def disk_error(source, target):
                if source == staged:
                    raise OSError(errno.ENOSPC, "simulated disk error")
                return replace(source, target)

            with patch.object(Path, "replace", disk_error), patch.object(imagenes.time, "sleep"):
                with self.assertRaises(OSError):
                    imagenes.store_response(path, receipt_path, staged, png, receipt)
            self.assertEqual(staged.read_bytes(), png)
            self.assertEqual(imagenes.plan(jobs, folder, dry_run=False), [])
            self.assertEqual(path.read_bytes(), png)
            self.assertEqual(imagenes.plan(jobs, folder, dry_run=False), [])

    def test_image_and_journal_copies_are_reconciled_once(self):
        usage = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
                 "input_tokens_details": {"text_tokens": 10, "image_tokens": 0, "cached_tokens": 2},
                 "output_tokens_details": {"image_tokens": 20, "text_tokens": 0}}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = root / "muestras"
            (samples / "color").mkdir(parents=True)
            folder = root / "novela" / "imagenes-finales"
            folder.mkdir(parents=True)
            sample = {"request_id": "sample", "usage": usage, "model": imagenes.IMAGE_MODEL}
            (samples / "color" / "novela.png.json").write_text(json.dumps(sample))
            receipt = {**sample, "request_id": "new", "attempt_id": "saved", "origin": "nueva"}
            (folder / "P0001.png.json").write_text(json.dumps(receipt))
            (folder / "P0001.png").write_bytes(png_fixture())
            log = imagenes.journal
            log(folder, "started", "P0001", attempt_id="saved")
            log(folder, "received", "P0001", attempt_id="saved", request_id="new", usage=usage)
            log(folder, "started", "P0002", attempt_id="unknown")
            log(folder, "failed", "P0002", attempt_id="unknown", http_status=503)
            with patch.object(costos_coleccion, "OUT", root), patch.object(costos_coleccion, "SAMPLE_DIR", samples):
                complete, counts = costos_coleccion.collect_images("novela")
            self.assertEqual(complete, {"P0001"})
            self.assertEqual(counts["nuevas_n"], 1)
            self.assertEqual(counts["nuevas_usd"], Decimal("0.0006425"))
            self.assertEqual((counts["intentos_abiertos"], counts["fallos_cargo_incierto"]), (0, 1))

    def test_collection_resumes_offline_and_publishes_recovered_image(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            slug = "novela"
            book = root / slug
            (book / "contenido").mkdir(parents=True)
            samples = root / "muestras"
            (samples / "color").mkdir(parents=True)
            png = png_fixture()
            digest = imagenes.png_sha256(png)
            ids = ("P0001", "P0002")
            paragraphs, picks = [], []
            for page, pid in enumerate(ids, 1):
                motif = f"Una silla junto a la ventana {page}."
                paragraphs.append({"id": pid, "page": page, "text": motif})
                picks.append({"id": pid, "page": page, "title": "La silla", "brief": motif,
                              "core": {"text": motif}, "evidence": [{"id": pid, "quote": motif}],
                              "draft_call": f"draft-{pid}", "decision_call": f"decision-{pid}",
                              "status": "aprobada", "version": "original"})
            corpus = json.dumps({"paragraphs": paragraphs, "segments": [
                {"id": "cap1", "start": ids[0], "end": ids[-1]}]}).encode()
            corpus_hash = sha256(corpus).hexdigest()
            (book / "corpus.json").write_bytes(corpus)
            result = {"book": slug, "corpus_sha256": corpus_hash, "reading_scope": seleccion.PROSPECTIVE_VERSION,
                      "variants": [{"id": "contenido", "picks": picks}]}
            (book / "contenido/resultados.json").write_text(json.dumps(result))
            prompt = produccion_imagen.make_prompt(slug, picks[0])
            sample = {"slug": slug, "cut": ids[0], "image": f"color/{slug}.png",
                      "sha256": digest, "corpus_sha256": corpus_hash,
                      "prompt_version": produccion_imagen.PROMPT_VERSION,
                      "draft_call": picks[0]["draft_call"], "decision_call": picks[0]["decision_call"]}
            (samples / "manifest.json").write_text(json.dumps({"books": [sample]}))
            (samples / "prompts.jsonl").write_text(json.dumps({"out": f"{slug}.png", "prompt": prompt}) + "\n")
            sample_image = samples / sample["image"]
            sample_image.write_bytes(png)
            sample_receipt = {**imagenes.IMAGE_OPTIONS, "file": sample_image.name, "request_id": "first",
                              "usage": None, "prompt_sha256": sha256(prompt.encode()).hexdigest(), "sha256": digest}
            sample_image.with_suffix(".png.json").write_text(json.dumps(sample_receipt))

            sdk = SimpleNamespace(APIError=self.FakeAPIError, OpenAI=MagicMock())
            client = sdk.OpenAI.return_value.__enter__.return_value
            client.images.generate.side_effect = self.FakeAPIError()
            with patch.dict(sys.modules, {"openai": sdk}), patch.object(produccion_imagen, "api_key", return_value="test"):
                self.assertEqual(produccion_imagen.execute([slug], 1, collection=root), 1)
            client.images.generate.assert_called_once()
            self.assertEqual(sdk.OpenAI.call_args.kwargs["max_retries"], 0)

            folder = book / "imagenes-finales"
            # Provider-side recovery may restore an existing response after a transport failure.
            (_, path, receipt_path, stage, expected), = imagenes.plan({
                "P0002.png": produccion_imagen.make_prompt(slug, picks[1])}, folder, dry_run=False)
            with patch.dict(sys.modules, {"openai": None}):
                self.assertEqual(produccion_imagen.execute([slug], 1, collection=root), 1)
                self.assertFalse((folder / "ilustraciones.json").exists())
                attempt = imagenes.previous_attempts(folder)["P0002"]
                receipt = {**expected, "request_id": attempt["request_id"], "attempt_id": attempt["attempt_id"],
                           "usage": None, "sha256": digest, "origin": "nueva"}
                receipt_path.write_text(json.dumps(receipt))
                stage.write_bytes(png)
                self.assertEqual(produccion_imagen.execute([slug], 1, collection=root), 0)
                self.assertEqual(produccion_imagen.execute([slug], 1, collection=root), 0)
            manifest = json.loads((folder / "ilustraciones.json").read_text())
            self.assertEqual(tuple(row["id"] for row in manifest["images"]), ids)
            self.assertEqual(path.read_bytes(), png)
