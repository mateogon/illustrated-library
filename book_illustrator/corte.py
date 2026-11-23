"""Build model input from narrative paragraphs available at one reading cut."""

from bisect import bisect_left
from hashlib import sha256
from itertools import accumulate

from book_illustrator.runtime import segmented_input


def prefix_boundaries(paragraphs):
    ends = list(accumulate(len(row["text"].split()) for row in paragraphs))
    return sorted({bisect_left(ends, words) + 1 for words in range(1100, ends[-1] + 1, 2500)})


def reading_prefix(paragraphs, cutoff, boundaries, purpose, *, start=0):
    """Only paragraph IDs and prose cross this boundary. The last row is the cut."""
    if not 0 <= start < cutoff <= len(paragraphs):
        raise ValueError("El corte no corresponde a un prefijo narrativo válido.")
    available = paragraphs[start:cutoff]
    cuts = [boundary - start for boundary in boundaries if start < boundary <= cutoff]
    return segmented_input(available, len(available), cuts, purpose)


def anonymous_cache_key(inputs, stage):
    first = inputs[-1]["content"][0]["text"].split("\n\n", 1)[0]
    return f"reading-cut-{sha256(first.encode()).hexdigest()[:16]}-{stage}"
