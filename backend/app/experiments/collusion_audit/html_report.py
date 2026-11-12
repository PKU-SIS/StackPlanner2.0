"""Opt-in reader-facing HTML, separate from technical evidence and SP metadata."""

import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

import html5lib

SECTIONS = ("overview", "scope", "findings", "explanations", "limitations", "recommendations", "sources")
TEMPLATE = Path(__file__).with_name("templates") / "reader-report.html"
INTERNAL_IDS = re.compile(r"(?<![A-Za-z0-9])(?:M(?:0\d{1,2}|1[0-5])|d\d{1,4}|[CE]\d{6})(?![A-Za-z0-9])|\b(?:spact|spart|audit_run)[_-]", re.I)
INTERNAL_NAMES = re.compile(
    r"(?<![A-Za-z0-9])(?:run_id|code_status|audit_run_id|finding_count|candidate_count|next_offset|artifact_metadata|source_scope|source_artifact_ids|SP2?|CentralAgent|Central|Reporter|Coder|Researcher|Perception)(?![A-Za-z0-9])|/(?:mnt|data\d*|home|tmp|project|knowledge)/|file://",
    re.I,
)
BLOCKS = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "h1", "h2", "h3", "li", "blockquote", "figure", "figcaption", "tr", "td", "th", "br"}
VOID = {"meta", "br", "hr", "img", "wbr", "col", "input", "source", "area", "base", "embed", "link", "param", "track"}


def _internal_marker_feedback(text):
    """Give native revision an exact, bounded location; never rewrite output."""
    matches = sorted([*INTERNAL_IDS.finditer(text), *INTERNAL_NAMES.finditer(text)], key=lambda match: (match.start(), match.end()))
    if not matches:
        return None
    samples = []
    for match in matches[:3]:
        excerpt = re.sub(r"\s+", " ", text[max(0, match.start() - 32) : min(len(text), match.end() + 32)])
        samples.append(f"标记“{match.group()}”（附近原文：“{excerpt}”）")
    return (
        "HTML中不得出现内部编号、运行字段、SP角色或宿主机路径；技术信息只留原生metadata。"
        f"共发现{len(matches)}处，定位示例：" + "；".join(samples) + "。请按定位逐一改为自然称谓，并检查全文其他同类残留；不要只宣称已清理。这里只反馈错误，不修改报告。"
    )


