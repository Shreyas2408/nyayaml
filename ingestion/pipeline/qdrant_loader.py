"""Load embeddings into Qdrant. Implemented in Phase 1."""
""" Publishing the embedded chunks to qdrant behind a stable,swappable aliasa."""

import argparse
import hashlib, json, os, uuid
from importlib.metadata import version
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import(
    CreateAlias, CreateAliasOperation, DeleteAlias, DeleteAliasOperation,
    Distance,FieldCondition, Filter,MatchValue,
    PayloadSchemaType, PointStruct, VectorParams,
)

from .common import file_hash, read_json, read_jsonl, require, write_json
from .config import OUTPUT_DIR

EMBEDDING_DIM= 768
BATCH_SIZE = 256
LOADER_VERSION= "qdrant-loader-2"

#the single stable name every retrieval query uses, it always points at newest verified build,so
# rebuild never exposes a half-loaded collection to searchtraffic
ALIAS="nyayaml_knowledge"

#fixed namespace: the same chunk always maps to the same point ID, so a rerun
# is idempotent upsert rather than a duplicate insert.

POINT_NAMESPACE= uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/Shreyas2408/nyayaml"
)

#the fields retrieval filters on. qdrant only prunes fast on indexed fields
KEYWORD_INDEXES = ("act", "kind", "citation_id", "json_pointer", "parent_citation_id")

#reconstruct at query time from header +parent_context+text , so storung it
#a second time would only inflate the index
DERIVED_FIELDS = ("embedding_text",)

def build_id(chunks_hash, embeddings_hash):
    """ name each build after its inputs ,so identical inputs
reuse one collections"""
    signature= f"{LOADER_VERSION}|{chunks_hash}|{embeddings_hash}|{EMBEDDING_DIM}"
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]

def point_id(chunk_id):
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))

def payload_for(chunk):
    return {key: value for key, value in chunk.items() if key not in DERIVED_FIELDS}

def prepare(chunks_path, vectors_path, embedding_report_path, chunk_report_path):
    #cross checking all 4 inputs
    embeddings= read_json(embedding_report_path)
    chunking = read_json(chunk_report_path)
    require(
        chunking["chunks_sha256"] == embeddings["chunks_sha256"],
        "the chunker and embedder reports decribe different chunk files"
    )
    require(
        file_hash(chunks_path) == embeddings["chunks_sha256"],
        "chunks.jsonl does not match the embedding report; re-run chunker then embedder.",
    )
    require(
        file_hash(vectors_path) == embeddings["embeddings_sha256"],
        "embeddings.npy does not match the embedding report; re-run the embedder.",
    )
    chunks = read_jsonl(chunks_path)
    vectors= np.load(vectors_path)
    require(vectors.dtype == np.float32, "EMbeddings must be float32")
    require(
        vectors.shape == (len(chunks), EMBEDDING_DIM),
        f"embeddings are {vectors.shape}, expected ({len(chunks)}, {EMBEDDING_DIM})",
    )
    require(len(chunks) == embeddings["count"], "Chunk count differs from the report.")
    ids = [point_id(chunk["chunk_id"]) for chunk in chunks]
    require(len(ids) == len(set(ids)), "Two chunks map to the same point ID.")
    return chunks, vectors, embeddings, chunking

