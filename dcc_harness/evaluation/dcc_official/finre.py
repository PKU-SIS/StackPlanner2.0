# ruff: noqa
# fmt: off
# Function-only excerpt: The-FinAI/FinAuditing@ccf99d9b0a1610576f8997fb18b35076c9bbb838/StartKit/evaluateFinRE.ipynb
# MIT license: FINAUDITING_LICENSE. Notebook execution cells are intentionally excluded.
import json

import re

from typing import List, Tuple, Dict, Any, Optional

from tqdm import tqdm

from sklearn.metrics import classification_report, accuracy_score, precision_score, recall_score, f1_score

LABELS = ["Reversal", "Inappropriateness", "CombinationErr"]

INVALID_LABEL = "invalid"

_CANONICAL = {
    "reversal": "Reversal",
    "inappropriateness": "Inappropriateness",
    "combinationerr": "CombinationErr",
    "combination_err": "CombinationErr",
    "combination error": "CombinationErr",
    "combination": "CombinationErr",
}

_CODEBLOCK_RE = re.compile(r"^```[a-zA-Z0-9]*\n([\s\S]*?)\n```$", re.M)

_QUOTED_RE = re.compile(r'^[\'"](.+)[\'"]$')

def normalize_findre_pred(pred_text: Any) -> str:
    """
    Convert raw model output to one of LABELS, else INVALID_LABEL.

    pred_text may include:
      - exact label
      - label inside quotes
      - codeblock
      - extra explanation text
      - JSON like {"label": "..."} or ["Reversal"] etc.
    """
    if pred_text is None:
        return INVALID_LABEL

    # already exact?
    if isinstance(pred_text, str):
        s = pred_text.strip()
    else:
        s = str(pred_text).strip()

    if not s:
        return INVALID_LABEL

    # strip code block wrapper if present
    m = _CODEBLOCK_RE.search(s)
    if m:
        s = m.group(1).strip()

    # if JSON object/array, try parse
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            obj = json.loads(s)
            # {"label": "..."}
            if isinstance(obj, dict):
                for key in ["label", "prediction", "answer", "output"]:
                    if key in obj and isinstance(obj[key], str):
                        s = obj[key].strip()
                        break
            # ["Reversal"]
            elif isinstance(obj, list) and len(obj) > 0:
                s0 = obj[0]
                s = s0.strip() if isinstance(s0, str) else str(s0).strip()
        except Exception:
            pass

    # strip outer quotes
    m = _QUOTED_RE.match(s)
    if m:
        s = m.group(1).strip()

    # If it contains the label somewhere in text, pick the first match
    # (handles: "The answer is Reversal." )
    low = s.lower()
    for key, canon in _CANONICAL.items():
        if key in low:
            return canon

    # direct match after cleanup
    if s in LABELS:
        return s

    # try normalize simple forms
    s_norm = re.sub(r"[^a-zA-Z_ ]+", "", low).strip()  # keep letters/_/space
    s_norm = re.sub(r"\s+", " ", s_norm)
    s_norm2 = s_norm.replace(" ", "")
    if s_norm in _CANONICAL:
        return _CANONICAL[s_norm]
    if s_norm2 in _CANONICAL:
        return _CANONICAL[s_norm2]

    return INVALID_LABEL

def normalize_findre_gold(gold: Any) -> str:
    """
    Ground truth should already be one of LABELS, but we still normalize for safety.
    """
    if gold is None:
        return INVALID_LABEL
    if isinstance(gold, str):
        s = gold.strip()
    else:
        s = str(gold).strip()

    if s in LABELS:
        return s

    # try same canonical map
    low = s.lower()
    low = re.sub(r"[^a-zA-Z_ ]+", "", low).strip()
    low = re.sub(r"\s+", " ", low)
    low2 = low.replace(" ", "")
    if low in _CANONICAL:
        return _CANONICAL[low]
    if low2 in _CANONICAL:
        return _CANONICAL[low2]

    return INVALID_LABEL

def load_findre_from_jsonl(
    result_path: str,
    pred_key: str = "prediction",
    gold_key: str = "ground_truth",
) -> Tuple[List[str], List[str]]:
    true_answer: List[str] = []
    pred_answer: List[str] = []

    with open(result_path, "r", encoding="utf-8") as f:
        for line in tqdm(f, desc="Load"):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)

            gold_raw = obj.get(gold_key, None)
            pred_raw = obj.get(pred_key, None)

            y_true = normalize_findre_gold(gold_raw)
            y_pred = normalize_findre_pred(pred_raw)

            true_answer.append(y_true)
            pred_answer.append(y_pred)

    return true_answer, pred_answer

def evaluate_findre(
    result_path: str,
    labels: List[str] = LABELS,
    include_invalid_in_report: bool = True,
    digits: int = 4
) -> Dict[str, float]:
    y_true, y_pred = load_findre_from_jsonl(result_path)

    # Optional: Include invalid entries in the report (for greater transparency).
    report_labels = labels + ([INVALID_LABEL] if include_invalid_in_report else [])

    print(classification_report(y_true, y_pred, labels=report_labels, digits=digits))

    # The main metrics usually only consider three categories; invalid data will affect accuracy (because it will be counted as an error).
    acc = accuracy_score(y_true, y_pred)

    macro_p = precision_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    macro_r = recall_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    macro_f1 = f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)

    return {
        "accuracy": acc,
        "macro_precision": macro_p,
        "macro_recall": macro_r,
        "macro_f1": macro_f1,
        "n": len(y_true),
        "n_invalid_pred": sum(1 for x in y_pred if x == INVALID_LABEL),
        "n_invalid_gold": sum(1 for x in y_true if x == INVALID_LABEL),
    }
