"""Reciprocal Rank Fusion (RRF) for hybrid vector + BM25 search results.

Combines two ranked lists with configurable weights, following the same
strategy used by OpenClaw's hybrid search backend.

Default weights: vector 70 % + text 30 %.
"""

from __future__ import annotations

# Constant used in the RRF formula to dampen the effect of high ranks.
# A higher value gives more weight to lower-ranked results.
_RRF_K = 60


def hybrid_rank(
    vector_results: list[dict],
    text_results: list[dict],
    vector_weight: float = 0.7,
    text_weight: float = 0.3,
    limit: int = 5,
) -> list[dict]:
    """Fuses vector and BM25 search results using weighted Reciprocal Rank Fusion.

    Each result dict must contain at least an ``"id"`` and ``"content"`` key.
    The returned list is sorted by descending fused score and capped at *limit*.

    The RRF score for a document *d* is::

        score(d) = Σ  weight_i / (k + rank_i(d))

    where *rank* starts at 1 for the top result.  Documents appearing in only
    one list still receive a score from that list.  The fusion is symmetric:
    a document ranked #1 in both lists gets the maximum possible score.

    Parameters
    ----------
    vector_results:
        Ranked list from the vector (cosine similarity) search.
    text_results:
        Ranked list from the BM25 (FTS5) search.
    vector_weight:
        Weight applied to the vector-search rank contribution.
    text_weight:
        Weight applied to the BM25-search rank contribution.
    limit:
        Maximum number of results to return after fusion.

    Returns
    -------
    list[dict]
        Merged results sorted by fused RRF score (highest first).  Each dict
        contains the original fields plus a ``"hybrid_score"`` key.
    """
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    # Score from vector results
    for rank, item in enumerate(vector_results, start=1):
        doc_id = item["id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + vector_weight / (_RRF_K + rank)
        if doc_id not in docs:
            docs[doc_id] = dict(item)

    # Score from text results
    for rank, item in enumerate(text_results, start=1):
        doc_id = item["id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + text_weight / (_RRF_K + rank)
        if doc_id not in docs:
            docs[doc_id] = dict(item)

    # Sort by fused score descending
    ranked_ids = sorted(scores, key=lambda d: scores[d], reverse=True)

    results: list[dict] = []
    for doc_id in ranked_ids[:limit]:
        entry = docs[doc_id]
        entry["hybrid_score"] = scores[doc_id]
        results.append(entry)

    return results