def ensure_collection(client, name):
    #creating a new collection or confirming an existing one has right shape
    if client.collection_exists(name):
        parameters = client.get_collection(name).config.params.vectors
        require(
            parameters.size == EMBEDDING_DIM and parameters.distance == Distance.COSINE,
            f"collection {name} exists with different vector configs"
        )
        print(f"resuing collection {name}: these exact inputs were loaded before")
        return
    client.create_collection(
        name,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    print(f"created collection {name} : {EMBEDDING_DIM} dimensions, cosine")


def payload_indexes(chunks):
    fields= [(field, PayloadSchemaType.KEYWORD) for field in KEYWORD_INDEXES]
    sections = [
        chunk.get("citation_path", {}).get("section_number") for chunk in chunks
    ]
    if sections and all(isinstance(value, int) for value in sections):
        fields.append(("citation_path.section_number", PayloadSchemaType.INTEGER))
    return fields

def create_indexes(client, name, chunks):
    fields= payload_indexes(chunks)
    for field, schema in fields:
        client.create_payload_index(name, field , schema, wait=True)
    return [field for field, _ in fields]

def upload(client, name, chunks, vectors, batch_size):
    points=[
        PointStruct(
            id=point_id(chunk["chunk_id"]),
            vector=vector,
            payload=payload_for(chunk),
        )
        for chunk, vector in zip(chunks, vectors.tolist())
    ]
    for start in range(0, len(points), batch_size):
        batch= points[start: start+batch_size]
        client.upsert(name, batch, wait=(start+ batch_size >= len(points)))
    return len(points)

def verify(client,name, chunks, vectors):
    stored= client.count(name, exact=True).count
    require(stored == len(chunks), f"Qdrant hold{stored} points , expected {len(chunks)}")
    by_act={}
    for act in sorted({chunk["act"] for chunk in chunks}):
        expected= sum(chunk["act"] == act for chunk in chunks)
        found= client.count(
            name,
            count_filter= Filter(must=[FieldCondition(key="act", match=MatchValue(value=act))]), exact=True).count
        require(found == expected, f"{act}: Qdrant holds {found}, expected {expected}.")
        by_act[act]= found

    probes= sorted({0,len(chunks) //2, len(chunks)-1})
    probe_ids = {point_id(chunks[index]["chunk_id"]): index for index in probes}
    records = client.retrieve(name, ids=list(probe_ids), with_vectors=True)
    require(
        len(records) == len(probe_ids), "A sampled point is missing from the collection."
    )
    for record in records:
        require(
            np.allclose(
                np.asarray(record.vector, dtype=np.float32),
                vectors[probe_ids[record.id]],
                atol=1e-6,
            ),
            f"Point {record.id} carries a different vector than embeddings.npy.",
        )
    return stored, by_act

        
def publish_alias(client, alias, target):
    # move the alias onto the new build in one atomic swap
    current={item.alias_name: item.collection_name for item in client.get_aliases().aliases}
    if current.get(alias)== target:
        return target
    operations=[]
    if alias in current:
        operations.append(
            DeleteAliasOperation(delete_alias=DeleteAlias(alias_name= alias))
        )
    operations.append(
        CreateAliasOperation(
            create_alias=CreateAlias(alias_name=alias, collection_name= target)
        )
    )
    client.update_collection_aliases(change_aliases_operations=operations)
    published = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
    require(published.get(alias) == target, f"Alias {alias} did not move {target}")
    return published[alias]

def main():
    parser= argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, default=OUTPUT_DIR / "chunks.jsonl")
    parser.add_argument("--vectors", type=Path, default=OUTPUT_DIR / "embeddings.npy")
    parser.add_argument("--embedding-report", type=Path, default=OUTPUT_DIR / "embedding_report.json")
    parser.add_argument(
        "--chunk-report", type=Path, default=OUTPUT_DIR / "chunk_report.json"
    )
    parser.add_argument(
        "--url", default=os.environ.get("QDRANT_URL", "http://localhost:6333")
    )
    parser.add_argument(
        "--local-path",
        type=Path,
        help="Embedded local database for tests. Skips payload indexes and aliases.",
    )
    parser.add_argument("--alias", default=ALIAS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the inputs and exit without contacting Qdrant.",
    )
    parser.add_argument(
        "--report", type=Path, default=OUTPUT_DIR / "qdrant_load_report.json"
    )
    args = parser.parse_args()

    chunks, vectors,embeddings, chunking= prepare(
        args.chunks, args.vectors, args.embedding_report, args.chunk_report
    )
    identity= build_id(embeddings["chunks_sha256"], embeddings["embeddings_sha256"])
    collection = f"{args.alias}_{identity}"
    print(
        f"Validated {len(chunks)} chunks and {EMBEDDING_DIM}-dimensional normalized"
        f"vectors (build {identity})."
    )
    print(f"Target collection: {collection}")
    if args.check_only:
        print(
            json.dumps(
                {
                    "status": "input_check_passed",
                    "build_id": identity,
                    "collection": collection,
                    "count": len(chunks),
                    "chunks_sha256": embeddings["chunks_sha256"],
                    "embeddings_sha256": embeddings["embeddings_sha256"],
                    "canonical_data_sha256": chunking["canonical_data_sha256"],
                },
                indent=2,
            )
        )
        return

    if args.local_path:
        client = QdrantClient(path=str(args.local_path))
    else:
        # TODO: for a protected Qdrant server, set QDRANT_API_KEY in the environment.
        client = QdrantClient(
            url=args.url, api_key=os.environ.get("QDRANT_API_KEY"), timeout=60
        )
    try:
        ensure_collection(client, collection)
        indexed = [] if args.local_path else create_indexes(client, collection, chunks)
        points = upload(client, collection, chunks, vectors, args.batch_size)
        stored, by_act = verify(client, collection, chunks, vectors)
        if args.local_path:
            # The embedded store is for tests; retrieval always goes through the alias.
            print("Local mode: alias publication skipped.")
            live = None
        else:
            live = publish_alias(client, args.alias, collection)
            print(f"Alias {args.alias} now serves {live}.")
        retained = sorted(
            item.name
            for item in client.get_collections().collections
            if item.name != collection
        )
        summary = {
            "status": "passed",
            "loader_version": LOADER_VERSION,
            "build_id": identity,
            "alias": args.alias,
            "collection": collection,
            "endpoint": str(args.local_path) if args.local_path else args.url,
            "points": stored,
            "counts_by_act": by_act,
            "embedding_dim": EMBEDDING_DIM,
            "distance": "cosine",
            "payload_indexes_created": indexed,
            "canonical_data_sha256": chunking["canonical_data_sha256"],
            "chunks_sha256": embeddings["chunks_sha256"],
            "embeddings_sha256": embeddings["embeddings_sha256"],
            "qdrant_client_version": version("qdrant-client"),
            "retained_collections": retained,
            "verified": "Total count, per-act counts and sampled point IDs and vectors checked.",
        }
    finally:
        client.close()
    write_json(args.report, summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()