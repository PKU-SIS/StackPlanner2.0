# ruff: noqa: E402
"""Check CLAUSE local resources, or explicitly download the pinned BERTScore encoder."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcc_harness.clause_metrics import file_hash, protocol, verify_model


def download_encoder(target):
    target = Path(target)
    expected = protocol()["bert"]
    if (target / "MODEL_REVISION.json").is_file():
        return verify_model(target)
    target.mkdir(parents=True, exist_ok=True)
    names = (
        "config.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.json",
        "merges.txt",
        "model.safetensors",
    )
    entries = []
    for name in names:
        url = f"https://huggingface.co/{expected['repo_id']}/resolve/{expected['revision']}/{name}"
        path = target / name
        temporary = path.with_name(name + ".download")
        digest = hashlib.sha256()
        try:
            print(f"Downloading pinned CLAUSE encoder resource: {name}", flush=True)
            with urlopen(url, timeout=60) as response, temporary.open("wb") as out:
                length = response.headers.get("Content-Length")
                size = 0
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    out.write(block)
                    digest.update(block)
                    size += len(block)
            if length and size != int(length):
                raise ValueError("incomplete encoder download")
            # CDN/Xet ETags identify storage objects, not necessarily file SHA256.
            # Compare the weights against the LFS SHA256 pinned at this revision.
            if name == "model.safetensors" and (
                digest.hexdigest() != expected["weights_sha256"]
                or size != expected["weights_size"]
            ):
                raise ValueError(
                    "encoder weights differ from the pinned LFS SHA256/size"
                )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        entries.append({"path": name, "size": size, "sha256": digest.hexdigest()})
    manifest = {
        "repo_id": expected["repo_id"],
        "revision": expected["revision"],
        "files": entries,
    }
    (target / "MODEL_REVISION.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return verify_model(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resource-root", type=Path, default=Path(".benchmark-data/clause/resources")
    )
    parser.add_argument(
        "--download-bert",
        action="store_true",
        help="Explicit network access; downloads about 1.4 GB of encoder weights",
    )
    args = parser.parse_args()
    try:
        for name, expected in protocol()["nltk_files"].items():
            if file_hash(args.resource_root / "nltk_data" / name) != expected:
                raise ValueError(
                    "NLTK resources differ from the pinned bundle; rerun data preparation"
                )
        model = args.resource_root / "models/roberta-large"
        revision = (
            download_encoder(model) if args.download_bert else verify_model(model)
        )
        print(
            json.dumps(
                {"status": "ready", "nltk": "verified", "bert": revision}, indent=2
            )
        )
    except (ValueError, OSError) as exc:
        parser.exit(
            2,
            f"CLAUSE resources not ready: {exc}\nUse --download-bert to explicitly fetch the fixed encoder, or select without_bert for diagnostic metrics only.\n",
        )


if __name__ == "__main__":
    main()
