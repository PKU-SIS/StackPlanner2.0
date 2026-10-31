# ruff: noqa
# fmt: off
# Function-only excerpt: The-FinAI/FinAuditing@ccf99d9b0a1610576f8997fb18b35076c9bbb838/StartKit/evaluateFinSM.ipynb
# MIT license: FINAUDITING_LICENSE. Notebook execution cells are intentionally excluded.
import json

import re

from typing import List, Tuple, Any, Dict, Optional

_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*?\]")

def _safe_json_loads(s: str) -> Any:
    """Try json.loads with small fixes."""
    s = s.strip()
   
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\n", "", s)
        s = re.sub(r"\n```$", "", s).strip()
    return json.loads(s)

def parse_pred_to_list(pred_text: Any) -> List[str]:
    """
    Convert model prediction to list[str].
    pred_text can be:
      - already a list
      - a JSON string of list
      - a string containing extra text but includes a JSON array
    Returns [] if cannot parse.
    """
    if pred_text is None:
        return []

    # already list
    if isinstance(pred_text, list):
        return [str(x).strip() for x in pred_text if str(x).strip()]

    # if dict (shouldn't happen for FinSM), return empty
    if isinstance(pred_text, dict):
        return []

    # treat as string
    s = str(pred_text).strip()
    if not s:
        return []

    # Try direct JSON parse first
    try:
        obj = _safe_json_loads(s)
        if isinstance(obj, list):
            return [str(x).strip() for x in obj if str(x).strip()]
    except Exception:
        pass

    # Try extract first JSON array from text (handles "Answer: [...]")
    m = _JSON_ARRAY_RE.search(s)
    if m:
        arr_str = m.group(0).strip()
        # fix common issues: single quotes -> double quotes (best-effort)
        # NOTE: this is heuristic; if tags contain apostrophes it's rare.
        try:
            obj = json.loads(arr_str)
            if isinstance(obj, list):
                return [str(x).strip() for x in obj if str(x).strip()]
        except Exception:
            try:
                obj = json.loads(arr_str.replace("'", '"'))
                if isinstance(obj, list):
                    return [str(x).strip() for x in obj if str(x).strip()]
            except Exception:
                return []

    return []

def parse_gold_to_list(gold: Any) -> List[str]:
    """
    Convert ground_truth to list[str].
    gold can be:
      - list[str]
      - JSON string of list
      - single string
    """
    if gold is None:
        return []
    if isinstance(gold, list):
        return [str(x).strip() for x in gold if str(x).strip()]
    if isinstance(gold, dict):
        return []
    s = str(gold).strip()
    if not s:
        return []
    # try JSON
    try:
        obj = _safe_json_loads(s)
        if isinstance(obj, list):
            return [str(x).strip() for x in obj if str(x).strip()]
    except Exception:
        pass
    # fallback single label
    return [s]

def hit_rate_at_k(true_answer: List[List[str]],
                  pred_answer: List[List[str]],
                  k: int,
                  ignore_empty_gold: bool = True) -> float:
    """
    HR@k (Hit Rate at k).
    If ignore_empty_gold=True, skip queries with empty gold.
    """
    assert len(true_answer) == len(pred_answer), "true_answer and pred_answer must have the same length"
    hits = 0
    n = 0
    for g, p in zip(true_answer, pred_answer):
        G = set(g)
        if ignore_empty_gold and len(G) == 0:
            continue
        topk = set(p[:k])
        if topk & G:
            hits += 1
        n += 1
    return hits / n if n > 0 else 0.0

def recall_at_k(true_answer: List[List[str]],
                pred_answer: List[List[str]],
                k: int,
                ignore_empty_gold: bool = True) -> float:
    """
    R@k (Recall at k).
    If ignore_empty_gold=True, skip queries with empty gold.
    """
    assert len(true_answer) == len(pred_answer), "true_answer and pred_answer must have the same length"
    recall_sum = 0.0
    n = 0
    for g, p in zip(true_answer, pred_answer):
        G = set(g)
        if ignore_empty_gold and len(G) == 0:
            continue
        if len(G) == 0:
            # if not ignoring, define recall as 0 (no relevant items)
            recall_sum += 0.0
            n += 1
            continue
        topk = set(p[:k])
        recall_sum += len(topk & G) / len(G)
        n += 1
    return recall_sum / n if n > 0 else 0.0

def macro_f1_at_k(true_answer: List[List[str]],
                  pred_answer: List[List[str]],
                  k: int,
                  zero_division: float = 0.0,
                  ignore_empty_gold: bool = True) -> float:
    """
    Macro F1@k (query-level F1 averaged).
    """
    assert len(true_answer) == len(pred_answer), "Lengths must match."

    f1_sum = 0.0
    n = 0

    for g, p in zip(true_answer, pred_answer):
        G = set(g)
        if ignore_empty_gold and len(G) == 0:
            continue

        P = set(p[:k])

        tp = len(G & P)
        fp = len(P - G)
        fn = len(G - P)

        precision = tp / (tp + fp) if (tp + fp) > 0 else zero_division
        recall    = tp / (tp + fn) if (tp + fn) > 0 else zero_division
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        f1_sum += f1
        n += 1

    return (f1_sum / n) if n > 0 else 0.0

def evaluate_metrics(true_answer: List[List[str]],
                     pred_answer: List[List[str]],
                     ks: List[int] = [1, 5, 10, 20],
                     ignore_empty_gold: bool = True) -> Dict[str, str]:
    results = {}
    hr_list, r_list, f1_list = [], [], []

    for k in ks:
        hr = hit_rate_at_k(true_answer, pred_answer, k, ignore_empty_gold=ignore_empty_gold) * 100
        r = recall_at_k(true_answer, pred_answer, k, ignore_empty_gold=ignore_empty_gold) * 100
        macro_f1 = macro_f1_at_k(true_answer, pred_answer, k, ignore_empty_gold=ignore_empty_gold) * 100

        results[f"HR@{k}"] = f"{hr:.2f}%"
        results[f"R@{k}"] = f"{r:.2f}%"
        results[f"Macro-F1@{k}"] = f"{macro_f1:.2f}%"

        hr_list.append(hr)
        r_list.append(r)
        f1_list.append(macro_f1)

    results["HR@avg"] = f"{sum(hr_list) / len(hr_list):.2f}%"
    results["R@avg"] = f"{sum(r_list) / len(r_list):.2f}%"
    results["Macro-F1@avg"] = f"{sum(f1_list) / len(f1_list):.2f}%"

    return results

def load_finsm_from_jsonl(pred_jsonl_path: str,
                          pred_key: str = "prediction",
                          gold_key: str = "ground_truth") -> Tuple[List[List[str]], List[List[str]]]:
    """
    Read your jsonl, return (true_answer, pred_answer) as List[List[str]].
    """
    true_answer: List[List[str]] = []
    pred_answer: List[List[str]] = []

    with open(pred_jsonl_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)

            gold_raw = obj.get(gold_key, None)
            pred_raw = obj.get(pred_key, "")

            gold_list = parse_gold_to_list(gold_raw)
            pred_list = parse_pred_to_list(pred_raw)

            true_answer.append(gold_list)
            pred_answer.append(pred_list)

    return true_answer, pred_answer

def evaluate_finsm(pred_jsonl_path: str,
                   ks: List[int] = [1, 5, 10, 20],
                   ignore_empty_gold: bool = True) -> Dict[str, str]:
    true_answer, pred_answer = load_finsm_from_jsonl(pred_jsonl_path)
    return evaluate_metrics(true_answer, pred_answer, ks=ks, ignore_empty_gold=ignore_empty_gold)
