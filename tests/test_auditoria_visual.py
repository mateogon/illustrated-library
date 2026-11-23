from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from zipfile import ZipFile

from PIL import Image
from book_illustrator import auditoria_visual as audit


class VisualGateTest(TestCase):
    def test_quotation_marks_do_not_change_the_cited_words(self):
        self.assertTrue(audit.quote_present("«válvulas abiertas»", "por las válvulas abiertas."))
        self.assertFalse(audit.quote_present("«válvulas cerradas»", "por las válvulas abiertas."))
        self.assertFalse(audit.quote_present("«»", "por las válvulas abiertas."))

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "sample.epub"
        with ZipFile(self.source, "w") as archive:
            archive.writestr("start.xhtml", b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                            b'<p>A shut box. Hidden crystal.</p></body></html>')
        raw = BytesIO()
        Image.new("RGB", (1200, 800), "ivory").save(raw, format="PNG")
        self.png = self.root / "box.png"
        self.png.write_bytes(raw.getvalue())
        self.scene = dict(id="P1", source_xhtml="start.xhtml", xhtml_path=[0], xhtml_tag="p",
                          text_start=0, text_end=11, xhtml_text_length=27, text="A shut box.",
                          title="Closed box", description="A closed box.", file="box.png",
                          sha256=audit.digest(raw.getvalue()))
        self.entry = dict(id="test-en", source_sha256=audit.digest(self.source.read_bytes()))

    def fake_client(self, safe=True):
        observation = {key: "fixture" for key in audit.BLIND["properties"]}
        review = dict.fromkeys(audit.FLAGS, True)
        review.update(spoiler_safe=safe, reason="La caja está cerrada.", source_quote="A shut box", uncertain=False)
        responses = []
        for data in (observation, review):
            answer = Mock(_request_id="simulated", status="completed", model=audit.MODEL,
                          output_text=json.dumps(data))
            answer.usage.model_dump.return_value = {"input_tokens": 100, "output_tokens": 10}
            responses.append(answer)
        client = Mock()
        client.responses.create.side_effect = responses
        return client

    def test_blind_read_then_review_and_byte_bound_export(self):
        client = self.fake_client()
        row, state = audit.review_scene(client, self.root, self.entry, self.source, self.scene)
        self.assertEqual(state, "approved")
        calls = client.responses.create.call_args_list
        blind = calls[0].kwargs["input"][0]["content"]
        self.assertEqual([x["type"] for x in blind], ["input_text", "input_image", "input_image"])
        self.assertNotIn("box", blind[0]["text"])
        inputs = json.loads(calls[1].kwargs["input"][0]["content"][0]["text"])
        self.assertEqual(inputs["YA_LEIDO"], "A shut box.")
        self.assertEqual(inputs["DESPUES"], "Hidden crystal.")
        def validate(scene):
            audit.require_approved(scene, "test-en", self.entry["source_sha256"], self.png, self.root)
        validate(row)
        with self.assertRaises(ValueError):
            validate(dict(row, title="Crystal"))
        audit.review_scene(client, self.root, self.entry, self.source, self.scene)
        self.assertEqual(client.responses.create.call_count, 2)
        localized = dict(self.scene, title="Caja cerrada")
        translated_client = self.fake_client()
        _, translated_review = list(translated_client.responses.create.side_effect)
        translated_client.responses.create.side_effect = [translated_review]
        audit.review_scene(translated_client, self.root, self.entry, self.source, localized)
        self.assertEqual(translated_client.responses.create.call_count, 1)

    def test_translated_citation_recovers_once_and_verifies_original(self):
        client = self.fake_client()
        blind, review = list(client.responses.create.side_effect)
        review.output_text = json.dumps(dict(json.loads(review.output_text), source_quote="Una caja cerrada."))
        citation = Mock(_request_id="simulated-citation", status="completed", model=audit.MODEL,
                        output_text=json.dumps({"source_quote": "A shut box."}))
        citation.usage.model_dump.return_value = {"input_tokens": 50, "output_tokens": 8}
        client.responses.create.side_effect = [blind, review, citation]
        row, state = audit.review_scene(client, self.root, self.entry, self.source, self.scene)
        self.assertEqual(state, "approved")
        receipt = audit.require_approved(row, "test-en", self.entry["source_sha256"], self.png, self.root)
        self.assertEqual(receipt["citation"], "A shut box.")
        self.assertNotIn("Hidden crystal", str(client.responses.create.call_args))
        audit.review_scene(client, self.root, self.entry, self.source, self.scene)
        self.assertEqual(client.responses.create.call_count, 3)

    def test_rejection_and_uncertain_transport_cannot_pass(self):
        client = self.fake_client(safe=False)
        row, state = audit.review_scene(client, self.root, self.entry, self.source, self.scene)
        self.assertEqual(state, "withheld")
        with self.assertRaises(ValueError):
            audit.require_approved(row, "test-en", self.entry["source_sha256"], self.png, self.root)
        changed = dict(self.scene, title="A second candidate")
        client.responses.create.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            audit.review_scene(client, self.root, self.entry, self.source, changed)
        with self.assertRaises(ValueError):
            audit.review_scene(client, self.root, self.entry, self.source, changed)
        self.assertEqual(client.responses.create.call_count, 3)

    @patch.object(audit.time, "sleep")
    def test_only_explicit_rate_limit_is_retried(self, sleep):
        client = self.fake_client()
        rate = RuntimeError("rate limited")
        rate.status_code, rate.code = 429, "rate_limit_exceeded"
        client.responses.create.side_effect = [rate, *client.responses.create.side_effect]
        row, state = audit.review_scene(client, self.root, self.entry, self.source, self.scene)
        self.assertEqual(state, "approved")
        saved = json.loads((self.root / row["visual_review"]).with_name("blind.json").read_bytes())
        self.assertEqual(len(saved["rate_limit_events"]), 1)
        self.assertEqual(client.responses.create.call_count, 3)
        sleep.assert_called_once()
