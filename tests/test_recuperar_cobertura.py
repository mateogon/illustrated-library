from importlib import import_module
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from book_illustrator.render_prompt import PUBLIC_PROMPT_VERSION


with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1] / "demo-publica/lote"), *sys.path]):
    recovery = import_module("recuperar_cobertura")


class CoverageRecoveryTest(unittest.TestCase):
    def test_uses_one_distinct_approved_candidate_per_gap_with_cap(self):
        def pick(pid, page, motif, *, evidence=None, status="aprobada"):
            return {"id": pid, "page": page, "core": {"text": motif}, "brief": motif,
                    "chapter": 1, "source_proposals": [pid], "status": status,
                    "evidence": [{"id": evidence or pid}], "scores": {"aporte": 0.95}, "value": 3}

        base = [pick("P0001", 3, "Un puente se curva sobre el río"),
                pick("P0002", 13, "Clérigo cuenta sus panes en un arcaz"),
                pick("P0003", 23, "Un tambor cuelga junto al blasón"),
                pick("P0004", 36, "Una estatua de piedra junto al muro")]
        pool = [*base, pick("P0005", 12, "El clérigo revisa el arcaz abierto"),
                pick("P0006", 13, "Un gato salta hacia la silla"),
                pick("P0007", 14, "Un balcón con flores", evidence="P0003"),
                pick("P0008", 15, "Una bota reluce junto al portón", status="rechazada"),
                pick("P0009", 18, "Una garza dobla el cuello"),
                pick("P0010", 28, "Una vasija rota"),
                pick("P0011", 29, "Un martillo en el suelo")]
        paragraphs = {p["id"]: {"source_xhtml": "texto.xhtml", "xhtml_path": [i], "text_start": 0, "text_end": 30}
                      for i, p in enumerate(pool)}
        result = {"variants": [{"id": "contenido"}], "cards": [], "duplicates": [], "pages": 36}
        with patch.object(recovery.seleccion, "variant_pool", return_value=pool):
            trials = recovery.choose(result, paragraphs, [base[0], base[2], base[3]], base,
                                     {p["id"] for p in base}, 1)
        self.assertEqual([(t["pages"], t["cut"], t.get("reason")) for t in trials],
                         [(20, "P0006", None), (13, None, "limite")])

    def test_audit_uses_catalog_source_hash_and_keeps_the_first_verdict(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            (folder / "P0001.png").write_bytes(b"fixture")
            bound = "a" * 64
            edition = {"id": "ejemplo", "sha256": "original-sha"}
            scene = {"id": "P0001", "file": "P0001.png"}
            verdict = folder / "auditoria-visual" / bound / "dictamen.json"

            def fake_review(client, base, entry, source, rendered):
                self.assertEqual(entry, {"id": "ejemplo", "source_sha256": "original-sha"})
                self.assertEqual((base, rendered), (folder, scene))
                verdict.parent.mkdir(parents=True)
                verdict.write_text('{"status":"withheld"}')
                return scene, "withheld"

            client = Mock()
            with patch.object(recovery.visual, "binding", return_value=bound), \
                    patch.object(recovery.visual, "review_scene", side_effect=fake_review) as judge, \
                    patch.object(recovery, "review_files"):
                first, error = recovery.audit_trial(folder, edition, Path("book.epub"), scene, client)
                self.assertIsNone(error)
                replayed, _ = recovery.audit_trial(folder, edition, Path("book.epub"), scene, client)
                self.assertEqual(replayed["status"], "withheld")
                self.assertEqual(replayed, first)
                judge.assert_called_once()
                client.assert_called_once()

    def test_editorial_revision_changes_only_unsubmitted_trials(self):
        first, second = {"cut": "P0001"}, {"cut": "P0002"}
        with TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            before = {"id": "ejemplo", "limit": 2, "trials": [first, second]}
            later = {**before, "editorial_overlaps": [{"cut": "P0002"}], "trials": [first, {"cut": "P0003"}]}
            recovery.imagenes.journal(folder, "started", "P0001", attempt_id="first")
            recovery.update_plan(folder, before, later)
            self.assertEqual(recovery.read(folder / "plan.json"), later)
            self.assertEqual(recovery.read(next(folder.glob("plan-*.json"))), before)
            with self.assertRaises(ValueError):
                recovery.update_plan(folder, later, {**later, "editorial_overlaps": [{"cut": "P0001"}],
                                                     "trials": [second, {"cut": "P0003"}]})
            self.assertEqual(recovery.read(folder / "plan.json"), later)

    def test_started_render_is_never_repeated_on_resume(self):
        edition = {"work_id": "lazarillo"}
        pick = {"id": "P0001", "core": {"text": "Una ventana abierta."}, "brief": "Una ventana abierta."}
        version = PUBLIC_PROMPT_VERSION
        prompt = recovery.make_public_prompt(edition["work_id"], pick, version=version)
        trial = {"pick": pick, "cut": pick["id"], "prompt_sha256": recovery.digest(prompt.encode())}
        with TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            recovery.imagenes.journal(folder, "started", "P0001", attempt_id="first")
            client = Mock()
            image, scene, failure = recovery.render_trial(folder, edition, version, trial, {}, client)
            self.assertIsNone(image)
            self.assertIsNone(scene)
            self.assertEqual(failure["attempt"]["event"], "started")
            self.assertEqual(recovery.costs(folder, [])["without_usage"], {"render": 1, "visual": 0})
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
