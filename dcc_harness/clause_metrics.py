"""CLAUSE In-Text metrics, ported from the supplied intext-yes-whole-document-v1 pack.

Preserves its GPT-DFS ordering, original-defect IDs, rounding and denominators.
See configs/clause/NOTICE.md. No explanation Judge or external-law evaluation.
Dependencies are loaded only when evaluation is initialized.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import statistics
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "configs/clause"
TEXT_KEYS = ("rouge-1", "rouge-2", "rouge-l", "meteor", "bert_score_f1")
SOURCES = ("CUAD", "NLI")
CATEGORIES = ("ambiguity", "inconsistency", "terminology", "omission", "structural")


@lru_cache(maxsize=1)
def protocol():
    return json.loads((CONFIG / "protocol.json").read_text(encoding="utf-8"))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_gold(gold):
    if not isinstance(gold, list) or len(gold) != 1 or not isinstance(gold[0], dict):
        raise ValueError("CLAUSE gold must contain one annotation object")
    perturbations = gold[0].get("perturbation")
    if not isinstance(perturbations, list) or not perturbations:
        raise ValueError("CLAUSE requires nonempty whole-document annotations")
    for item in perturbations:
        if not isinstance(item, dict) or item.get("contradiction_exists") != "YES":
            raise ValueError("CLAUSE gold must retain only whole-document YES cases")
        if (
            not isinstance(item.get("explanation"), str)
            or not item["explanation"].strip()
        ):
            raise ValueError("CLAUSE gold explanation is missing")
        spans = [item.get(key) for key in ("changed_text", "contradicted_text")]
        if any(
            value is not None and not isinstance(value, str) for value in spans
        ) or not any(isinstance(v, str) and v.strip() for v in spans):
            raise ValueError("CLAUSE gold requires text spans")
    return perturbations


def align(ground_truth, prediction, sentence_tokenize=None):
    if sentence_tokenize is None:
        import nltk

        sentence_tokenize = nltk.sent_tokenize
    gt, pred = [], []
    for i, item in enumerate(ground_truth[0]["perturbation"]):
        for suffix, key in (
            ("changed", "changed_text"),
            ("contradicted", "contradicted_text"),
        ):
            if item.get(key):
                gt.append(
                    dict(id=f"gt_{i}_{suffix}", original_id=f"gt_{i}", text=item[key])
                )
    for i, item in enumerate(prediction):
        pred.append(dict(id=f"pred_{i}", text=item["text"]))
    items = gt + pred
    for item in items:
        item["sentences"] = {
            s.strip().lower() for s in sentence_tokenize(item["text"]) if s.strip()
        }
    by_id = {item["id"]: item for item in items}
    adj = defaultdict(list)
    for left in gt:
        for right in pred:
            if left["sentences"] & right["sentences"]:
                adj[left["id"]].append(right["id"])
                adj[right["id"]].append(left["id"])
    visited, groups = set(), []
    for item_id in by_id:
        if item_id in visited:
            continue
        group, stack = [], [item_id]
        visited.add(item_id)
        while stack:
            node_id = stack.pop()
            group.append(by_id[node_id])
            for neighbor in adj[node_id]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        groups.append(group)
    matches, hit_gt, hit_pred = [], set(), set()
    for group in groups:
        gs = [x for x in group if x["id"].startswith("gt_")]
        ps = [x for x in group if x["id"].startswith("pred_")]
        if not (gs and ps):
            continue
        hit_gt.update(x["original_id"] for x in gs)
        hit_pred.update(x["id"] for x in ps)
        matches.append(
            dict(
                gt_ids=sorted({x["original_id"] for x in gs}),
                gt_node_ids=[x["id"] for x in gs],
                pred_ids=[x["id"] for x in ps],
                gt_text=" ".join(x["text"] for x in gs),
                pred_text=" ".join(x["text"] for x in ps),
            )
        )
    n, m = len({x["original_id"] for x in gt}), len(pred)
    return dict(
        n=n,
        m=m,
        FN=n - len(hit_gt),
        FP=m - len(hit_pred),
        miss=round((n - len(hit_gt)) / n, 4) if n else 0.0,
        extra=round((m - len(hit_pred)) / m, 4) if m else 0.0,
        signed_balance=round((m - n) / (m + n), 4) if m + n else 0.0,
        normalized_gap=round(abs(m - n) / max(m, n), 4) if max(m, n) else 0.0,
        groups=matches,
    )


def average(rows):
    return {
        k: round(statistics.mean(r[k] for r in rows if k in r), 4)
        for k in TEXT_KEYS
        if any(k in r for r in rows)
    }


def summarize(docs):
    matched = [d for d in docs if d["text_scores"] is not None]
    return dict(
        documents=len(docs),
        ground_truth=sum(d["n"] for d in docs),
        predictions=sum(d["m"] for d in docs),
        FN=sum(d["FN"] for d in docs),
        FP=sum(d["FP"] for d in docs),
        miss=round(statistics.mean(d["miss"] for d in docs), 4) if docs else None,
        extra=round(statistics.mean(d["extra"] for d in docs), 4) if docs else None,
        text_scores=average([d["text_scores"] for d in matched]),
        text_score_documents=len(matched),
        no_match_documents=len(docs) - len(matched),
        aligned_groups=sum(d["matched_groups"] for d in docs),
    )


def aggregate_documents(docs):
    by = {
        src: {
            cat: summarize(
                [d for d in docs if d["source"] == src and d["category"] == cat]
            )
            for cat in CATEGORIES
        }
        for src in SOURCES
    }
    means = {}
    for src, categories in by.items():
        active = [v for v in categories.values() if v["documents"]]
        means[src] = (
            {
                k: round(statistics.mean(v[k] for v in active), 4)
                for k in ("miss", "extra")
            }
            if active
            else {}
        )
        means[src]["text_scores"] = average([v["text_scores"] for v in active])
        means[src]["categories_with_documents"] = len(active)
        means[src]["text_metric_category_denominators"] = {
            k: sum(k in v["text_scores"] for v in active)
            for k in means[src]["text_scores"]
        }
    return dict(
        all_documents_macro=summarize(docs),
        by_source_category=by,
        source_five_category_equal_macro=means,
    )


def verify_model(model_dir):
    model_dir = Path(model_dir)
    expected = protocol()["bert"]
    revision = json.loads((model_dir / "MODEL_REVISION.json").read_text())
    if any(revision.get(k) != expected[k] for k in ("repo_id", "revision")):
        raise ValueError("BERTScore requires the pinned RoBERTa-large revision")
    files = {entry["path"]: entry for entry in revision["files"]}
    required = {"config.json", "vocab.json", "merges.txt", "model.safetensors"}
    if not required <= files.keys():
        raise ValueError("BERTScore model manifest is incomplete")
    weights = files["model.safetensors"]
    if (
        weights["sha256"] != expected["weights_sha256"]
        or weights["size"] != expected["weights_size"]
    ):
        raise ValueError("BERTScore weights differ from the pinned LFS identity")
    for name, info in files.items():
        if Path(name).name != name or name.startswith("."):
            raise ValueError("invalid model resource name")
        path = model_dir / name
        if (
            not path.is_file()
            or path.stat().st_size != info["size"]
            or file_hash(path) != info["sha256"]
        ):
            raise ValueError("BERTScore model resource integrity check failed")
    return revision


class TextMetrics:
    def __init__(
        self,
        *,
        nltk_data,
        without_bert=False,
        model_dir=None,
        device="cpu",
        batch_size=8,
    ):
        pins = {"nltk": "3.8.1", "rouge-score": "0.1.2"}
        if not without_bert:
            pins.update({"bert-score": "0.3.13", "transformers": "4.44.2"})
        for name, version in pins.items():
            if importlib.metadata.version(name) != version:
                raise ValueError(f"CLAUSE protocol requires {name}=={version}")
        root = Path(nltk_data).resolve()
        hashes = protocol()["nltk_files"]
        for relative, expected in hashes.items():
            if file_hash(root / relative) != expected:
                raise ValueError("CLAUSE NLTK resource integrity check failed")
        import nltk
        from nltk.corpus.reader import WordNetCorpusReader
        from rouge_score import rouge_scorer

        nltk.data.path.insert(0, str(root))
        nltk.data.clear_cache()
        self.sentence_tokenize = nltk.data.load(
            str(root / "tokenizers/punkt/PY3/english.pickle")
        ).tokenize
        self.wordnet = WordNetCorpusReader(
            nltk.data.find("corpora/wordnet.zip/wordnet/", paths=[str(root)]), None
        )
        self.wordnet.synsets("contract")
        self.rouge = rouge_scorer.RougeScorer(
            ["rouge1", "rouge2", "rougeL"], use_stemmer=True
        )
        self.bert = None
        self.truncated_groups = 0
        self.provenance = {
            "nltk_files": hashes,
            "bert": {"enabled": not without_bert},
            "metrics_complete": not without_bert,
        }
        if not without_bert:
            revision = verify_model(model_dir)
            from bert_score import BERTScorer

            previous = {
                key: os.environ.get(key)
                for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
            }
            try:
                os.environ.update({key: "1" for key in previous})
                self.bert = BERTScorer(
                    model_type=str(Path(model_dir).resolve()),
                    num_layers=17,
                    lang="en",
                    idf=False,
                    rescale_with_baseline=False,
                    device=device,
                    batch_size=batch_size,
                    use_fast_tokenizer=False,
                )
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.provenance["bert"].update(protocol()["bert"], weight_manifest=revision)

    def score(self, gt, pred):
        import nltk
        from nltk.translate.meteor_score import meteor_score

        rs = self.rouge.score(gt, pred)
        result = {
            key: round(rs[src].fmeasure, 4)
            for key, src in (
                ("rouge-1", "rouge1"),
                ("rouge-2", "rouge2"),
                ("rouge-l", "rougeL"),
            )
        }
        result["meteor"] = round(
            meteor_score(
                [nltk.word_tokenize(gt)], nltk.word_tokenize(pred), wordnet=self.wordnet
            ),
            4,
        )
        if self.bert is not None:
            tok = self.bert._tokenizer
            lengths = [
                len(
                    tok.encode(
                        t.strip(),
                        add_special_tokens=True,
                        add_prefix_space=True,
                        truncation=False,
                        verbose=False,
                    )
                )
                for t in (gt, pred)
            ]
            truncated = any(length > tok.model_max_length for length in lengths)
            self.truncated_groups += int(truncated)
            _, _, f1 = self.bert.score([pred], [gt], batch_size=self.bert.batch_size)
            result["bert_score_f1"] = round(f1.mean().item(), 4)
            result["_bert_truncated"] = truncated
        return result
