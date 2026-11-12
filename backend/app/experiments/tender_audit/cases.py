"""Legacy-compatible case construction; ground-truth answers never enter cases."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError


@dataclass(frozen=True)
class AuditCase:
    case_id: int
    tender_name: str
    dataset_id: str
    rule_names: tuple[str, ...]
    rule_content: str
    keywords: str

    def public_dict(self) -> dict:
        return asdict(self)

    def task_context(self) -> str:
        return f"""【实验编号】{self.case_id}
【标书名称】{self.tender_name}
【唯一 RAGFlow dataset_id】{self.dataset_id}
【资源 URI】rag://dataset/{self.dataset_id}
【审核要点名称】{"；".join(self.rule_names)}
【审核要点完整规则】
{self.rule_content}

【建议起始检索词】{self.keywords}

执行要求：
1. 审核要点完整规则是唯一判断标准，不得自行增加标准。原规则中的旧输出字段描述由本实验统一交付格式替代，判断标准本身保持不变。
2. 必须调用 local_search_tool；应使用不同关键词多次检索，以寻找需要前后对比的全部条款。
3. 只能使用绑定的这一个标书知识库，禁止网络搜索，禁止引用外部资料。
4. 不得因为只检索到一处规定就轻率判定一致；证据不足时标记 needs_review=true。
5. 对不一致结论给出至少两处可相互比较的原文；单处内部合计错误可给出包含各分项的一处完整证据。
6. pass=false 表示发现不满足审核规则的问题；pass=true 表示已有充分证据确认通过。"""

    def user_input(self) -> str:
        return f"""请使用现有 StackPlanner 多智能体流程完成一次标书一致性审核。
【实验编号】{self.case_id}
【标书名称】{self.tender_name}
【审核要点名称】{"；".join(self.rule_names)}
请先委派 researcher 使用唯一绑定的本地知识库充分检索和审核，再委派 reporter 根据研究证据生成最终 JSON Artifact。不要直接回答审核结论。
本次唯一检索工具叫 local_search_tool。委派 researcher 时，若指定 tool_names 或 allowed_tools，只能包含 local_search_tool；不要填写 web_search/web_fetch。
实际标书材料通过绑定知识库的 local_search_tool 提供，执行者会收到完整规则和研究证据。
最终交付是 Reporter 创建的 JSON Artifact；保留 SP2 的产物验收，使用它的准确 artifact ref 完成任务。

{self.task_context()}

【所有委派和修订共同遵守的交付契约】
{self.report_instructions()}
Central 应原样保留此契约，不自行发明字段、改用中文键名或改变 score 的含义。
Reporter 在原生信封的 artifact_content 中返回 JSON 对象，由原生产物适配器保存文件；不要求调用 write_file。
格式合规与审核判断、证据正确性分别评测，不把通过格式校验当成证据已核实。"""

    def report_instructions(self) -> str:
        return f"""审核正文只能是一个 JSON 对象，不使用代码围栏或解释性前后缀。
必须依据 researcher 的检索证据填写 chunk ID、quote 和位置，不编造来源或事实。
quote 可使用原文摘录或忠实概括，不要求是 chunk 的连续子串；不要改动关键数字或把不确定信息说成事实。证据正确性在离线评测阶段判断，不作为运行时交付门槛。
case_id 必须是 {self.case_id}。score 是模型对审核回答的自报置信度，范围为 0–1，不是采购文件里的评分或 0–100 综合得分，也不是需要从原文找到的事实数值。
score 必须是数值，不能改成 null、unknown、TBD 或字符串；信息不足通过 needs_review 和 reference 如实表达，不删除字段或新增 note。
字段必须为：
{{"case_id": {self.case_id}, "pass": false, "needs_review": false, "score": 0.0,
"conclusion": "简明结论", "reference": "判断理由，明确比较哪些条款",
"evidence": [{{"chunk_id": "实际 chunk id", "quote": "支持判断的原文", "position": "实际位置；没有则为空"}}]}}
这是审核结果格式，不是最终结论或固定置信度示例；根据证据独立判断 pass、needs_review 和 score。
外层原生信封的 artifact_content 必须直接放此 JSON 对象，不把它再编码成 JSON 字符串；正常序列化由 SP2 完成。"""


def _rows(path: Path) -> list[dict]:
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = iter(book.worksheets[0].iter_rows(values_only=True))
        headers = [str(value or "").strip() for value in next(rows)]
        return [dict(zip(headers, row, strict=False)) for row in rows if any(value is not None for value in row)]
    finally:
        book.close()


def build_cases(checks_path: Path, ground_truth_path: Path, *, expected_count: int = 20) -> list[AuditCase]:
    checks = [row for row in _rows(checks_path) if row.get("审核要点名称") and row.get("审核点内容")]
    cases = []
    for row in _rows(ground_truth_path):
        if not row.get("序号") or not row.get("标书文件对应的知识库id"):
            continue
        selector = str(row["审核要点名称"])
        matched = [item for item in checks if str(item["审核要点名称"]) in selector]
        matched.sort(key=lambda item: selector.find(str(item["审核要点名称"])))
        if not matched:
            raise ValueError(f"No rule matched case {row['序号']}")
        case = AuditCase(
            int(float(row["序号"])),
            str(row["出错的标书文件名"]),
            str(row["标书文件对应的知识库id"]).strip(),
            tuple(str(item["审核要点名称"]) for item in matched),
            "\n\n".join(str(item["审核点内容"]) for item in matched),
            "、".join(str(item["关键词"]) for item in matched if item.get("关键词")),
        )
        if len(case.task_context()) > 24_000:
            raise ValueError(f"Complete rules exceed context budget for case {case.case_id}; refusing truncation")
        cases.append(case)
    if len(cases) != expected_count or len({case.case_id for case in cases}) != len(cases):
        raise ValueError(f"Expected {expected_count} unique cases, got {len(cases)}")
    return sorted(cases, key=lambda case: case.case_id)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chunk_id: StrictStr
    quote: Annotated[StrictStr, Field(min_length=1)]
    position: StrictStr


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: StrictInt
    passed: StrictBool = Field(alias="pass")
    needs_review: StrictBool
    score: float = Field(ge=0, le=1)
    conclusion: Annotated[StrictStr, Field(min_length=1)]
    reference: Annotated[StrictStr, Field(min_length=1)]
    evidence: list[Evidence]


DELIVERY_VALIDATION_POLICY = "json_schema_only_v2"


def validate_result(raw: str, case: AuditCase, chunks: list[dict] | None = None) -> dict:
    """Validate delivery format only; never repair output or judge evidence.

    The optional legacy ``chunks`` argument is ignored. Source correctness,
    fabricated IDs, quotation fidelity and GT recall belong to offline review.
    """
    validation = {"json_valid": False, "schema_valid": False, "validation_policy": DELIVERY_VALIDATION_POLICY, "evidence_evaluated": False, "evidence_evaluation": "offline_only", "valid": False, "errors": []}
    try:
        value = json.loads(raw)
        validation["json_valid"] = True
        result = AuditResult.model_validate(value)
        if result.case_id != case.case_id:
            raise ValueError("case_id does not match the input")
        validation["schema_valid"] = True
        validation["valid"] = True
        validation["parsed_result"] = result.model_dump(by_alias=True)
    except (ValueError, ValidationError, TypeError) as exc:
        validation["errors"].append(str(exc))
    return validation
