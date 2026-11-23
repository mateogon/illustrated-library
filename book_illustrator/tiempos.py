"""Estimate the remaining validation time once detection has found the cuts."""

from math import ceil

from book_illustrator.runtime import read_json


def has_later_receipts(root, detection_names, *, inherited=False):
    for path in (root / "llamadas").glob("*.json"):
        if path.name in detection_names:
            continue
        try:
            receipt = read_json(path)
        except (OSError, ValueError):
            return True
        if not isinstance(receipt, dict):
            return True
        phase = receipt.get("phase")
        if inherited:
            if phase == "draft" and receipt.get("status") == "completed":
                return True
        elif phase != "detect":
            return True
    return False


def remaining_seconds(cuts, workers, content_mode=False):
    if not cuts:
        return 0, 0

    def waves(jobs):
        # stage() warms the longest prefix before dispatching the rest.
        return 1 + ceil((jobs - 1) / workers)

    # 148 drafts and 139 audits from the timing benchmark: drafts averaged 10.5 s
    # (p90 13.8 s), audits averaged about 1 s (p90 below 2 s).
    draft_waves = waves(cuts)
    low = draft_waves * 10 + waves(round(cuts * 0.94))
    # Allow 8 s for slower calls at the end of a stage and 30 s for focal
    # recovery, which took up to 29 s including local selection.
    high = draft_waves * 11 + waves(cuts) * 2 + 8 + 30
    if content_mode:
        high += 25
    return low, high


def detection_summary(root, cuts, workers, detection_receipts, *, cache_only):
    summary = f"Detección terminada. {cuts} cortes únicos para fichas."
    if cache_only:
        return f"{summary} Reconstrucción local con recibos."
    if not cuts:
        return f"{summary} Queda evaluar si procede la recuperación focal."
    content_mode = root.name == "contenido"
    detection_names = {f"{receipt['fingerprint']}.json" for receipt in detection_receipts}
    peer = root.parent if content_mode else root / "contenido"
    if has_later_receipts(root, detection_names) or has_later_receipts(peer, detection_names, inherited=True):
        return f"{summary} Hay recibos posteriores a la detección que podrían reutilizarse."
    low, high = remaining_seconds(cuts, workers, content_mode)
    return (f"{summary} Quedan aproximadamente {low}–{high} s con {workers} workers para trabajo nuevo. "
            "El tiempo depende de la latencia y de cuántos huecos necesiten una búsqueda.")