READER_GUIDANCE = """
【报告生成顺序】直接基于已交接事实组织最终HTML并调用write_file，不先在阶段回复中输出整页草稿。若收到交付检查失败及具体定位，逐一处理反馈所列的违规词和附近原文，并检查其他同类残留；保持事实、原文引文和已披露局限不变，不以“已经清理”的声明替代实际文件修订。文件写入成功后立即返回简短原生阶段协议，勿重新展开取证或重复整页。模板CSS可原样复用，删除无用装饰，不额外生成复杂图表。
【面向读者的HTML交付要求】最终报告是一个可离线打开、可打印的独立中文HTML文件，含内联CSS、UTF-8与手机视口；参照reader_report_context.layout_template的结构与视觉层级，可调整排版，不能复制占位符或预填任何结论。
先说清楚本项检查的意见及最重要依据，再说检查什么、实际检查到哪里、发现和双端证据、正常解释、未核事项、具体建议及原件定位。短段落、对照表、逐字引文和有意义的高亮优先；数据图只用实际事实及清楚的分母，优先静态SVG/CSS，不编造概率、风险分数、图像或截图，不把同一证据重复加权。
用企业全称、自然文档名和PDF物理页码（从1起）定位，DOCX写章节/表格；原件内部对象可用中文解释的对象号及字节位置。document_labels只是内部映射，请转换为自然名称。正文、标题、表格、图、脚注、注释和HTML属性均不得出现内部规则/文档/证据/候选编号、计算或执行编号、宿主机路径、英文工具参数、SP角色名、token及模型/工具调用开销。源文件中真实的项目编号、设备型号、字体标记或重要PDF字节本身可以引用并解释，不冒充系统编号。
统计和页码按本轮真实工具回执核对，reader_source_facts提供自然文档与实际返回页码的辅助对应，不能把另一投标人的页码写到本企业。证据定位表只列实际用于相应发现的位置；上游没有明确给出“企业—材料内容—页码”对应关系时，回查来源或不列该项，不能猜填。该辅助表不一定涵盖所有来源，未列出不等于未读。
不新增无可靠统计口径的数字卡片；检查主体数与风险涉及主体数必须区分。工具接收记录数不等于语义复核数；没有可靠去重审核台账时，用自然语言说明重点核对的企业、页段和未覆盖范围，不写精确已复核数或“其余多少未复核”。已知同口径计数做加减必须前后一致，近似措辞也不能掩盖算术矛盾。图像规则已有真实判读回执统计时，仍按原有自然中文覆盖表披露。
技术编号、计算状态、集合字段和完整追踪放原生metadata/日志，不能藏在HTML注释或data属性。没有技术字段不等于删除审核局限：把它们译成具体的业务事实，例如“部分扫描页未能定位文字”“只逐图核对了其中四组”，不要说code_status=partial或finding_count。
保留真实原文，不把原件的错字改正确；用mark突出关键差异。只用已收到的事实和来源，不能编造页码、厂家、市场解释；按已核实/尚未核实区分正常解释。“可能来自公共模板”不等于已排除；未检索到不等于未提交，未读不等于没有OCR。对已完成范围说明发现风险线索/未发现充分线索/证据不足/不适用，未发现不等于全项目无风险。
正文总长度（包括表格和引文）通常约1200至2500汉字，重点是能顺畅阅读而非机械凑字数。建议按模板组织七项业务内容；section id和表格数量不是验收门槛，来源表与双端对照易读。
独立文件只能使用内联CSS/静态SVG/片段锚点，不加载脚本、远端字体、CDN、外部图片、iframe或表单。引用原文里的<、>和&须正确HTML转义。
不得给来源编造下载地址，用自然文档名和页码即可。
证据等级用完整中文原词，放在证据说明里即可。原生返回协议继续使用summary、artifact_type、artifact_content、artifact_metadata；这些不是HTML正文的一部分。created_paths必须是本次实际创建的.html定稿；成功写文件后不要把整页再次嵌套到阶段JSON。完整HTML工具参数与阶段终态JSON是两种输出，约1200-token的短交接目标只约束后者，不要求把完整HTML压入该协议正文；直接依据已交接事实组织报告，勿在阶段回复重复整页草稿。不用Markdown/代码围栏/技术附录替代HTML，不把资料局限误当报告未完成。
【本任务实际工具能力，仅用于调度信息，不进入读者报告】Researcher已注册本地证据研究能力：可使用现成审核工具、RAG检索、连续原文读取和原图核验，不以互联网检索为前提。Coder也可执行现成计算与补充校核，不要求修改源码；Perception可做任务/输入感知与受托只读检查；Reporter负责组织读者报告，并可在实际工具允许的范围内核对来源。
每次委派最终工具集以实际注册能力与Central所选tool_names为准，不声称使用未提供的工具。此处描述能力，不规定必须使用某角色；角色与先后顺序仍由Central自主选择。任务感知完成不表示后续证据研究已完成。原生将Coder的纯文件读取请求归一化为Perception；“已完成感知禁止重入”与“连续同角色委派需控制检查点”是不同限制，控制检查点不会重开已经完成的感知阶段。
收到前者时，应依据当前真实任务是后续证据研究、计算、复核还是报告，以及已注册工具能力，自主选择合法工作安排，不重复把同一读取委派当成新的环境感知；不把阶段路由限制误写成数据缺失或整个审核无法继续。这里只解释既有约束，不指定下一角色，不改变原生路由和阶段门。
【取证范围与现有预算】本配置在单次委派约16条工具结果、或至少4条结果且工具文本累计约20万字符时预留收尾；这是单次委派边界，不是父任务审核上限。按现有预算自主组织可交付的小范围问题，不把所有未核事项塞进同一次委派的必做清单。候选总数、数据警告和未核范围不自动等于全部逐条审核的前置门槛；必要补查以规则判断所需证据为依据，结论强度与实际覆盖匹配，受托承诺未完成仍如实partial。
M01可用audit_m01_index按文档对（document_pair是d001|d007这样的普通字符串，非JSON数组）、类别或匹配方式定位原始候选索引，可用sort_by=shared_chars直接定位共享文字最多的记录，source_locations供核对同一企业实际页段、避免重复；无须为少量候选顺序通读巨大索引；索引只导航，双端正文仍须实际读取。它不改变任何候选、排名、规则或判断标准，不指定角色、步骤及是否补查。
【自然语言阶段交接】Central在中间取证/复核委派的expected_output中，应要求按自然企业名组织的简短新增判断、关键对照及来源定位、实际覆盖与未核事项；不把候选逐条长表、逐条长引文或技术编号数组设为阶段验收前置。
完整持久化代码与原文保留且可按需读取，不要为交接重复转抄。可合并相同事实的重叠窗口，不能省略独立的实质发现、排除依据或未核事项；不以缩短交接代替实际核验，不把未完写成完成。
所有阶段均遵守reader_source_facts.coverage_disclosure：本轮没有去重语义台账，不在中间summary、artifact_content或Central委派中累计精确已复核/剩余候选数；已有模型阶段中的此类计数也不是可信台账，最终用自然企业、材料和页段表达实际覆盖。图像规则真实回执支持的计数保持独立原口径。
中间阶段仍返回原生JSON协议；内部标识和必要覆盖回执可保留在metadata，关键新增判断用简明自然语言表达。若当前明确受托核验尚未完成，如实partial与evidence_gaps；后续角色、步骤和是否补查继续由原生Central自主决定。
上述交接要求不取代最终HTML报告：最终报告仍须呈现所有实质发现、双端原文与自然来源、正常解释和局限，不以短摘要替代报告。
图像审核的覆盖表使用自然中文行名：“逐对查看图像”“逐对核对扫描文字”“涉及不同页面”，分别给实际组数、实际组数、去重页数；metadata仍保留真实offset列表和页数用于核验，页面绝不展示offset或内部编号。
首次说明一组是候选两端图像的一次对照；“涉及不同页面”指全部自动候选端点去重的原件总页数，不等于实际已逐图查看的页数，不能将两种覆盖混写。
"""


