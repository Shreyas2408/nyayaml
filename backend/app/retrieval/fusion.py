""" reciprocal rank fusion of the dense and sparse result lists"""

from ingestion.pipeline.common import require

RRF_K = 60

def reciprocal_rank_fusion(result_lists, top_k):
    """ fuse ranked hit lists into one ranking, deduplicated by chunk_id
    RRF uses only position, never raw scores, so a cosine similarity and a BM25
    never have to be made comparable.
    """
    require(bool(result_lists), "No result lists to fuse.")
    require(type(top_k) is int and top_k >0, "top_k must be a positive integer.")
    fused ={}
    for hits in result_lists:
        for rank, hit in enumerate(hits, start=1):
            entry = fused.get(hit["chunk_id"])
            if entry is None:
                entry ={
                    "chunk_id": hit["chunk_id"],
                    "payload": hit["payload"],
                    "sources": [],
                    "rrf_score": 0.0,
                }
                fused[hit["chunk_id"]] = entry
            else:
                require(
                    entry["payload"] == hit["payload"],
                    f"The retrievers disagree on {hit['chunk_id']}",
                )
            entry["rrf_score"] += 1.0/(RRF_K + rank)
            if hit["source"] not in entry["sources"]:
                entry["sources"].append(hit["source"])
        
    ranking = sorted(fused.values(), key=lambda entry: (-entry["rrf_score"], entry["chunk_id"]))
    return ranking[:top_k]

