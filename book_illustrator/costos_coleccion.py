"""Reconcile API costs from image and text receipts."""

from collections import Counter, defaultdict
from decimal import Decimal

from book_illustrator.coleccion import OUT
from book_illustrator.imagenes import IMAGE_MODEL, image_attempt_events, image_attempt_unconfirmed
from book_illustrator.seleccion import priced_text_attempt, text_attempts
from book_illustrator.runtime import read_json


MILLION = Decimal(1_000_000)
SAMPLE_DIR = OUT / "muestras"


def priced_image(receipt):
    usage = receipt["usage"]
    if receipt["model"] != IMAGE_MODEL or not usage:
        raise ValueError(f"Faltan modelo o consumo de imagen verificables: {receipt.get('request_id')}")
    ins, outs = usage["input_tokens_details"], usage["output_tokens_details"]
    text, image = ins["text_tokens"], outs["image_tokens"]
    cached_text = ins.get("cached_tokens", 0)
    if (ins["image_tokens"] or outs["text_tokens"] or text != usage["input_tokens"]
            or image != usage["output_tokens"] or not 0 <= cached_text <= text or image < 0
            or usage["total_tokens"] != text + image):
        raise ValueError(f"Modalidades o tokens inesperados: {receipt.get('request_id')}")
    return ((text - cached_text) * 5 + cached_text * Decimal("1.25") + image * 30) / MILLION


def priced_text(receipts):
    return sum(map(priced_text_attempt, text_attempts(receipts)), Decimal(0))


def collect_images(slug):
    # A sample can have identical copies in the preview, the edition, and the event log.
    ids = {}
    counts = Counter()

    def record(row, category):
        request = row.get("request_id")
        if not request and row.get("attempt_id"):
            request = ("intento", row["attempt_id"])
        if not request:
            raise ValueError(f"{slug}: falta el ID de solicitud")
        if request in ids:
            previous, prior_category = ids[request]
            previous_usage = previous.get("usage")
            if row.get("usage") and previous_usage and row["usage"] != previous_usage:
                raise ValueError(f"{slug}: dos recibos con consumo distinto para {request}")
            if row.get("usage") and not previous_usage:
                counts["imagenes_sin_uso"] -= 1
                counts[prior_category + "_usd"] += priced_image(row)
                ids[request] = row, prior_category
            return
        ids[request] = row, category
        counts[category + "_n"] += 1
        if row.get("usage"):
            counts[category + "_usd"] += priced_image(row)
        else:
            counts["imagenes_sin_uso"] += 1

    preview = read_json(SAMPLE_DIR / "color" / (slug + ".png.json"))
    record(preview, "muestras")
    for path in sorted((SAMPLE_DIR / "revision").rglob(slug + ".png.json")):
        record(read_json(path), "revisiones")
    folder = OUT / slug / "imagenes-finales"
    completed = set()
    stored = set()
    for path in sorted(folder.glob("P*.png.json")):
        row = read_json(path)
        if row["origin"] == "muestra" and row["request_id"] != preview["request_id"]:
            raise ValueError(f"{slug}: no coincide el recibo de la primera imagen")
        record(row, "nuevas")
        if (path.parent / path.stem).is_file():
            completed.add(path.name.split(".")[0])
            if row.get("attempt_id"):
                stored.add(row["attempt_id"])

    events = defaultdict(list)
    for row in image_attempt_events(folder):
        if row["event"] not in {"started", "received", "failed", "completed"}:
            raise ValueError(f"{slug}: evento de generación desconocido {row['event']}")
        events[row["attempt_id"]].append(row)
        if row.get("usage") or row["event"] in {"received", "completed"}:
            record({**row, "model": row.get("model", IMAGE_MODEL)}, "nuevas")

    for attempt, history in events.items():
        row = history[-1]
        if row["event"] in {"started", "received"} and attempt not in stored:
            counts["intentos_abiertos"] += 1
        if row["event"] == "failed":
            counts["fallos_imagen"] += 1
            if row.get("code") == "moderation_blocked":
                counts["rechazos_contenido"] += 1
            if not any(item.get("usage") or item["event"] == "received" for item in history):
                if image_attempt_unconfirmed(row) and attempt not in stored:
                    counts["fallos_cargo_incierto"] += 1
                if row.get("request_id") not in ids and attempt not in stored:
                    counts["imagenes_sin_uso"] += 1
    return completed, counts
