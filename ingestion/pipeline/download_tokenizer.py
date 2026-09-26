"""Download only the pinned BGE tokenizer, not the embedding model weights."""

import argparse
import hashlib
import urllib.request
from pathlib import Path

from .common import require
from .config import PROJECT

REVISION = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
SHA256 = "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"
TOKENIZER_PATH = PROJECT / "data" / "tokenizer" / "tokenizer.json"
URL = f"https://huggingface.co/BAAI/bge-base-en-v1.5/resolve/{REVISION}/tokenizer.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=TOKENIZER_PATH)
    args = parser.parse_args()
    with urllib.request.urlopen(URL, timeout=60) as response:
        content = response.read()
    require(
        hashlib.sha256(content).hexdigest() == SHA256,
        "Tokenizer download does not match the pinned file.",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(args.output)


if __name__ == "__main__":
    main()
