# ruff: noqa
# fmt: off
# Function-only excerpt: The-FinAI/FinAuditing@ccf99d9b0a1610576f8997fb18b35076c9bbb838/StartKit/evaluateFinMR.ipynb
# MIT license: FINAUDITING_LICENSE. Notebook execution cells are intentionally excluded.
import os

import json

import re

from typing import List, Dict, Any, Tuple, Optional

from tqdm import tqdm

LABELS = {"A", "S", "E", "C"}

PRED_KEY = "prediction"

GOLD_KEY = "ground_truth"

JUDGE_OUT_JSONL = "finmr_judge_results.jsonl"

_CODEBLOCK_RE = re.compile(r"^```[a-zA-Z0-9]*\n([\s\S]*?)\n```$", re.M)

_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")

def _strip_codeblock(s: str) -> str:
    s = s.strip()
    m = _CODEBLOCK_RE.search(s)
    return m.group(1).strip() if m else s

def parse_json_object_best_effort(text: Any) -> Optional[Dict[str, Any]]:
    """
    Try to extract a JSON object from text.
    Returns dict if possible, else None.

    Handles:
      - already dict
      - JSON string
      - text containing {...}
      - code block wrapping
    """
    if text is None:
        return None
    if isinstance(text, dict):
        return text
    if isinstance(text, str):
        s = text.strip()
    else:
        s = str(text).strip()

    if not s:
        return None

    s = _strip_codeblock(s)

    # direct parse
    if s.startswith("{") and s.endswith("}"):
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass

    # extract {...} from longer text
    m = _JSON_OBJ_RE.search(s)
    if m:
        obj_str = m.group(0).strip()
        try:
            obj = json.loads(obj_str)
            return obj if isinstance(obj, dict) else None
        except Exception:
            # common minor issue: single quotes
            try:
                obj = json.loads(obj_str.replace("'", '"'))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

    return None

