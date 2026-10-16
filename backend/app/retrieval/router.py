""" HTTP wrapper around HybridRetriever. no llm"""

from __future__ import annotations

import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .hybrid import HybridRetriever

try:
    from qdrant_client.http.exceptions import ResponseHandlingException
except ImportError:
    ResponseHandlingException = Exception

router = APIRouter()

_lock = threading.Lock()
_retriever: HybridRetriever | None=None

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    act: Optional[Literal["BNS", "BNSS", "BSA"]] = None
    top_k: int = Field(10, ge=1, le=50)

def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        with _lock:
            if _retriever is None:
                _retriever = HybridRetriever.standard()
    return _retriever

def close_retriever() -> None:
    global _retriever
    with _lock:
        if _retriever is not None:
            close = getattr(_retriever, "close", None)
            if callable(close):
                close()
            _retriever = None

@router.post("/search")
def search(body: SearchRequest, retriever: HybridRetriever = Depends(get_retriever)):
    try:
        return retriever.search(query=body.query, top_k=body.top_k, act=body.act)
    except ResponseHandlingException as exc:
        raise HTTPException(status_code=503, detail="Qdrant unavailable") from exc
    except (ConnectionError, OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="Qdrant unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc