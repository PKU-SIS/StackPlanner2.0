"""RAGFlow REST adapter, resumable indexing ledger, and sample-scoped retrieval.

API contract: https://github.com/infiniflow/ragflow/blob/v0.22.1/docs/references/http_api_reference.md
Dataset creation options are passed explicitly, allowing newer deployment APIs
to select parser_id/parse_type without changing the preprocessing pipeline.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from pathlib import Path
from threading import Lock, local
from urllib.parse import urlsplit

from .loaders import inside
from .pipeline import atomic_json, digest, encoded, verified_samples


class RagflowError(RuntimeError):
    """An infrastructure error; never a negative auditing prediction."""


class RagflowClient:
    def __init__(self, config: dict):
        import requests

        self.base_url = config["base_url"].rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("RAGFlow base_url must be an HTTP(S) endpoint without credentials, query, or fragment")
        if parsed.path.endswith("/user-setting/api"):
            raise ValueError("base_url is the RAGFlow origin, not the /user-setting/api settings page")
        self.base_url = self.base_url.removesuffix("/api/v1")
        env_name = config.get("api_key_env", "RAGFLOW_API_KEY")
        key_file = config.get("api_key_file") or os.environ.get(config.get("api_key_file_env", "RAGFLOW_API_KEY_FILE"))
        self._api_key = os.environ.get(env_name, "").strip()
        if not self._api_key and key_file:
            self._api_key = Path(key_file).read_text().strip()
        if not self._api_key:
            raise RagflowError(f"configure {env_name} or RAGFLOW_API_KEY_FILE (or api_key_file); no key is included in public artifacts")
        self.timeout = float(config.get("http_timeout", 60))
        if self.timeout <= 0:
            raise ValueError("http_timeout must be positive")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + self._api_key})
        self._dataset_inventory: dict[str, list[dict]] | None = None

    def _request(self, method: str, path: str, **kwargs):
        import requests

        # Only reads are retried automatically. Ambiguous create/upload failures
        # are reconciled by deterministic names when the command is resumed.
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(method, self.base_url + "/api/v1" + path, timeout=self.timeout, allow_redirects=False, **kwargs)
            except requests.RequestException:
                if attempt + 1 < attempts:
                    time.sleep(0.5 * 2**attempt)
                    continue
                raise RagflowError(f"RAGFlow {method} {path}: connection/timeout failure; resume to reconcile uncertain writes") from None
            if response.status_code in {429, 502, 503, 504} and attempt + 1 < attempts:
                time.sleep(0.5 * 2**attempt)
                continue
            try:
                value = response.json()
            except ValueError:
                raise RagflowError(f"RAGFlow {method} {path}: HTTP {response.status_code}, non-JSON response") from None
            if not isinstance(value, dict) or not 200 <= response.status_code < 300 or value.get("code") != 0:
                code = value.get("code") if isinstance(value, dict) else "invalid response"
                message = str(value.get("message", "")) if isinstance(value, dict) else ""
                message = message.replace(self._api_key, "[redacted]")[:500]
                raise RagflowError(f"RAGFlow {method} {path}: HTTP {response.status_code}, code {code}: {message}")
            return value.get("data")
        raise AssertionError("unreachable")

    def ensure_dataset(self, name: str, options: dict) -> str:
        if "name" in options:
            raise ValueError("dataset_options must not override the managed dataset name")
        if self._dataset_inventory is None:
            inventory, page = defaultdict(list), 1
            while True:
                # Some deployments reject a nonexistent name as a permission
                # error. List accessible libraries and match locally. Load this
                # inventory once per run rather than once per sample/library.
                data = self._request("GET", "/datasets", params={"page": page, "page_size": 100})
                rows = data if isinstance(data, list) else (data or {}).get("datasets", [])
                for row in rows:
                    inventory[row["name"]].append(row)
                if len(rows) < 100:
                    break
                page += 1
            self._dataset_inventory = inventory
        matches = self._dataset_inventory.get(name, [])
        if len(matches) > 1:
            raise RagflowError(f"multiple datasets have the managed name {name}; resolve the ambiguity before resuming")
        if matches:
            return matches[0]["id"]
        try:
            data = self._request("POST", "/datasets", json={"name": name, **options})
        except RagflowError:
            self._dataset_inventory = None  # Reconcile an uncertain create on resume.
            raise
        if not isinstance(data, dict) or not data.get("id"):
            raise RagflowError("dataset creation did not return an id")
        self._dataset_inventory[name] = [{**data, "name": name}]
        return data["id"]

    def list_documents(self, dataset_id: str) -> list[dict]:
        documents, page = [], 1
        while True:
            data = self._request("GET", f"/datasets/{dataset_id}/documents", params={"page": page, "page_size": 100})
            rows = data if isinstance(data, list) else (data or {}).get("docs", [])
            documents.extend(rows)
            if len(rows) < 100:
                return documents
            page += 1

    def upload_document(self, dataset_id: str, path: Path) -> str:
        with path.open("rb") as handle:
            data = self._request("POST", f"/datasets/{dataset_id}/documents", files={"file": (path.name, handle)})
        if not isinstance(data, list) or len(data) != 1 or not data[0].get("id"):
            raise RagflowError("document upload did not return exactly one document id")
        return data[0]["id"]

    def parse_documents(self, dataset_id: str, document_ids: list[str]) -> None:
        self._request("POST", f"/datasets/{dataset_id}/chunks", json={"document_ids": document_ids})

    def retrieve(self, payload: dict) -> dict:
        data = self._request("POST", "/retrieval", json=payload)
        if not isinstance(data, dict) or not isinstance(data.get("chunks"), list):
            raise RagflowError("retrieval did not return a chunks list")
        return data

    def embedding_models(self) -> list[dict]:
        """RAGFlow 0.26+ model readiness probe, excluding provider secrets."""
        data = self._request("GET", "/models", params={"type": "embedding"})
        rows = data if isinstance(data, list) else (data or {}).get("models", [])
        models = []
        for model in rows:
            types = model.get("model_type", [])
            types = [types] if isinstance(types, str) else types
            if "embedding" not in types or model.get("enable") is False:
                continue
            models.append(
                {
                    key: model[key]
                    for key in ("name", "model_name", "model_provider", "provider_name", "model_instance", "instance_name", "model_type")
                    if key in model
                }
            )
        return models


def _status(document: dict) -> str:
    value = str(document.get("run", "UNSTART")).upper()
    return {"0": "UNSTART", "1": "RUNNING", "2": "CANCEL", "3": "DONE", "4": "FAIL"}.get(value, value)


def index_identity(manifest: dict, config: dict) -> dict:
    scope = config.get("knowledge_base_scope", "sample")
    if scope != "sample":
        raise ValueError("knowledge_base_scope must be 'sample'; independent samples cannot share a knowledge base")
    return {
        "public_sha256": manifest["public_sha256"],
        "base_url": config["base_url"].rstrip("/").removesuffix("/api/v1"),
        "dataset_prefix": config.get("dataset_prefix", "dcc"),
        "dataset_options": config.get("dataset_options", {}),
        "knowledge_base_scope": scope,
    }


def validate_sample_scopes(state: dict, sample_ids: list[str]) -> None:
    if state.get("schema_version") != 2 or state.get("knowledge_base_scope") != "sample":
        raise RagflowError("index does not use dedicated sample knowledge bases; create a new bundle")
    owners = {}
    for sample_id in sample_ids:
        scope = state.get("scopes", {}).get(sample_id, {})
        datasets, documents = scope.get("dataset_ids"), scope.get("document_ids")
        if not isinstance(datasets, list) or len(datasets) != 1 or not isinstance(datasets[0], str) or not datasets[0]:
            raise RagflowError(f"sample {sample_id} must have exactly one dedicated knowledge base")
        if not isinstance(documents, list) or not documents or not all(isinstance(value, str) and value for value in documents):
            raise RagflowError(f"sample {sample_id} has an empty document scope")
        dataset_id = datasets[0]
        if dataset_id in owners:
            raise RagflowError(f"samples {owners[dataset_id]} and {sample_id} share a knowledge base")
        owners[dataset_id] = sample_id


def index_bundle(bundle: Path, config: dict, *, client=None, retry_failed: bool = False) -> dict:
    """Upload only public document assets and wait for searchable chunks.

    One deterministic knowledge base per sample. Its related documents may share
    that base; independent samples never do, even with identical document bytes.
    Exact document-id allowlists remain an additional retrieval check.
    Re-run with the same bundle/configuration to resume. Mutating commands for
    the same bundle must be serialized by the CLI's file lock.
    """
    bundle = Path(bundle).resolve()
    manifest, samples = verified_samples(bundle)
    if manifest.get("document_mode") == "inline":
        raise ValueError("inline documents do not require an index; run render directly")
    identity = index_identity(manifest, config)
    configuration_sha256 = digest(encoded(identity))
    path = bundle / "index.json"
    state = (
        json.loads(path.read_text())
        if path.exists()
        else {"schema_version": 2, **identity, "configuration_sha256": configuration_sha256, "datasets": {}, "scopes": {}, "ready": False}
    )
    if state.get("schema_version") != 2:
        raise ValueError("legacy shared knowledge-base ledger cannot resume; create a new bundle for sample isolation")
    if state["configuration_sha256"] != configuration_sha256:
        raise ValueError("index configuration or input changed; create a new bundle instead of adopting unrelated remote data")
    interval, timeout = (float(config.get("poll_interval", 2)), float(config.get("parse_timeout", 900)))
    batch_size = int(config.get("parse_batch_size", 32))
    if interval < 0 or timeout <= 0 or not 1 <= batch_size <= 256:
        raise ValueError("invalid polling/parse configuration")
    workers = config.get("index_workers", 1)
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("index_workers must be an integer between 1 and 16")
    client = client or RagflowClient(config)
    if client.base_url != identity["base_url"]:
        raise ValueError("client base_url differs from index configuration")
    if config.get("check_embedding_models", False) and not client.embedding_models():
        raise RagflowError("no enabled embedding model is configured for this RAGFlow account; configure one in Model providers before indexing")
    state["ready"] = False
    atomic_json(path, state)
    lock, thread_state, sessions = Lock(), local(), []

    def index_sample(item, active_client):
        dataset, sample_id = item["dataset"], item["id"]
        suffix = digest(encoded({"configuration": configuration_sha256, "dataset": dataset, "sample_id": sample_id, "split": item["split"]}))[:24]
        name_prefix = re.sub(r"[^A-Za-z0-9_-]", "-", f"{identity['dataset_prefix']}-{dataset}-{sample_id}")[:90]
        managed_name = name_prefix + "-" + suffix
        dataset_id = active_client.ensure_dataset(managed_name, identity["dataset_options"])
        with lock:
            previous = deepcopy(state["datasets"].get(sample_id))
        if previous and previous["id"] != dataset_id:
            raise RagflowError("managed dataset id changed remotely; inspect the index ledger before continuing")
        ledger = previous or {
            "id": dataset_id,
            "name": managed_name,
            "sample_id": sample_id,
            "source_dataset": dataset,
            "split": item["split"],
            "documents": {},
        }

        def checkpoint(scope=None):
            with lock:
                if any(owner != sample_id and record["id"] == dataset_id for owner, record in state["datasets"].items()):
                    raise RagflowError("independent samples must not share a knowledge base")
                state["datasets"][sample_id] = deepcopy(ledger)
                if scope is not None:
                    state["scopes"][sample_id] = scope
                atomic_json(path, state)

        checkpoint()
        existing = active_client.list_documents(dataset_id)
        by_name = defaultdict(list)
        for doc in existing:
            by_name[doc["name"]].append(doc)
        assets = {asset["path"]: asset for asset in item["documents"]}
        if set(by_name) - {Path(reference).name for reference in assets}:
            raise RagflowError(f"sample {sample_id} knowledge base contains foreign documents; inspect it before resuming")
        for reference, asset in assets.items():
            name = Path(reference).name
            matches = by_name[name]
            if len(matches) > 1:
                raise RagflowError(f"ambiguous duplicate document name in dataset {dataset_id}: {name}")
            old = ledger["documents"].get(reference)
            if matches:
                document_id = matches[0]["id"]
                if old and old["id"] != document_id:
                    raise RagflowError("document id changed remotely; inspect the index ledger before continuing")
            else:
                document_id = active_client.upload_document(dataset_id, inside(bundle, reference))
            ledger["documents"][reference] = {"id": document_id, "name": name, "sha256": asset["sha256"]}
            checkpoint()
        expected = {doc["id"] for doc in ledger["documents"].values()}
        statuses = {doc["id"]: doc for doc in active_client.list_documents(dataset_id)}
        if expected != statuses.keys():
            raise RagflowError("sample knowledge base contains missing or foreign documents; inspect it before resuming")
        start = []
        for identifier in sorted(expected):
            status = _status(statuses[identifier])
            if status == "UNSTART" or (retry_failed and status in {"FAIL", "CANCEL"}):
                start.append(identifier)
            elif status in {"FAIL", "CANCEL"}:
                raise RagflowError(f"document parsing {status}: {identifier}; inspect RAGFlow and resume with --retry-failed")
            elif status not in {"DONE", "RUNNING"}:
                raise RagflowError(f"unknown parsing state {status}: {identifier}")
        for offset in range(0, len(start), batch_size):
            active_client.parse_documents(dataset_id, start[offset : offset + batch_size])
        deadline = time.monotonic() + timeout
        while True:
            statuses = {doc["id"]: doc for doc in active_client.list_documents(dataset_id)}
            if expected != statuses.keys():
                raise RagflowError("sample knowledge base contains missing or foreign documents during parsing")
            references = {record["id"]: ref for ref, record in ledger["documents"].items()}
            for identifier in expected:
                doc = statuses[identifier]
                status = _status(doc)
                ledger["documents"][references[identifier]].update(run=status, chunk_count=doc.get("chunk_count", 0))
                if status in {"FAIL", "CANCEL"}:
                    checkpoint()
                    raise RagflowError(f"document parsing {status}: {identifier}; inspect RAGFlow and resume with --retry-failed")
                if status == "DONE" and not doc.get("chunk_count", 0):
                    checkpoint()
                    raise RagflowError(f"document parsing produced zero indexed chunks: {identifier}")
                if status not in {"DONE", "RUNNING", "UNSTART"}:
                    raise RagflowError(f"unknown parsing state {status}: {identifier}")
            checkpoint()
            if all(_status(statuses[identifier]) == "DONE" for identifier in expected):
                break
            if time.monotonic() >= deadline:
                raise RagflowError("parsing wait timed out; the index ledger is saved; resume the same index command")
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        checkpoint({"dataset_ids": [dataset_id], "document_ids": sorted({ledger["documents"][asset["path"]]["id"] for asset in item["documents"]})})

    def run_sample(item):
        if not hasattr(thread_state, "client"):
            thread_state.client = RagflowClient(config)
            with lock:
                sessions.append(thread_state.client)
        index_sample(item, thread_state.client)

    try:
        if workers == 1:
            for item in samples:
                index_sample(item, client)
        else:
            # Keep only a bounded window in flight; stop submitting on failure.
            # Each worker owns its HTTP session; ledger writes are serialized.
            remaining = iter(samples)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                pending = {executor.submit(run_sample, item) for item in [next(remaining) for _ in range(min(workers, len(samples)))]}
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                    for _ in done:
                        item = next(remaining, None)
                        if item is not None:
                            pending.add(executor.submit(run_sample, item))
    finally:
        for session_client in sessions:
            session_client.session.close()
    validate_sample_scopes(state, [item["id"] for item in samples])
    state["ready"] = True
    atomic_json(path, state)
    return state


RETRIEVAL_OPTIONS = {
    "similarity_threshold",
    "vector_similarity_weight",
    "keywords_similarity_weight",
    "top_k",
    "knn_top_k",
    "rerank_id",
    "keyword",
    "highlight",
}


def retrieve(client, scope: dict, question: str, *, page: int = 1, page_size: int = 10, options: dict | None = None) -> dict:
    if not isinstance(question, str) or not question.strip() or len(question) > 32000:
        raise ValueError("retrieval requires a nonempty question (at most 32000 characters)")
    if not 1 <= page or not 1 <= page_size <= 100:
        raise ValueError("invalid retrieval pagination")
    datasets, documents = scope.get("dataset_ids"), scope.get("document_ids")
    if (
        not isinstance(datasets, list)
        or len(datasets) != 1
        or not isinstance(documents, list)
        or not documents
        or not all(isinstance(value, str) and value for value in [*datasets, *documents])
    ):
        raise ValueError("retrieval scope requires exactly one sample knowledge base and nonempty document_ids")
    if scope.get("base_url") and scope["base_url"] != client.base_url:
        raise RagflowError("retrieval endpoint differs from the indexed scope")
    options = options or {}
    if options.keys() - RETRIEVAL_OPTIONS:
        raise ValueError("unsupported retrieval options; dataset/document scope cannot be overridden")
    data = client.retrieve({**options, "question": question, "dataset_ids": datasets, "document_ids": documents, "page": page, "page_size": page_size})
    allowed = set(documents)
    for chunk in data.get("chunks", []):
        if chunk.get("document_id", chunk.get("doc_id")) not in allowed:
            raise RagflowError("RAGFlow returned a hit outside the sample document scope")
    return data