def normalize_two_key_obj(obj: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Ensure object has exactly two required keys:
      extracted_value, calculated_value
    Return normalized dict if valid else None.
    """
    if not isinstance(obj, dict):
        return None
    if set(obj.keys()) != {"extracted_value", "calculated_value"}:
        return None
    return {
        "extracted_value": obj["extracted_value"],
        "calculated_value": obj["calculated_value"],
    }

def load_finmr_pairs_from_jsonl(path: str) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """
    Returns:
      - items: list of raw line objects (for id/query etc.)
      - true_answer: list of JSON-serializable true objects (dict or str)
      - pred_answer: list of raw prediction strings
    """
    items = []
    true_answer = []
    pred_answer = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            items.append(obj)
            true_answer.append(obj.get(GOLD_KEY))
            pred_answer.append(obj.get(PRED_KEY, ""))
    return items, true_answer, pred_answer

def get_prompt(true_answer: Dict[str, Any], pred_answer: str) -> str:
    return f"""Instruction: You are an evaluator. Your task is to judge whether the model’s output pred_answer is correct compared to the given true_answer. 
Follow the rules strictly:

Step 1 (Structure Check):
    Verify whether pred_answer has the same structure as true_answer. The required structure is a JSON object with exactly two keys:
        {{"extracted_value": <value>, "calculated_value": <value>}}
    Minor formatting differences (e.g., line breaks, indentation, whitespace) are acceptable.
    If the structure is invalid, output the label: S
    If valid, continue to Step 2

Step 2 (Extracted Value Check):
    Compare true_answer["extracted_value"] and pred_answer["extracted_value"] by their mathematical meaning, not their string form. For example, "-1,284" and "-1284" are considered equal.
    If they are not equal in numeric meaning, output the label: E
    If equal, continue to Step 3

Step 3 (Calculated Value Check):
    Compare true_answer["calculated_value"] and pred_answer["calculated_value"] strictly in numeric meaning. They must be exactly equal (zero tolerance).
    If they are not equal, output the label: C
    If equal, then everything is correct

Final Decision:
    If all three checks pass, output the label: A
Output only one label: S, E, C, or A. Do not explain your reasoning.

Input:
    true_answer = {true_answer}
    pred_answer = {pred_answer}
Output:
"""

def make_openai_client(api_key: Optional[str] = None):
    from openai import OpenAI
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("Missing OPENAI_API_KEY. Set env var or pass api_key=...")
    return OpenAI(api_key=api_key)

def get_response(client,
                 user_input: str,
                 model: str = "gpt-4o") -> str:
    """
    Compatible judge caller for:
      - gpt-4o / gpt-4.1  (Chat Completions)
      - gpt-5 / gpt-5-mini (Responses API)
    Returns plain text.
    """

    model_l = model.lower()

    # -----------------------------
    # GPT-4o / GPT-4.1 branch
    # -----------------------------
    if model_l.startswith("gpt-4o") or model_l.startswith("gpt-4.1"):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a strict evaluator."},
                {"role": "user", "content": user_input},
            ],
            temperature=0.0,
            max_tokens=4,
        )
        return resp.choices[0].message.content.strip()

    # -----------------------------
    # GPT-5 / GPT-5-mini branch
    # -----------------------------
    else:
        resp = client.responses.create(
            model=model,
            input=user_input,
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"},
        )
        return resp.output_text.strip()

def normalize_judge_label(x: Any) -> Optional[str]:
    s = str(x).strip().upper()
    # Sometimes the model outputs "A\n" or "Output: A"; we extract the first A/S/E/C.
    if s in LABELS and len(s) == 1:
        return s
    m = re.search(r"\b([ASEC])\b", s)
    if m:
        return m.group(1)
    return None

def evaluate_finmr_with_judge(
    pred_jsonl_path: str,
    api_key: Optional[str] = None,
    judge_model: str = "gpt-5-mini",
    save_judge_jsonl: bool = True,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    items, true_raw, pred_raw = load_finmr_pairs_from_jsonl(pred_jsonl_path)
    if limit is not None:
        items = items[:limit]
        true_raw = true_raw[:limit]
        pred_raw = pred_raw[:limit]

    client = make_openai_client(api_key=api_key)

    A_list, S_list, E_list, C_list = [], [], [], []
    output_errors = 0
    preparse_fail_gold = 0

    # Optional: Write the judge's output.
    judge_f = open(JUDGE_OUT_JSONL, "w", encoding="utf-8") if save_judge_jsonl else None

    for obj, t_a_raw, p_a_raw in tqdm(zip(items, true_raw, pred_raw), total=len(items), desc="Judging"):
        # First, normalize `true_answer` into a dictionary of the form `dict(extracted_value, calculated_value)`.
        t_obj = normalize_two_key_obj(parse_json_object_best_effort(t_a_raw))
        if t_obj is None:
            # Gold is not legal, and therefore will not be considered for evaluation.
            preparse_fail_gold += 1
            output_errors += 1
            if judge_f:
                judge_f.write(json.dumps({
                    "id": obj.get("id"),
                    "judge_label": None,
                    "error": "invalid_gold_structure",
                    "ground_truth_raw": t_a_raw,
                    "prediction_raw": p_a_raw,
                }, ensure_ascii=False) + "\n")
            continue

        # 直接把 pred 原始文本给 judge（让 judge 做结构/数值校验）
        prompt = get_prompt(t_obj, p_a_raw)
        res_raw = get_response(client, prompt, model=judge_model)
        label = normalize_judge_label(res_raw)

        if label is None:
            output_errors += 1
        else:
            if label == "A":
                A_list.append(1)
            elif label == "S":
                S_list.append(1)
            elif label == "E":
                E_list.append(1)
            elif label == "C":
                C_list.append(1)

        if judge_f:
            judge_f.write(json.dumps({
                "id": obj.get("id"),
                "judge_label": label,
                "judge_raw": res_raw,
                "ground_truth": t_obj,
                "prediction_raw": p_a_raw,
            }, ensure_ascii=False) + "\n")

    if judge_f:
        judge_f.close()

    total = len(items)
    evaluated = total - output_errors

    def pct(x, d):
        return round(100.0 * x / d, 2) if d > 0 else 0.0

    results = {
        "total": total,
        "evaluated": evaluated,
        "Parsing success rate(%)": pct(evaluated, total),

        # The denominators of the following four terms are evaluated.
        "ACC(%)": pct(len(A_list), evaluated),
        "Structural error rate(%)": pct(len(S_list), evaluated),
        "Extraction error rate(%)": pct(len(E_list), evaluated),
        "Calculating error rate(%)": pct(len(C_list), evaluated),

        # Additional diagnosis
        "gold_invalid_count": preparse_fail_gold,
        "judge_output_unparsed_count": output_errors - preparse_fail_gold,
        "judge_jsonl": JUDGE_OUT_JSONL if save_judge_jsonl else None,
        "judge_model": judge_model,
    }
    return results
