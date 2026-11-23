"""Paginate narrative units and rank non-overlapping illustration sequences."""

from bisect import bisect_left
from functools import cache

def paginate(paragraphs, target=300):
    pages = [[]]
    used = 0
    for row in paragraphs:
        words = len(row["text"].split())
        if pages[-1] and used + words > target:
            pages.append([])
            used = 0
        pages[-1].append(row["id"])
        used += words
    return pages


INTERVALS = (8, 6, 4)


MIN_GAP, MAX_GAP = 4, 8


def slots(cards, conflicts, total_pages, *, intervals=INTERVALS):
    """Find the best lineup for each count, with a four-page minimum between illustrations."""
    pages = [card["page"] for card in cards]
    after = [
        (1 << len(cards)) - (1 << bisect_left(pages, page))
        for page in range(total_pages + MIN_GAP + 1)
    ]
    masks = [0] * len(cards)
    indices = {card["id"]: i for i, card in enumerate(cards)}
    for pair in conflicts:
        first, second = sorted(pair)
        if first in indices and second in indices:
            left, right = sorted((indices[first], indices[second]))
            masks[left] |= 1 << right

    def optimize(interval):
        @cache
        def best(previous, remaining, available):
            if not remaining:
                return max(0, total_pages - previous - MAX_GAP), 0, 0, ()
            next_page = previous + MIN_GAP if previous else 1
            if next_page + MIN_GAP * (remaining - 1) > total_pages:
                return None

            result = None
            while available:
                bit = available & -available
                available ^= bit
                index = bit.bit_length() - 1
                page = pages[index]
                if page + MIN_GAP * (remaining - 1) > total_pages:
                    break
                tail = best(page, remaining - 1, available & after[page + MIN_GAP] & ~masks[index])
                if tail is None:
                    continue
                excess, negative_value, rhythm_error, later = tail
                gap = page - previous if previous else page - 1
                option = (
                    max(0, gap - MAX_GAP) + excess,
                    negative_value - cards[index]["value"],
                    rhythm_error + ((gap - interval) ** 2 if previous else 0),
                    (index, *later),
                )
                if result is None or option < result:
                    result = option
            return result

        target = (total_pages + interval // 2) // interval
        cap = min(len(set(pages)), (total_pages - 1) // MIN_GAP + 1)
        choices = {n: best(0, n, after[1]) for n in range(cap + 1)}
        choices = {n: choice for n, choice in choices.items() if choice is not None}
        count = min(choices, key=lambda n: (choices[n][0], abs(n - target), *choices[n][1:]))
        best.cache_clear()
        return [cards[i]["id"] for i in choices[count][3]], {
            "target_count": target,
            "actual_count": count,
            "counts_with_no_long_gap": [n for n, choice in choices.items() if not choice[0]],
            "long_gap_excess_by_count": {n: choice[0] for n, choice in choices.items()},
        }

    return {interval: optimize(interval) for interval in intervals}


def cadence(total, picks, plan, banned, conflicts, positions):
    placed = [pick["id"] for pick in picks]
    selected = set(placed)
    edges = {"before_first": None, "after_last": None}
    if placed:
        edges = {"before_first": positions[placed[0]] - 1, "after_last": total - positions[placed[-1]]}
        boundaries = [None, *placed, None]
    else:
        boundaries = [None, None]
    intervals = []
    optimum_excess = plan["long_gap_excess_by_count"][len(placed)]
    for left, right in zip(boundaries, boundaries[1:]):
        start = positions[left] if left else 1
        end = positions[right] if right else total
        distance = total if not left and not right else end - start
        minimum = MIN_GAP if left and right else 0
        interval = {"left": left, "right": right, "pages": distance, "min": minimum, "max": MAX_GAP}
        if not minimum <= distance <= MAX_GAP:
            alternatives = []
            for pid, page in positions.items():
                if pid in selected or not start <= page <= end:
                    continue
                if pid in banned:
                    reason = "no obtuvo aprobación editorial"
                elif (left and page - start < MIN_GAP) or (right and end - page < MIN_GAP):
                    reason = "quedaría demasiado cerca de una inserción contigua"
                elif any(frozenset((pid, other)) in conflicts for other in selected):
                    reason = "repite un aporte de la secuencia"
                else:
                    reason = "insertarla no reduce el exceso total de esta secuencia"
                alternatives.append({"id": pid, "page": page, "reason": reason})
            interval["justification"] = (
                f"El mejor resultado sumó {optimum_excess} pasos por encima de ocho. "
                "La búsqueda consideró todas las fichas aprobadas y las incompatibilidades editoriales."
            )
            interval["candidates"] = alternatives
        intervals.append(interval)

    assert all(item["pages"] >= item["min"] for item in intervals)
    assert sum(max(0, item["pages"] - MAX_GAP) for item in intervals) == optimum_excess
    return {
        **plan,
        "pages": [pick["page"] for pick in picks],
        "edges": edges,
        "intervals": intervals,
        "exceptions": [item for item in intervals if "justification" in item],
        "count_target_met": plan["target_count"] == plan["actual_count"],
        "in_band": all("justification" not in item for item in intervals),
        "count_justification": (
            "La cantidad objetivo se calculó al dividir las páginas por el intervalo nominal "
            "y redondear al entero más cercano. La búsqueda minimiza primero los pasos por encima "
            "de ocho, luego la distancia a la cantidad objetivo. A igual distancia elige el mayor "
            "aporte editorial acumulado y la cadencia más próxima al intervalo nominal."
        ),
    }
