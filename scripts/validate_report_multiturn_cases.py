"""Validate/freeze multi-turn fixtures offline. This module cannot launch an API run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("unexpected fields in model-visible fixture schema")


def load_public_cases(path):
    """The only fixture loader intended for future agent input construction."""
    data = _read(path)
    _keys(data, ("schema_version", "suite_id", "cases"))
    if data["schema_version"] != 1 or not 2 <= len(data["cases"]) <= 3:
        raise ValueError("suite requires schema 1 and two or three cases")
    identities = set()
    case_ids = set()
    for case in data["cases"]:
        _keys(case, ("case_id", "length_band", "turns"))
        if (
            case["case_id"] in case_ids
            or case["length_band"] != "10000"
            or not 5 <= len(case["turns"]) <= 7
        ):
            raise ValueError("invalid case identity, length band or turn count")
        case_ids.add(case["case_id"])
        for position, turn in enumerate(case["turns"], 1):
            _keys(turn, ("turn_index", "message_id", "query"))
            if (
                turn["turn_index"] != position
                or turn["message_id"] in identities
                or not isinstance(turn["query"], str)
                or not turn["query"].strip()
            ):
                raise ValueError("invalid source message identity or query")
            identities.add(turn["message_id"])
    return data


def _manifest(directory, public):
    return {
        "schema_version": 1,
        "suite_id": public["suite_id"],
        "api_experiments_authorized": False,
        "sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("requests.json", "evaluator_checks.json")
        },
    }


def validate_suite(directory, *, verify_frozen=True):
    directory = Path(directory)
    public = load_public_cases(directory / "requests.json")
    private = _read(directory / "evaluator_checks.json")
    if private["schema_version"] != 1 or private["suite_id"] != public["suite_id"]:
        raise ValueError("evaluator suite identity mismatch")
    expected = {case["case_id"]: case for case in private["cases"]}
    if len(expected) != len(public["cases"]) or set(expected) != {
        case["case_id"] for case in public["cases"]
    }:
        raise ValueError("evaluator case identity mismatch")
    turns = 0
    for case in public["cases"]:
        rows = expected[case["case_id"]]["turns"]
        if len(rows) != len(case["turns"]):
            raise ValueError("evaluator turn count mismatch")
        accepted = set()
        head = active = None
        for number, row in enumerate(rows, 1):
            mode = row["mode"]
            if row["turn_index"] != number or mode not in {
                "generate",
                "revise",
                "read_only",
                "exact_reuse",
            }:
                raise ValueError("invalid evaluator mode or turn")
            base = row["expected_base_turn"]
            if (number == 1 and (mode != "generate" or base is not None)) or (
                number > 1 and (base not in accepted or mode == "generate")
            ):
                raise ValueError("base must refer to an earlier accepted report")
            if mode in {"generate", "revise"}:
                accepted.add(number)
                head = active = number
            elif mode == "exact_reuse":
                active = base
            if (
                head != row["expected_accepted_head_turn"]
                or active != row["expected_active_turn"]
            ):
                raise ValueError("incorrect accepted-head or active-version transition")
            if row["native_delivery_required"] != (mode in {"generate", "revise"}):
                raise ValueError(
                    "native delivery and archive reuse must remain distinct"
                )
            if set(row["active_requirements"]) & set(row["withdrawn_requirements"]):
                raise ValueError("withdrawn requirement is still active")
            if mode in {"read_only", "exact_reuse"} and not {
                "researcher",
                "writer",
                "reporter",
            } <= set(row["forbidden_roles"]):
                raise ValueError("archive turn must forbid research and writing")
            turns += 1
    if verify_frozen and _read(directory / "manifest.json") != _manifest(
        directory, public
    ):
        raise ValueError("frozen fixture checksum mismatch")
    return {
        "suite_id": public["suite_id"],
        "cases": len(public["cases"]),
        "turns": turns,
        "api_calls": 0,
        "api_experiments_authorized": False,
    }


def freeze_suite(directory):
    directory = Path(directory)
    validate_suite(directory, verify_frozen=False)
    expected = _manifest(directory, load_public_cases(directory / "requests.json"))
    path = directory / "manifest.json"
    if path.exists():
        if _read(path) != expected:
            raise ValueError(
                "suite is already frozen; create a new suite version for changes"
            )
        return expected
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(expected, ensure_ascii=False, indent=2) + "\n")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "docs/research-report/multiturn-evaluation-20261122",
    )
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="Create the SHA manifest; never permits API execution",
    )
    args = parser.parse_args()
    if args.freeze:
        freeze_suite(args.suite_dir)
    print(json.dumps(validate_suite(args.suite_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