def template_text():
    return TEMPLATE.read_text(encoding="utf-8")


def reader_context(rule, manifest):
    from .data import allowed_rule_fields

    labels = {}
    roles = {"bid": "投标文件", "quote_detail": "分项报价表", "quote_opening": "开标一览表", "tender": "招标文件"}
    for doc in manifest.get("documents", []):
        label = (doc.get("bidder") or "") + "《" + roles.get(doc.get("role"), "项目原件") + "》"
        if doc.get("role") == "tender":
            label += "（Word版）" if Path(doc.get("path", "")).suffix.lower() == ".docx" else "（PDF版）"
        labels[doc["id"]] = label
    return {
        "project_name": manifest.get("project_name") or "当前采购项目",
        "review_name": rule.get("规则名称", "单项审核"),
        "rule": {k: rule[k] for k in allowed_rule_fields if k != "合并规则ID" and k in rule},
        "document_labels": labels,
        "layout_template": template_text(),
    }


def html_protocol(text):
    """Project delivery wording only; intermediate evidence instructions remain."""
    # The reader guide already states the target format, including literal negations.
    if READER_GUIDANCE in text:
        return READER_GUIDANCE.join(html_protocol(part) for part in text.split(READER_GUIDANCE))
    text = text.replace("Markdown", "HTML")
    text = re.sub(r"(?<!parsed)\.md(?![A-Za-z])", ".html", text)
    text = text.replace("规则、真实计算run_id和status、关键事实与来源", "检查目标、关键事实与自然来源定位")
    text = text.replace(
        "报告含：规则与范围；风险意见（发现风险线索/未发现充分风险线索/证据不足/不适用）；计算run_id、工具状态和口径；关键事实与主体；证据ID、原件document_id/物理页码或对象摘要；正常解释及排除；复核范围与覆盖缺口。",
        "报告含：审核意见、检查目标与实际范围、具体发现和双端原文、正常解释及验证程度、局限、后续建议、自然语言原件定位。内部标识与技术状态仅留原生metadata，不进入读者页面。",
    )
    text = text.replace("报告含规则范围、风险意见、计算run_id/状态、事实与证据定位、正常解释排除、复核覆盖和缺口；", "报告含审核意见、范围、双端原文及自然来源、正常解释、覆盖局限和建议；")
    return text


class ReaderDocument(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ids = set()
        self.stack = []
        self.errors = []
        self.h1 = 0
        self.lang = ""
        self.charset = False
        self.viewport = False
        self.tables = 0
        self.quotes = 0
        self.style_depth = 0
        self.styles = []
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "iframe", "object", "embed", "form", "input", "button", "base", "link", "foreignobject"}:
            self.errors.append("页面不得加载脚本、外部资源或交互表单。")
        for name, value in attrs.items():
            if name.startswith("on") or name == "srcdoc":
                self.errors.append("页面不得包含事件脚本。")
            if name in {"src", "href", "xlink:href", "action"} and value:
                allowed = value.startswith("#") or (tag == "img" and re.match(r"^data:image/(?:png|jpeg|webp);base64,", value))
                if not allowed:
                    self.errors.append("页面资源和链接必须可离线使用，不得暴露宿主机地址。")
            if name == "style" and re.search(r"url\s*\(|expression\s*\(", value or "", re.I):
                self.errors.append("内联样式不得引用外部资源或脚本。")
        if tag == "html":
            self.lang = attrs.get("lang", "")
        if tag == "meta":
            self.charset |= attrs.get("charset", "").lower() in {"utf-8", "utf8"}
            self.viewport |= attrs.get("name", "").lower() == "viewport"
        if tag == "section" and attrs.get("id"):
            self.ids.add(attrs["id"])
        self.h1 += tag == "h1"
        self.tables += tag == "table"
        self.quotes += tag == "blockquote"
        if tag == "style":
            self.style_depth += 1
        if tag in BLOCKS:
            self.parts.append("\n")
        if tag == "tr":
            self.row = []
        if tag in {"th", "td"}:
            self.cell = []
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "style":
            self.style_depth = max(0, self.style_depth - 1)
        if tag in {"th", "td"} and self.row is not None and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag not in VOID:
            if not self.stack or self.stack[-1] != tag:
                self.errors.append("HTML标签必须完整且正确嵌套。")
            elif self.stack:
                self.stack.pop()
        if tag in BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.style_depth:
            self.styles.append(data)
        else:
            self.parts.append(data)
            if self.cell is not None:
                self.cell.append(data)

    @property
    def text(self):
        return "".join(self.parts)


