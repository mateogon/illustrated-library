import json
from pathlib import Path
import unittest
from unittest.mock import patch

from book_illustrator import produccion_imagen, seleccion


def contexto(desenlace):
    textos = ["Llegó al taller.", "El sobre seguía cerrado.", desenlace]
    filas = [dict(id=f"P{i:04d}", text=texto, page=1, chapter="Taller")
             for i, texto in enumerate(textos, 1)]
    return dict(paragraphs=filas, position={p["id"]: i for i, p in enumerate(filas)},
                segment_at={1: "tramo"}, boundaries=[1, 3])


class PruebasDelCorte(unittest.TestCase):
    def solicitudes(self, desenlace):
        ctx = contexto(desenlace)
        propuesta = dict(block="b1", id="b1.1", value=2, motif=desenlace)
        ficha = dict(id="P0002", title="El sobre", brief="Sobre cerrado en la mesa.",
                     contribution="Permite ver el objeto.", evidence=[dict(id="P0002", quote="cerrado")])
        with patch.object(seleccion, "phase_call", return_value={"fingerprint": "local", "status": "sin_red"}) as api:
            seleccion.draft_card(Path("contenido"), None, ctx, "P0002", [propuesta])
            seleccion.audit_card(Path("contenido"), None, ctx, ficha, "audit")
        self.assertEqual(api.call_count, 2)
        return [(call.args[2:], call.kwargs) for call in api.call_args_list]

    def test_dos_desenlaces_mismas_solicitudes_hasta_el_corte(self):
        primera = self.solicitudes("El contenido era un mapa de ámbar.")
        segunda = self.solicitudes("El contenido era una flor de cobre.")
        self.assertEqual(primera, segunda)
        for args, _ in primera:
            mensajes = json.dumps(args, ensure_ascii=False)
            self.assertIn("El sobre seguía cerrado.", mensajes)
            self.assertNotIn("P0003", mensajes)
            self.assertNotIn("ámbar", mensajes)

    def test_cita_literal_futura_rechazada_aunque_exista_en_el_libro(self):
        citas = [dict(id="P0002", quote="cerrado"), dict(id="P0003", quote="mapa de ámbar")]
        validas, invalidas = seleccion.citation_check(citas, contexto("Un mapa de ámbar."), 0, 1, "core")
        self.assertEqual([c["id"] for c in validas], ["P0002"])
        self.assertEqual([c["id"] for c in invalidas], ["P0003"])

    def test_prompt_de_imagen_ignora_metadatos_editoriales_del_desenlace(self):
        ficha = dict(brief="Sobre cerrado sobre la mesa.", core={"text": "Un sobre opaco"})
        limpio = produccion_imagen.make_public_prompt("treasure-island", ficha)
        con_metadatos = produccion_imagen.make_public_prompt("treasure-island", dict(
            ficha, chapter="El mapa de ámbar", title="La flor de cobre",
            evidence=[dict(id="P0003", quote="El mapa de ámbar")],
        ))
        self.assertEqual(limpio, con_metadatos)
        self.assertIn(ficha["brief"], limpio)
        self.assertIn(ficha["core"]["text"], limpio)
