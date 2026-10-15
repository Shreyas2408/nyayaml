"""BGE-base embedding generation. Implemented in Phase 1."""
# now embed each chunk with BGE-base-en_-v1.5 model

import argparse
import json
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer

from .common import file_hash, read_json, require, write_json
from .config import OUTPUT_DIR
from .download_tokenizer import REVISION #resuse same pinned model snapshot

MODEL_NAME= "BAAI/bge-base-en-v1.5"
EMBEDDING_DIM= 768
BATCH_SIZE= 64
EMBEDDER_VERSION= "bge-base-en-v1.5-1"

def read_chunks(path):
    chunks=[]
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                chunks.append(json.loads(line))
    require(bool(chunks), "NO chunks to embed.")
    return chunks

def main():
    parser= argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, default=OUTPUT_DIR/ "chunks.jsonl")
    parser.add_argument("--report", type=Path, default=OUTPUT_DIR/ "chunk_report.json")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--device", default="cpu", help="cpu,cuda, or torch device")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args= parser.parse_args()

    report = read_json(args.report)
    require(
        file_hash(args.chunks) == report["chunks_sha256"],
        "chunks.jsonl does not match chunk_report.json: re run the first chunker."
        )
    chunks = read_chunks(args.chunks)
    require(len(chunks) == report["chunks"], "chunk count differs from the report")

    model = SentenceTransformer(MODEL_NAME, revision=REVISION, device=args.device)
    model.max_seq_length= 512
    require(
        model.get_sentence_embedding_dimension() == EMBEDDING_DIM, "UNEXPECTED embedding dimension for BGE-bbase-en-v1.5",
    )

    vectors = model.encode(
        [chunk["embedding_text"] for chunk in chunks],
        batch_size= args.batch_size,
        normalize_embeddings=True, #unit vectors ,so Qdrant uses cosine distance
        convert_to_numpy=True,
        show_progress_bar=True,
    ).astype("float32")
    require(
        vectors.shape == (len(chunks), EMBEDDING_DIM), "Embedding matrix shape does not match chunk count",
    )

    #row i of embeddings.npy is chunk i of chunks.jsonl , the loader zips them in order
    args.output_dir.mkdir(parents=True, exist_ok=True)
    vector_path = args.output_dir / "embeddings.npy"
    np.save(vector_path, vectors)
    summary = {
        "embedder_version": EMBEDDER_VERSION,
        "model": MODEL_NAME,
        "model_revision": REVISION,
        "embedding_dim": EMBEDDING_DIM,
        "normalized": True,
        "count": len(chunks),
        "chunks_sha256": report["chunks_sha256"],
        "embeddings_sha256": file_hash(vector_path),
    }
    write_json(args.output_dir / "embedding_report.json", summary)
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()