"""Run reproducible Qwen comparisons against local SP2 and DeerFlow gateways.

The script intentionally stores raw state, run metadata, events, uploads, and
visible answers.  It does not score prose with another LLM: deterministic checks
are performed separately so the comparison remains auditable.

Usage::

    SP2_EXPERIMENT_EMAIL=... SP2_EXPERIMENT_PASSWORD=... \
      uv run python scripts/compare_sp2_deerflow_qwen.py --case case-01
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = ROOT / "experiments" / "sp2-vs-deerflow-qwen-20260728"
FIXTURES = EXPERIMENT_ROOT / "fixtures"
RAW_ROOT = EXPERIMENT_ROOT / "raw"


@dataclass(frozen=True)
class Deployment:
    key: str
    base_url: str
    public_url: str
    assistant_id: str
    context: dict[str, Any]


DEPLOYMENTS = {
    "sp2": Deployment(
        key="sp2",
        base_url=os.getenv("SP2_EXPERIMENT_BASE_URL", "http://127.0.0.1:8101"),
        public_url="http://123.59.6.244:2027",
        assistant_id="stackplanner",
        context={
            "model_name": "qwen3-32b",
            "mode": "ultra",
            "thinking_enabled": False,
            "is_plan_mode": False,
            "subagent_enabled": False,
            "reasoning_effort": None,
        },
    ),
    "deerflow": Deployment(
        key="deerflow",
        base_url=os.getenv(
            "DEERFLOW_EXPERIMENT_BASE_URL",
            "http://127.0.0.1:8103",
        ),
        public_url="http://123.59.6.244:2026",
        assistant_id="lead_agent",
        context={
            "model_name": "qwen3-32b",
            "mode": "ultra",
            "thinking_enabled": False,
            "is_plan_mode": False,
            "subagent_enabled": True,
            "reasoning_effort": None,
        },
    ),
}


CASES: dict[str, dict[str, Any]] = {
    "case-01-ambiguous-intake": {
        "title": "高风险报告的需求澄清与续接",
        "turns": [
            "请为 Project Aurora 写一份提交给董事会的复盘报告，并给出下一阶段建议。请开始。",
            ("补充信息：复盘范围是 2026 年第二季度，读者是董事会；重点比较北区、南区、西区的营收、毛利率和流失率，报告要有执行摘要、风险、建议和数据局限。目前我还没有给你实际数值，也不允许联网或虚构数据。请基于这些约束继续。"),
        ],
    },
    "case-02-short-memory-pressure": {
        "title": "连续修订下的短期/中期任务记忆",
        "turns": [
            "建立活动方案台账：城市=上海；预算=120000元；人数=100；日期=2026-08-30；餐标=180元；主题=云帆；备用场地=浦东A厅。先确认收到，不要总结。",
            "永久约束A：禁酒；所有后续方案都必须满足。只确认收到。",
            "修订1：城市改为杭州，上海作废。只确认收到。",
            "永久约束B：需要中英双语投影。只确认收到。",
            "修订2：预算改为90000元，120000作废。只确认收到。",
            "永久约束C：场地必须无障碍。只确认收到。",
            "修订3：人数改为80，100作废。只确认收到。",
            "修订4：日期改为2026-09-06，2026-08-30作废。只确认收到。",
            "修订5：餐标改为220元，180作废。只确认收到。",
            "修订6：主题改为星桥，云帆作废。只确认收到。",
            "修订7：备用场地改为西湖B厅，浦东A厅作废。只确认收到。",
            "永久约束D：最晚在2026-08-20冻结供应商。只确认收到。",
            "补充偏好：流程轻松，不安排高强度团建；这不是可牺牲约束。只确认收到。",
            "补充风险：9月可能下雨，必须包含室内备选流程。只确认收到。",
            "质量要求：最终答案不得把任何已作废值写成当前值。只确认收到。",
            "预算口径：90000元是总预算，餐标220元/人包含在总预算内。只确认收到。",
            "输出口径：最终先给一个严格JSON对象，再给不超过5条建议。只确认收到。",
            ("现在输出最终结果。JSON必须包含 city、budget_cny、attendees、date、meal_cny_per_person、theme、backup_venue、vendor_freeze_date、hard_constraints、risks；随后给不超过5条可执行建议。"),
        ],
    },
    "case-03-multidoc-report": {
        "title": "多文档冲突消解与可下载报告",
        "uploads": ["aurora_baseline.md", "aurora_correction.md", "aurora_policy.md"],
        "turns": [
            (
                "请只依据本消息附带的三个 Project Aurora 文件生成一份完整的董事会 Markdown "
                "报告。必须使用当前权威数据，明确说明旧数据已被替代，逐区判断 readiness，"
                "保留所有来源标签、决策/行动编号、审计标记，并将报告作为可下载文件交付。"
                "禁止联网，禁止虚构。"
            )
        ],
    },
    "case-04-report-revision": {
        "title": "报告产物的增量修订与版本保真",
        "uploads": ["aurora_baseline.md", "aurora_correction.md", "aurora_policy.md"],
        "turns": [
            ("请只依据附带的三个 Project Aurora 文件生成完整的董事会 Markdown 报告，使用当前权威数据、来源标签和审计标记，并作为可下载文件交付。禁止联网。"),
            ("修订刚才的报告：把执行结论移到最前面，并新增一段阈值敏感性建议；除此之外必须保留原有当前指标表中的全部数值、来源标签、CUR-R9、RENEW-14、REVIEWED-HIGH 和 AURORA-AUDIT-7319。请交付修订后的可下载 Markdown 文件。"),
        ],
    },
    "case-05-search-failure": {
        "title": "不可检索事实下的有界停止",
        "turns": [("请联网查询虚构交易所 ORCHID-QUASAR-9Z-7319 在 2026-07-27 的官方收盘价，只能引用交易所官方页面。若检索不到，请明确说明无法验证并停止；不得猜测，最多尝试3个不同搜索查询。")],
    },
    "case-06-price-rule-correction": {
        "title": "优惠门槛更正后的实付价格纠错",
        "turns": [
            ("我在两家店选洗衣机。A店标价3200元，会员打9折，满3000元再减300元，服务费按打折后商品价的8%收，运费60元；店员说满减门槛按标价判断。B店全部算下来是3140元。先帮我算选哪家便宜、差多少。"),
            ("我刚问清楚，店员前面说错了：A店满3000元的门槛其实看会员打折后的商品价，服务费还是按打折后的商品价算，运费不参与。B店仍是3140元。请重新检查你上次的结论，告诉我最后选谁、两边各付多少、差多少。"),
        ],
    },
    "case-07-rooming-constraint-correction": {
        "title": "分房约束变化后的方案回退与唯一性验证",
        "turns": [
            ("我要给6个朋友分3间双人房。小安只能和小陈或小丁住，小博只能和小娥住，小芳只能和小陈或小丁住。前台排的是小安和小陈、小博和小娥、小丁和小芳。先帮我看看这样是否满足要求，并把分房结果写清楚。"),
            ("我刚收到消息，小安和小陈也不能同住，其他要求不变。请检查你上次的安排，重新排房；不要漏人、不要重复，最后说明新安排是不是唯一的。"),
        ],
    },
    "case-08-range-script-repair": {
        "title": "区间解析脚本的边界条件发现与修复",
        "turns": [
            ("帮我写个Python小脚本，把“1-3,5,7-9”这种写法展开成数字列表。还要支持倒序区间，去重但保留数字第一次出现的顺序。请实际运行几个例子检查，然后把代码文件给我下载。"),
            ("我又试了一下，输入“-3--1,2”会出问题。负数和负数区间也要支持，这个例子的结果应该是[-3,-2,-1,2]。请检查之前的做法哪里不对，修好后把原来的例子和这个新例子都实际跑一遍，再给我修订后的代码文件。"),
        ],
    },
    "case-09-hotel-price-correction": {
        "title": "酒店结算口径更正后的复杂价格复核",
        "turns": [
            (
                "我在比较两家酒店。A酒店房费标价4680元，会员打88折；满4200元减380元，"
                "前台说门槛按标价判断。服务费是折后房费的6%，周末附加费是标价的7%。"
                "税是9%，计税基础为“折后房费减优惠券，再加服务费和周末附加费”；"
                "停车费135元不计税。B酒店最终一共5108.50元。所有中间数先别四舍五入，"
                "只把最后实付保留两位小数。先帮我算选哪家、两边各付多少、差多少。"
            ),
            ("我刚确认，前台把优惠券门槛说错了：满4200元其实看会员折后的房费，其他计算规则都不变。请重新检查上次的结论，最后明确选哪家、A和B各付多少、差多少，并把关键算式列出来。"),
        ],
    },
    "case-10-rooming-all-solutions": {
        "title": "分房规则更正后的全方案与唯一性复核",
        "turns": [
            (
                "8个朋友要分4间双人房。小安只能和小陈或小丁住；小博只能和小娥或小芳住；"
                "小高不能和小慧住；小陈不能和小慧住；小丁不能和小娥住。前台现在排的是："
                "小安和小陈、小博和小娥、小丁和小高、小芳和小慧。先帮我检查这个安排"
                "是否符合规则，并把结果写清楚。"
            ),
            ("我刚收到更正：小安和小陈也不能同住，其他规则都不变。请重新分房。别找到一种就停，把所有可行方案都列出来；调换房间编号、或者交换同一房间两个人的先后，都不算新的方案。最后说明新安排是不是唯一的。"),
        ],
    },
    "case-11-rooming-six-solutions": {
        "title": "多处分房更正后的全量穷举",
        "turns": [
            (
                "12个朋友要分6间双人房。小安只能和小陈或小丁住；小博只能和小娥或小芳住；"
                "小高只能和小慧或小杰住；小凯只能和小琳或小明住。前台现在排的是："
                "小安和小陈、小博和小娥、小高和小慧、小凯和小琳、小丁和小芳、小杰和小明。"
                "先帮我检查这个安排是否符合规则。"
            ),
            (
                "我刚收到三处更正：小安和小陈不能同住，小博和小娥不能同住，"
                "小凯和小琳不能同住；其他规则都不变。请重新分房，把所有可行方案都列出来，"
                "别找到一种就停。调换房间编号、或者交换同一房间两人的先后，都不算新方案。"
                "最后告诉我一共有几种，是不是唯一。"
            ),
        ],
    },
    "case-12-report-factual-correction": {
        "title": "报告事实纠错后的反思回退与证据保真",
        "uploads": ["aurora_baseline.md", "aurora_correction.md", "aurora_policy.md"],
        "turns": [
            ("请只依据附带的三个 Project Aurora 文件，做一份完整的董事会 Markdown 报告。请写清当前指标、各区域是否达到门槛、来源优先级和历史审计，并把报告做成可下载文件。不要联网，也不要虚构。"),
            (
                "我看了一下，你刚才对南区门槛的说法有错：南区毛利率30.1%已经达到30.0%，"
                "只有流失率5.1%没有达到“不高于5.0%”。所以最小边界改进只是把流失率降低"
                "0.1个百分点到5.0%，不能再说毛利率也没过线。请反查并修订刚才的文件；"
                "其他当前指标、来源说明和历史审计都不要丢，尤其保留 CORRECTION-JULY、"
                "REVIEWED-HIGH、RENEW-14、CUR-R9、POLICY-V3、AURORA-AUDIT-7319、"
                "BASELINE-JUNE、OLD-R7。给我修订后的可下载 Markdown。"
            ),
        ],
    },
    "case-13-lightweight-reflective-report": {
        "title": "轻量判断后的纠错反思与完整报告",
        "uploads": ["aurora_baseline.md", "aurora_correction.md", "aurora_policy.md"],
        "turns": [
            ("请先快速核对这三个 Project Aurora 文件，只用两三句话告诉我南区现在是否 ready、具体是哪项指标没过线。暂时不要做报告或文件，不要联网。"),
            (
                "我发现你刚才遗漏了一个关键纠错点：南区毛利率30.1%已经达到30.0%，"
                "只有流失率5.1%超过5.0%；最小边界改进是把流失率降低0.1个百分点到5.0%。"
                "请反查上一步，然后只依据这三个文件生成一份完整、可下载的中文董事会 "
                "Markdown 报告。其他当前指标、来源说明和历史审计都不要丢，尤其保留 "
                "CORRECTION-JULY、REVIEWED-HIGH、RENEW-14、CUR-R9、POLICY-V3、"
                "AURORA-AUDIT-7319、BASELINE-JUNE、OLD-R7。不要联网，不要虚构。"
            ),
        ],
    },
    "case-14-public-multiwoz-state": {
        "title": "MultiWOZ公开对话中的跨领域短期状态追踪",
        "turns": [
            (
                "下面做一个公开 MultiWOZ 2.2 对话的状态追踪测试。我会逐段贴出原始客服"
                "对话。请只记住客户当前仍然有效的需求，不要替客户搜索、不要联网；后续"
                "修改覆盖旧值。前几轮都只回复“收到。”。\n\n"
                "片段1：\n"
                "USER: I'm looking for an expensive restaurant in the centre.\n"
                "SYSTEM: I can recommend several restaurants in the centre of town. "
                "Are you looking for any type of food in particular?\n"
                "USER: Yes, Caribbean food please."
            ),
            (
                "片段2：\n"
                "SYSTEM: There is no matching restaurant. Would you like to change "
                "your search criteria?\n"
                "USER: What about a restaurant that serves european food?\n"
                "SYSTEM: I found 2 expensive european restaurants and 1 expensive "
                "modern european restaurant. Which kind would you prefer?\n"
                "USER: I don't have a preference.\n\n"
                "更新当前需求，只回复“收到。”。"
            ),
            (
                "片段3：\n"
                "SYSTEM: Okay, I can book your table at Eraina. They serve european "
                "food. How many in your party, what day and time please?\n"
                "USER: I would like to book for Tuesday at 12:45.\n"
                "SYSTEM: Are you looking to book for just yourself?\n"
                "USER: Yes. Just one person.\n\n"
                "更新当前需求，只回复“收到。”。"
            ),
            ("片段4：\nSYSTEM: Great your reference number is 2K1P6FTA. Thank you.\nUSER: I also need to find a train from stansted airport to cambridge that arrives by 8:00 on tuesday.\n\n保留餐厅需求并增加火车需求，只回复“收到。”。"),
            (
                "片段5：\n"
                "SYSTEM: Alright, there are three different trains leaving that "
                "morning that will get you there on time. Each one has a duration "
                "of twenty-eight minutes, should I book one of them?\n"
                "USER: Yes please, just one ticket. Could I get the reference number "
                "for the train you book please?\n"
                "SYSTEM: OK, I've booked you 1 ticket on TR0514, departing stansted "
                "airport at 7:24 and arriving cambridge at 7:52.\n\n"
                "更新当前需求，只回复“收到。”。"
            ),
            (
                "现在根据所有片段输出严格 JSON，不要联网，不要解释。JSON 只能有 "
                "restaurant 和 train 两个顶层对象。restaurant 必须包含 area、food、"
                "price_range、name、day、time、people；train 必须包含 departure、"
                "destination、day、arrive_by、people。全部使用小写英文字符串，people "
                "使用整数。只保留客户最终有效需求；已经被替代的 caribbean 不得出现。"
            ),
        ],
    },
    "case-15-public-sgd-correction": {
        "title": "SGD公开对话中的槽位覆盖与失败状态保真",
        "turns": [
            (
                "下面做一个公开 Schema-Guided Dialogue 对话的状态追踪测试。我会逐段贴出"
                "原始客服对话。只记住当前有效的预订参数和最终预订状态，不要联网。前几轮"
                "只回复“收到。”。\n\n"
                "片段1：\n"
                "USER: I want to make a reservation at a restaurant.\n"
                "SYSTEM: Is there a particular restaurant that you wish to visit?\n"
                "USER: Can you look at Villa Romano in Napa?\n"
                "SYSTEM: Yes. What time?\n"
                "USER: Try for afternoon 12:45.\n"
                "SYSTEM: Please confirm: reservation for 2 at Villa Romano in Napa, "
                "today at 12:45 pm."
            ),
            ("片段2：\nUSER: Make it for 4 people.\nSYSTEM: Okay, please confirm: reservation for 4 people at Villa Romano.\n\n更新当前状态，只回复“收到。”。"),
            ("片段3：\nUSER: Actually, can we change it to Big 4.\nSYSTEM: Please confirm: table for 4 people at The Big 4.\nUSER: Yes, that's right.\n\n更新当前状态，只回复“收到。”。"),
            ("片段4：\nSYSTEM: I'm sorry, I couldn't make that reservation for you.\nUSER: No, that's it.\n\n更新当前状态，只回复“收到。”。"),
            (
                "现在只输出一个严格 JSON 对象，不要解释。字段固定为 restaurant_name、"
                "location、date、time、number_of_seats、booking_status。使用客服最终确认"
                "后的名称；date 和 time 保留对话中的字面值；number_of_seats 使用整数；"
                "booking_status 只能是 success 或 failed。不得出现已经被替代的 "
                "Villa Romano 或人数2。"
            ),
        ],
    },
    "case-16-public-worldbank-query-state": {
        "title": "世界银行公开API查询条件的多轮覆盖与最终执行",
        "turns": [
            ("我准备查询世界银行 Indicators API。先记录查询条件：国家是中国、印度、日本；年份是2022年；指标是 GDP（current US$，代码 NY.GDP.MKTP.CD）。现在不要联网，只回复“收到。”。"),
            ("把指标改为人均 GDP（current US$，代码 NY.GDP.PCAP.CD）；总 GDP 指标作废。不要联网，只回复“收到。”。"),
            ("把日本替换为巴西；中国和印度继续保留。不要联网，只回复“收到。”。"),
            ("把年份改为2023年，2022年作废。不要联网，只回复“收到。”。"),
            ("再增加人口总数指标 SP.POP.TOTL，其他条件不变。不要联网，只回复“收到。”。"),
            ("撤销刚才增加的人口指标；国家替换、2023年和人均GDP修改仍然有效。不要联网，只回复“收到。”。"),
            (
                "现在执行当前有效查询，只能使用世界银行官方 Indicators API。输出严格 "
                "JSON 数组，不要解释。每项字段固定为 country、iso3、year、indicator、"
                "value_usd；按 iso3 升序排列，value_usd 四舍五入到小数点后2位。不得"
                "出现日本、2022年、总GDP或人口指标；如果官方值缺失就填 null，不得回退"
                "到其他年份。"
            ),
        ],
    },
    "case-17-public-multiwoz-domain-collision": {
        "title": "MultiWOZ公开对话中的跨领域日期防串线",
        "turns": [
            (
                "下面做一个公开 MultiWOZ 2.2 对话的状态追踪测试。我会逐段贴出原始客服"
                "对话。请只记住客户当前有效的火车和餐厅需求，不要搜索、不要联网；后续"
                "明确修改覆盖旧值。前几轮只回复“收到。”。\n\n"
                "片段1：\n"
                "USER: I'd like a train that is departing from Cambridge and is "
                "going to London Liverpool street.\n"
                "SYSTEM: Would you like to narrow it down by day and/or time?\n"
                "USER: I would like to leave on Thursday after 16:15."
            ),
            (
                "片段2：\n"
                "SYSTEM: We have ten choices for you with departures every two hours "
                "beginning at 7:27.\n"
                "USER: I just want to be clear. This is 7:27 friday morning correct? "
                "In that case, I'd like to book that for 5 people with reference "
                "number, please.\n"
                "SYSTEM: Booking was successful.\n\n"
                "按客户最新确认更新火车需求，只回复“收到。”。"
            ),
            (
                "片段3：\n"
                "USER: Do I pick up the tickets there, as well?\n"
                "SYSTEM: That is correct. Can I help with anything else?\n"
                "USER: Yes, actually. Are there any moderately priced restaurants "
                "that serve British food?\n\n"
                "保留火车需求并增加餐厅需求，只回复“收到。”。"
            ),
            (
                "片段4：\n"
                "SYSTEM: I found five restaurants. Do you have a preference in "
                "location?\n"
                "USER: No, I don't. What would you recommend?\n"
                "SYSTEM: I like The Oak Bistro in the centre. Would you like me to "
                "make a reservation?\n"
                "USER: Yes please book it for 5 people.\n\n"
                "更新餐厅需求，只回复“收到。”。"
            ),
            ("片段5：\nSYSTEM: Please verify the date and time for the restaurant booking.\nUSER: I'd like it for thursday at 10:45.\nSYSTEM: All booked!\n\n更新餐厅需求，只回复“收到。”。"),
            (
                "现在输出严格 JSON，不要解释。JSON 只能有 train 和 restaurant 两个顶层"
                "对象。train 字段固定为 departure、destination、day、leave_at、people；"
                "restaurant 字段固定为 area、food、price_range、name、day、time、people。"
                "全部使用小写英文字符串，people 使用整数。注意两个领域的日期不同；只保留"
                "最终有效值，火车最初的 thursday 和 16:15 已被替代。"
            ),
        ],
    },
}


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _message_type(message: dict[str, Any]) -> str:
    return str(message.get("type") or message.get("role") or "")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        if isinstance(part, str):
            chunks.append(part)
        elif isinstance(part, dict):
            value = part.get("text") or part.get("content")
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(item.strip() for item in chunks if item.strip()).strip()


def _visible_answer(messages: list[dict[str, Any]], start_index: int) -> str:
    answers: list[str] = []
    for message in messages[start_index:]:
        if not isinstance(message, dict):
            continue
        kwargs = message.get("additional_kwargs")
        if isinstance(kwargs, dict) and kwargs.get("hide_from_ui") is True:
            continue
        msg_type = _message_type(message)
        if msg_type == "ai":
            text = _content_text(message.get("content"))
            if text:
                answers.append(text)
        elif msg_type == "tool" and message.get("name") in {"ask_clarification", "sp_ask_human"}:
            text = _content_text(message.get("content"))
            if text:
                answers.append(f"[需要澄清]\n{text}")
    return "\n\n".join(answers).strip()


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or event.get("event") or "unknown")


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_counts = Counter(_event_type(event) for event in events)
    tool_counts: Counter[str] = Counter()
    search_queries: list[str] = []
    seen_tool_calls: set[tuple[str, str]] = set()
    for node in _walk(events):
        name = node.get("name") or node.get("tool_name")
        if isinstance(name, str) and name:
            if name in {
                "web_search",
                "web_fetch",
                "read_file",
                "write_file",
                "present_files",
                "present_file",
                "sp_delegate",
                "sp_summarize",
                "sp_finish",
                "sp_ask_human",
                "sp_reflect",
                "sp_revise",
                "sp_backtrack",
                "sp_replan",
            }:
                call_id = str(node.get("tool_call_id") or node.get("id") or "")
                identity = (name, call_id or json.dumps(node, ensure_ascii=False, sort_keys=True, default=str))
                if identity not in seen_tool_calls:
                    seen_tool_calls.add(identity)
                    tool_counts[name] += 1
        if name == "web_search":
            args = node.get("args") or node.get("arguments") or node.get("input")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    pass
            if isinstance(args, dict):
                query_value = args.get("query") or args.get("q")
                if isinstance(query_value, str) and query_value not in search_queries:
                    search_queries.append(query_value)
    return {
        "event_type_counts": dict(sorted(event_counts.items())),
        "tool_call_counts": dict(sorted(tool_counts.items())),
        "search_queries": search_queries,
    }


def _memory_summary(values: dict[str, Any]) -> dict[str, Any]:
    raw = values.get("sp_task_memory")
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
    history = raw.get("history") if isinstance(raw.get("history"), list) else []
    all_entries = [entry for entry in [*entries, *history] if isinstance(entry, dict)]
    return {
        "active_entry_count": len(entries),
        "history_entry_count": len(history),
        "action_counts": dict(sorted(Counter(str(entry.get("action") or "unknown") for entry in all_entries).items())),
        "status_counts": dict(sorted(Counter(str(entry.get("status") or "unknown") for entry in all_entries).items())),
        "summary_entries": [entry for entry in all_entries if entry.get("action") == "summarize"],
    }


def _artifact_summary(values: dict[str, Any]) -> dict[str, Any]:
    refs = values.get("sp_current_artifact_refs")
    if not isinstance(refs, dict):
        return {}
    history = refs.get("_history") if isinstance(refs.get("_history"), list) else []
    return {
        "current": {key: value for key, value in refs.items() if key != "_history"},
        "history": history,
    }


class GatewaySession:
    def __init__(self, deployment: Deployment, email: str, password: str) -> None:
        self.deployment = deployment
        self.email = email
        self.password = password
        self.client = httpx.AsyncClient(
            base_url=deployment.base_url,
            follow_redirects=True,
            timeout=httpx.Timeout(420.0, connect=15.0),
            trust_env=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def login(self) -> None:
        response = await self.client.post(
            "/api/v1/auth/login/local",
            data={"username": self.email, "password": self.password},
        )
        response.raise_for_status()
        csrf = self.client.cookies.get("csrf_token")
        if not csrf:
            raise RuntimeError(f"{self.deployment.key}: login did not issue csrf_token")
        self.client.headers["X-CSRF-Token"] = csrf

    async def healthcheck(self) -> None:
        response = await self.client.get("/openapi.json", timeout=15.0)
        response.raise_for_status()

    async def clear_long_term_memory(self) -> None:
        response = await self.client.delete("/api/memory")
        response.raise_for_status()

    async def create_thread(self, case_id: str, title: str) -> str:
        response = await self.client.post(
            "/api/threads",
            json={
                "assistant_id": self.deployment.assistant_id,
                "metadata": {
                    "title": f"Qwen对照实验｜{case_id}｜{title}",
                    "experiment": "sp2-vs-deerflow-qwen-20260728",
                    "case_id": case_id,
                    "system": self.deployment.key,
                    "model": "qwen3-32b",
                },
            },
        )
        response.raise_for_status()
        return str(response.json()["thread_id"])

    async def state(self, thread_id: str) -> dict[str, Any]:
        response = await self.client.get(f"/api/threads/{thread_id}/state")
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def runs(self, thread_id: str) -> list[dict[str, Any]]:
        response = await self.client.get(f"/api/threads/{thread_id}/runs")
        response.raise_for_status()
        data = response.json()
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def events(self, thread_id: str, run_id: str) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"/api/threads/{thread_id}/runs/{run_id}/events",
            params={"limit": 2000},
        )
        response.raise_for_status()
        data = response.json()
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def upload(self, thread_id: str, names: list[str]) -> list[dict[str, Any]]:
        if not names:
            return []
        handles = []
        try:
            files = []
            for name in names:
                handle = (FIXTURES / name).open("rb")
                handles.append(handle)
                files.append(("files", (name, handle, "text/markdown")))
            response = await self.client.post(f"/api/threads/{thread_id}/uploads", files=files)
            response.raise_for_status()
            data = response.json()
            return data.get("files", []) if isinstance(data, dict) else []
        finally:
            for handle in handles:
                handle.close()

    async def run_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        upload_info: list[dict[str, Any]],
    ) -> dict[str, Any]:
        state_before = await self.state(thread_id)
        values_before = state_before.get("values") if isinstance(state_before.get("values"), dict) else {}
        messages_before = values_before.get("messages") if isinstance(values_before.get("messages"), list) else []
        runs_before = await self.runs(thread_id)
        run_ids_before = {str(item.get("run_id")) for item in runs_before}

        additional_kwargs: dict[str, Any] = {}
        if upload_info:
            additional_kwargs["files"] = [
                {
                    "filename": item.get("filename"),
                    "size": item.get("size"),
                    "path": item.get("virtual_path"),
                    "status": "uploaded",
                }
                for item in upload_info
            ]
        payload = {
            "assistant_id": self.deployment.assistant_id,
            "input": {
                "messages": [
                    {
                        "type": "human",
                        "content": [{"type": "text", "text": prompt}],
                        "additional_kwargs": additional_kwargs,
                    }
                ]
            },
            "config": {"recursion_limit": 150},
            "context": {**self.deployment.context, "thread_id": thread_id},
            "metadata": {
                "experiment": "sp2-vs-deerflow-qwen-20260728",
                "model": "qwen3-32b",
                "system": self.deployment.key,
            },
            "stream_mode": ["values", "custom"],
            "stream_subgraphs": True,
            "on_disconnect": "continue",
        }
        started = time.monotonic()
        response = await self.client.post(f"/api/threads/{thread_id}/runs/wait", json=payload)
        elapsed = time.monotonic() - started
        response.raise_for_status()
        wait_response = response.json()

        state_after = await self.state(thread_id)
        values_after = state_after.get("values") if isinstance(state_after.get("values"), dict) else {}
        messages_after = values_after.get("messages") if isinstance(values_after.get("messages"), list) else []
        runs_after = await self.runs(thread_id)
        new_runs = [item for item in runs_after if str(item.get("run_id")) not in run_ids_before]
        new_runs.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
        run = new_runs[-1] if new_runs else (runs_after[0] if runs_after else {})
        run_id = str(run.get("run_id") or "")
        events = await self.events(thread_id, run_id) if run_id else []
        answer = _visible_answer(messages_after, len(messages_before))
        return {
            "prompt": prompt,
            "elapsed_seconds": round(elapsed, 3),
            "wait_response": wait_response,
            "state": state_after,
            "run": run,
            "events": events,
            "answer": answer,
            "event_summary": _event_summary(events),
            "message_count_before": len(messages_before),
            "message_count_after": len(messages_after),
        }

    async def download_artifacts(
        self,
        *,
        thread_id: str,
        state: dict[str, Any],
        destination: Path,
    ) -> list[dict[str, Any]]:
        urls: list[str] = []
        virtual_paths: list[str] = []
        for node in _walk(state):
            value = node.get("artifact_url")
            if isinstance(value, str) and "/artifacts/" in value and value not in urls:
                urls.append(value)
            name = node.get("name") or node.get("tool_name")
            args = node.get("args") or node.get("arguments")
            if name in {"present_files", "present_file"} and isinstance(args, dict):
                paths = args.get("filepaths") or args.get("paths")
                if isinstance(paths, str):
                    paths = [paths]
                if isinstance(paths, list):
                    virtual_paths.extend(path for path in paths if isinstance(path, str) and path.startswith("/mnt/user-data/outputs/"))
        for virtual_path in dict.fromkeys(virtual_paths):
            url = f"/api/threads/{thread_id}/artifacts{virtual_path}"
            if url not in urls:
                urls.append(url)
        saved: list[dict[str, Any]] = []
        destination.mkdir(parents=True, exist_ok=True)
        for index, url in enumerate(urls, 1):
            try:
                response = await self.client.get(url, params={"download": "true"})
                response.raise_for_status()
                raw_name = url.rstrip("/").split("/")[-1].split("?", 1)[0]
                filename = quote(raw_name, safe="._-") or f"artifact-{index}"
                path = destination / filename
                path.write_bytes(response.content)
                saved.append(
                    {
                        "artifact_url": url,
                        "saved_path": str(path.relative_to(EXPERIMENT_ROOT)),
                        "size": len(response.content),
                        "content_type": response.headers.get("content-type"),
                    }
                )
            except Exception as exc:  # evidence collection must not erase run results
                saved.append({"artifact_url": url, "error": repr(exc)})
        return saved


async def _run_deployment(
    *,
    deployment: Deployment,
    case_id: str,
    case: dict[str, Any],
    email: str,
    password: str,
    reset_long_term_memory: bool,
) -> dict[str, Any]:
    session = GatewaySession(deployment, email, password)
    case_dir = RAW_ROOT / case_id / deployment.key
    case_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{case_id}] {deployment.key}: login", flush=True)
    try:
        await session.healthcheck()
        await session.login()
        if reset_long_term_memory:
            await session.clear_long_term_memory()
        thread_id = await session.create_thread(case_id, str(case["title"]))
        print(f"[{case_id}] {deployment.key}: thread={thread_id}", flush=True)
        upload_info = await session.upload(thread_id, list(case.get("uploads") or []))
        if upload_info:
            _json_dump(case_dir / "uploads.json", upload_info)

        turn_summaries: list[dict[str, Any]] = []
        final_state: dict[str, Any] = {}
        for index, prompt in enumerate(case["turns"], 1):
            print(f"[{case_id}] {deployment.key}: turn {index}/{len(case['turns'])}", flush=True)
            result = await session.run_turn(
                thread_id=thread_id,
                prompt=str(prompt),
                upload_info=upload_info if index == 1 else [],
            )
            turn_dir = case_dir / f"turn-{index:02d}"
            _json_dump(turn_dir / "wait-response.json", result["wait_response"])
            _json_dump(turn_dir / "state.json", result["state"])
            _json_dump(turn_dir / "run.json", result["run"])
            _json_dump(turn_dir / "events.json", result["events"])
            (turn_dir / "query.md").write_text(str(prompt).strip() + "\n", encoding="utf-8")
            (turn_dir / "answer.md").write_text((result["answer"] or "[无可见回答]") + "\n", encoding="utf-8")

            state_values = result["state"].get("values") if isinstance(result["state"].get("values"), dict) else {}
            summary = {
                "turn": index,
                "elapsed_seconds": result["elapsed_seconds"],
                "run": result["run"],
                "answer": result["answer"],
                "event_summary": result["event_summary"],
                "memory_summary": _memory_summary(state_values),
                "artifact_summary": _artifact_summary(state_values),
                "message_count_before": result["message_count_before"],
                "message_count_after": result["message_count_after"],
            }
            _json_dump(turn_dir / "summary.json", summary)
            turn_summaries.append(summary)
            final_state = result["state"]
            print(
                f"[{case_id}] {deployment.key}: turn {index} done ({result['elapsed_seconds']:.1f}s, status={result['run'].get('status')}, tokens={result['run'].get('total_tokens')})",
                flush=True,
            )

        artifacts = await session.download_artifacts(
            thread_id=thread_id,
            state=final_state,
            destination=case_dir / "artifacts",
        )
        manifest = {
            "case_id": case_id,
            "title": case["title"],
            "system": deployment.key,
            "assistant_id": deployment.assistant_id,
            "model": "qwen3-32b",
            "context": deployment.context,
            "long_term_memory_reset_before_case": reset_long_term_memory,
            "thread_id": thread_id,
            "thread_url": f"{deployment.public_url}/workspace/chats/{thread_id}",
            "uploads": upload_info,
            "turns": turn_summaries,
            "artifacts": artifacts,
        }
        _json_dump(case_dir / "manifest.json", manifest)
        failure_path = case_dir / "failure.json"
        if failure_path.exists():
            failure_path.unlink()
        return manifest
    except Exception as exc:
        failure = {
            "case_id": case_id,
            "system": deployment.key,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "repr": repr(exc),
        }
        _json_dump(case_dir / "failure.json", failure)
        print(f"[{case_id}] {deployment.key}: FAILED {exc!r}", flush=True)
        return failure
    finally:
        await session.close()


async def _run_case(
    case_id: str,
    email: str,
    password: str,
    *,
    max_turns: int | None = None,
    reset_long_term_memory: bool = False,
    systems: list[str] | None = None,
) -> dict[str, Any]:
    case = CASES[case_id]
    if max_turns is not None:
        case = {**case, "turns": case["turns"][:max_turns]}
    print(f"\n=== {case_id}: {case['title']} ===", flush=True)
    selected = systems or ["sp2", "deerflow"]
    completed = await asyncio.gather(
        *[
            _run_deployment(
                deployment=DEPLOYMENTS[system],
                case_id=case_id,
                case=case,
                email=email,
                password=password,
                reset_long_term_memory=reset_long_term_memory,
            )
            for system in selected
        ]
    )
    manifests = dict(zip(selected, completed, strict=True))
    for system in ("sp2", "deerflow"):
        if system in manifests:
            continue
        manifest_path = RAW_ROOT / case_id / system / "manifest.json"
        if manifest_path.exists():
            manifests[system] = json.loads(manifest_path.read_text(encoding="utf-8"))
    comparison = {
        "case_id": case_id,
        "title": case["title"],
        "query_turns": case["turns"],
        "sp2": manifests.get("sp2", {"error": "not run"}),
        "deerflow": manifests.get("deerflow", {"error": "not run"}),
    }
    _json_dump(RAW_ROOT / case_id / "comparison.json", comparison)
    return comparison


def rebuild_existing(case_ids: list[str]) -> int:
    """Recompute derived answers/counts from immutable raw states and events."""
    for case_id in case_ids:
        comparison_path = RAW_ROOT / case_id / "comparison.json"
        if not comparison_path.exists():
            print(f"[{case_id}] no saved comparison; skipped", flush=True)
            continue
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        for system in ("sp2", "deerflow"):
            case_dir = RAW_ROOT / case_id / system
            manifest_path = case_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for turn in manifest.get("turns", []):
                index = int(turn["turn"])
                turn_dir = case_dir / f"turn-{index:02d}"
                state = json.loads((turn_dir / "state.json").read_text(encoding="utf-8"))
                events = json.loads((turn_dir / "events.json").read_text(encoding="utf-8"))
                values = state.get("values") if isinstance(state.get("values"), dict) else {}
                messages = values.get("messages") if isinstance(values.get("messages"), list) else []
                answer = _visible_answer(messages, int(turn.get("message_count_before") or 0))
                turn["answer"] = answer
                turn["event_summary"] = _event_summary(events)
                turn["memory_summary"] = _memory_summary(values)
                turn["artifact_summary"] = _artifact_summary(values)
                _json_dump(turn_dir / "summary.json", turn)
                (turn_dir / "answer.md").write_text((answer or "[无可见回答]") + "\n", encoding="utf-8")
            _json_dump(manifest_path, manifest)
            comparison[system] = manifest
        _json_dump(comparison_path, comparison)
        print(f"[{case_id}] rebuilt derived evidence", flush=True)
    return 0


async def async_main(
    case_ids: list[str],
    *,
    rebuild_only: bool = False,
    max_turns: int | None = None,
    reset_long_term_memory: bool = False,
    systems: list[str] | None = None,
) -> int:
    if rebuild_only:
        return rebuild_existing(case_ids)
    email = os.getenv("SP2_EXPERIMENT_EMAIL", "").strip()
    password = os.getenv("SP2_EXPERIMENT_PASSWORD", "")
    if not email or not password:
        raise SystemExit("Set SP2_EXPERIMENT_EMAIL and SP2_EXPERIMENT_PASSWORD")
    results = []
    for case_id in case_ids:
        results.append(
            await _run_case(
                case_id,
                email,
                password,
                max_turns=max_turns,
                reset_long_term_memory=reset_long_term_memory,
                systems=systems,
            )
        )
    _json_dump(
        RAW_ROOT / "index.json",
        {
            "experiment": "sp2-vs-deerflow-qwen-20260728",
            "email": email,
            "model": "qwen3-32b",
            "case_ids": case_ids,
            "results": results,
        },
    )
    return 1 if any("error" in result.get(system, {}) for result in results for system in ("sp2", "deerflow")) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        choices=sorted(CASES),
        help="Case to run (repeatable); defaults to all cases",
    )
    parser.add_argument(
        "--rebuild-existing",
        action="store_true",
        help="Recompute answers and summaries from already saved raw state without new model runs",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        help="Run only the first N turns of each selected case (smoke/debug use)",
    )
    parser.add_argument(
        "--reset-long-term-memory",
        action="store_true",
        help="Clear the logged-in test user's long-term memory before each case",
    )
    parser.add_argument(
        "--system",
        dest="systems",
        action="append",
        choices=sorted(DEPLOYMENTS),
        help="Run only this deployment (repeatable); omitted means both",
    )
    args = parser.parse_args()
    if args.max_turns is not None and args.max_turns <= 0:
        parser.error("--max-turns must be positive")
    return asyncio.run(
        async_main(
            args.case_ids or list(CASES),
            rebuild_only=args.rebuild_existing,
            max_turns=args.max_turns,
            reset_long_term_memory=args.reset_long_term_memory,
            systems=args.systems,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
