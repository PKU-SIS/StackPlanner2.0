"""Check declared facts and receipt scope, without repairing model text or JSON."""

import re


def assertion_errors(metadata, *, expected_grade, rule_id, displayed_pairs, ocr_pairs=None, candidate_page_count=None):
    errors = []
    if metadata.get("evidence_level") != expected_grade:
        errors.append("artifact_metadata.evidence_level必须逐字使用规则库证据等级：" + expected_grade)
    if rule_id == "M06":
        reviewed = metadata.get("visual_reviewed_candidates")
        if not isinstance(reviewed, list) or any(type(v) is not int for v in reviewed) or len(set(reviewed)) != len(reviewed):
            errors.append("M06需给visual_reviewed_candidates：本报告实际判读的候选offset列表，可为[]；不要求覆盖全部候选。")
        elif set(reviewed) - displayed_pairs:
            errors.append("以下候选尚无两端实际展示回执，不得声称已视觉判读：" + str(sorted(set(reviewed) - displayed_pairs)))
        if ocr_pairs is not None:
            reviewed = metadata.get("ocr_reviewed_candidates")
            if not isinstance(reviewed, list) or any(type(v) is not int for v in reviewed) or len(set(reviewed)) != len(reviewed):
                errors.append("M06需给ocr_reviewed_candidates：实际依据两端OCR判读的候选offset，可为[]；页面范围推断不等于逐对判读。")
            elif set(reviewed) - ocr_pairs:
                errors.append("以下候选尚无两端完整OCR chunk读取回执，不得声称已OCR判读：" + str(sorted(set(reviewed) - ocr_pairs)))
        if candidate_page_count is not None and (type(metadata.get("candidate_page_count")) is not int or metadata["candidate_page_count"] != candidate_page_count):
            errors.append("candidate_page_count须使用真实候选端点去重页数，不是页码范围长度之和：" + str(candidate_page_count))
    return errors


def report_text_errors(text, metadata, *, expected_grade, rule_id, candidate_page_count=None):
    errors = []
    for raw in re.findall(r"证据等级\s*[:：]\s*([^\n]+)", text.replace("**", "")):
        value = raw.replace("**", "").strip().lstrip("：:")
        if not value.startswith(expected_grade):
            errors.append("正文证据等级与规则不一致；使用完整原始等级，不要为技术名词片段赋予等级含义。")
    if rule_id == "M11":
        for statement in re.split(r"[。\n]", text.replace("**", "")):
            conditional = re.search(r"可能|假设|若|尚未|未.*验证|未经.*验证|不能|不代表|不等于|无法", statement)
            definite_mechanism = re.search(r"子集(?:标签|前缀).*(?:是|为|属于|必然).*确定性", statement)
            absolute_reuse = re.search(r"否定跨主体.*(?:字体|字节).*复用|(?:真实字体|字节).*复用.*已.*排除", statement)
            if not conditional and (definite_mechanism or absolute_reuse):
                errors.append("字体结论超出当前代码事实：生成机制未由实际工具验证时须用条件表述；零字节匹配只限本次成功提取范围，不能写成排除文件复用或共同制作来源。")
    if rule_id == "M06":
        match = re.search(r"视觉判读候选数\s*[:：]\s*(\d+)", text)
        if match is None or int(match[1]) != len(metadata.get("visual_reviewed_candidates") if isinstance(metadata.get("visual_reviewed_candidates"), list) else []):
            errors.append("正文须写视觉判读候选数：N，与visual_reviewed_candidates个数一致；展示不等于判读。")
        if candidate_page_count is not None:
            match = re.search(r"候选涉及唯一页数\s*[:：]\s*(\d+)", text)
            if match is None or int(match[1]) != candidate_page_count:
                errors.append("正文须写候选涉及唯一页数：" + str(candidate_page_count) + "；不能把连续页码区间长度当作候选唯一页数。")
            match = re.search(r"OCR文本判读候选数\s*[:：]\s*(\d+)", text)
            reviewed = metadata.get("ocr_reviewed_candidates")
            count = len(reviewed) if isinstance(reviewed, list) else 0
            if match is None or int(match[1]) != count:
                errors.append("正文须写OCR文本判读候选数：N，与ocr_reviewed_candidates个数一致；可为0，不要求全量判读。")
    return errors
