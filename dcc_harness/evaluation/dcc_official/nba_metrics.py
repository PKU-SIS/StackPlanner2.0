# ruff: noqa
# fmt: off
# Isolated official aggregation block from RuleArena@59f60f6/nba/auto_test.py.
# MIT license: RULEARENA_LICENSE. Source I/O and model drivers are excluded.
from collections import defaultdict
import numpy as np

def metrics(rule_applications, all_rules):
    problem_wise_recall, problem_wise_precision = [], []
    tp, fp, fn = defaultdict(int), defaultdict(int), defaultdict(int)
    for applied_rule_list, ground_truth_rule_list in rule_applications:
        problem_wise_tp, problem_wise_fp, problem_wise_fn = 0, 0, 0
        for rule in set(applied_rule_list + ground_truth_rule_list):
            if rule in applied_rule_list and rule in ground_truth_rule_list:
                tp[rule] += 1
                problem_wise_tp += 1
            elif rule in applied_rule_list:
                fp[rule] += 1
                problem_wise_fp += 1
            else:
                fn[rule] += 1
                problem_wise_fn += 1
        if problem_wise_tp + problem_wise_fn > 0:
            problem_wise_recall.append(problem_wise_tp / (problem_wise_tp + problem_wise_fn))
        if problem_wise_tp + problem_wise_fp > 0:
            problem_wise_precision.append(problem_wise_tp / (problem_wise_tp + problem_wise_fp))

    problem_wise_recall = np.mean(problem_wise_recall)
    problem_wise_precision = np.mean(problem_wise_precision)

    rule_wise_recall, rule_wise_precision, rule_wise_total = dict(), dict(), dict()
    for rule in all_rules:
        if tp[rule] + fp[rule] > 0:
            rule_wise_precision[rule] = tp[rule] / (tp[rule] + fp[rule])
        else:
            rule_wise_precision[rule] = np.nan
        if tp[rule] + fn[rule] > 0:
            rule_wise_recall[rule] = tp[rule] / (tp[rule] + fn[rule])
        else:
            rule_wise_recall[rule] = np.nan
        rule_wise_total[rule] = tp[rule] + fp[rule] + fn[rule]
    

    return {"problem_wise_recall": problem_wise_recall, "problem_wise_precision": problem_wise_precision, "rule_wise_recall": rule_wise_recall, "rule_wise_precision": rule_wise_precision, "rule_wise_total": rule_wise_total}