@lru_cache(maxsize=32)
def parse_reader_html(text):
    # Parse an in-memory HTML5 DOM; never rewrite the model-authored file.
    tree = html5lib.parse(text, namespaceHTMLElements=False)
    normalized = html5lib.serialize(tree, omit_optional_tags=False, quote_attr_values="always")
    document = ReaderDocument()
    document.feed(normalized)
    document.close()
    return document


@lru_cache(maxsize=32)
def _format_review(text, expected_grade):
    errors, warnings = [], []
    if not re.match(r"\s*<!doctype\s+html\s*>", text, re.I) or not re.search(r"</html>\s*$", text, re.I):
        errors.append("最终报告须为完整的独立HTML5页面，不能是片段或截断文件。")
    try:
        doc = parse_reader_html(text)
    except (ValueError, TypeError):
        return (*errors, "无法解析HTML页面。"), ()
    errors += doc.errors
    body = html5lib.parse(text, namespaceHTMLElements=False).find("body")
    visible = "".join(body.itertext()).strip() if body is not None else ""
    if not visible or not re.search(r"<body(?:\s|>)", text, re.I):
        errors.append("最终HTML必须有实际报告正文，不能是空壳。")
    # HTML5 permits omitted end tags. The normalized DOM, not an XML-like
    # raw-source stack, is the reference for text and table extraction.
    if not doc.lang.lower().startswith("zh") or not doc.charset or not doc.viewport or doc.h1 != 1:
        warnings.append("建议使用中文语言标记、UTF-8、手机视口和一个主标题。")
    if set(SECTIONS) - doc.ids:
        warnings.append("建议按审核意见、范围、发现、正常解释、局限、建议和来源组织内容。")
    if doc.tables + doc.quotes == 0:
        warnings.append("可用证据对照表或引文增强可读性。")
    if "{{" in text or "请填入真实证据" in doc.text:
        errors.append("报告不能保留模板占位符。")
    marker_error = _internal_marker_feedback(text)
    if marker_error:
        errors.append(marker_error)
    if re.search(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b|\btokens?\b|模型调用|工具调用|运行编号|执行编号", doc.text, re.I):
        warnings.append("建议将工具字段译成业务含义，避免暴露系统开销；原件真实标记可解释后引用。")
    if re.search(r"@import|url\s*\(|expression\s*\(", "".join(doc.styles), re.I):
        errors.append("CSS须内联且不得加载外部资源。")
    if expected_grade and expected_grade not in doc.text:
        warnings.append("建议在正文说明完整中文证据等级；元数据一致性独立校验。")
    return tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings))


def html_report_review(text, *, expected_grade="", rule_id=None, metadata=None):
    """Cached delivery errors plus non-blocking reader advice; no text repair."""
    errors, cached_warnings = _format_review(text, expected_grade)
    warnings = list(cached_warnings)
    if rule_id == "M06":
        doc = parse_reader_html(text)
        values = metadata or {}
        expected = {"逐对查看图像": len(values.get("visual_reviewed_candidates") or []), "逐对核对扫描文字": len(values.get("ocr_reviewed_candidates") or []), "涉及不同页面": values.get("candidate_page_count")}
        for label, number in expected.items():
            rows = [row for row in doc.rows if len(row) >= 2 and row[0].strip() == label]
            found = re.fullmatch(r"\s*(\d+)\s*(?:组|页)?\s*", rows[0][1]) if len(rows) == 1 else None
            if number is None or not found or int(found[1]) != number:
                warnings.append(label + "建议使用自然语言如实披露；真实覆盖元数据与原始回执仍单独核验。")
    return {"errors": list(errors), "warnings": list(dict.fromkeys(warnings))}


def validate_html_report(text, *, expected_grade="", rule_id=None, metadata=None):
    """Only actual delivery errors block native reporting/FINISH."""
    return html_report_review(text, expected_grade=expected_grade, rule_id=rule_id, metadata=metadata)["errors"]
