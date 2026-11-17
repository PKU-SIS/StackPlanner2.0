"""Concise run-scoped epistemic boundaries, no case answers or role sequence."""

COMMON = """【通用证据判断】代码候选是线索，不是风险结论；保留其原始表述、自然来源和物理页码。匹配归一化文本不是原件，RAG生成的章节路径不是正文。
涉及错字、符号丢失、错列、拼接或跨页归属的决定性事实，用相应原图确认；未确认则列待核，不能把OCR现象定为原文错误。看过图片不等于已证实OCR文字：独立抄录原图可辨的短句，再与文字通道逐字比较；小字可用view_image的region局部放大。region必须传原生JSON数组，不能把数组写成字符串；参数校验失败后不得原样重试，可改用不带region的整页原图，仍不可辨则如实待核。遮挡、模糊或不能独立转录时只标待核，不用已有OCR补全原图。
单位和不等号优先引用对应comparison中的computed_response_quantities及calculation公式、response_quantity_comparisons；完整quantity_computations是补充。量纲不明不猜算，不用阶段摘要中的心算替代代码公式。non_template_shared_chars仅是当前字面过滤未扣除的字符数，不是已验证非模板或私有原创正文，不能表述为“无法用模板解释”；重复次数也不是独立证据数。
所选相似引用可能对错行，先核同一标的、条目和选项，不能将另一选项当作唯一要求。模板排除必须有可定位的同段公共来源，比较共享部分与共同新增、删改部分；标题属于资格、商务、承诺或响应表，不等于内容全是模板。仅未检索到来源应写来源未确认，不能反向证明原创或私有底稿。正常解释要说明支持证据和仍解释不了的部分，且仅排除其直接解释的现象。单侧错误或实际响应值不同，均不能单独否定同段共同改写/近同；须先对齐同一标的、条目和左右列。
已授权的补查深度、读取数量与何时生成披露局限的报告由Central自主决定，不因partial或未看候选而询问调用者是否继续或是否出报告；真实缺少且无法从现有资料获取的任务必需信息仍可如实询问。本轮只交接新增事实、判断修正和未完成范围，完整明细留在来源文件。摘要精简不得丢掉改变判断的未决事实：某段公共来源未确认时，summary、下一委派和最终报告不能改称全部已溯源或已排除。阶段意见、Central委派中的结论不等于原文；与具体源证据或未决明细冲突时，以可核原文为准并保留局限。同一文件同一页段无新问题时复用已读结果；有冲突只核对决定性双方原文，不反复通读全集。临近预算时如实交接partial与局限，后续角色和是否继续由Central自主决定。最终HTML区分已核事实、风险推断、待核事项；抽查不能写成全部排除，证据不足不等于证实无风险。"""

COMMON += """
【材料角色与参照核验】查看reference_source_observations与material_scope_observations。跨家相同句子先核同页标题、项目名、合同双方、日期和附件角色。历史业绩合同的采购方式、采购人和项目名称不能直接与本次采购要求比较后宣布套用错误；投标附件中出现其他企业不等于本次投标主体写错。历史材料也不能整批排除，仍应检查其中跨主体共享的私有信息、异常改写及真实关联。
模板必须核对同一种格式、同一种声明或承诺、同一事项和条目。投标函不等于承诺函；产品声明不等于成本比例承诺；产品注册证明不等于生产、经营许可。相似度最高只说明字面接近，不证明它是被改写的原始基线。正确模板可能位于相邻页或同文件另一格式，应查看代码提供的其他公共来源定位；可用短固定措辞搜索全文，不能把包含已填写项目名/编号的长句零命中视为没有模板。
代码的填空模板匹配只解释指定短句：保留原始模板文字、实际填充值和物理页码，不把抬头正常填空扩大成后续实质承诺全部正常。共同时间、责任或条件变化必须另核。若正文指向另附采购需求说明、技术参数或补充文件但清单未提供，不能声称已经穷尽招标公共来源，也不能把三家的要求列共文当成独立自撰。原文比阶段摘要优先；Reporter应重新核对关键来源卡片，不能照抄先前错误参照形成的结论。"""

COMMON += """
【参照对应关系与报告口径】原始片段可能被断行截断，不能只因其与另一张表字面更近就认定存在共同改写。优先查看同用途候选参照及其完整原文；声明函、承诺函和投标函之间的差异不等于投标人改写了同一个基线。尚未核实同一种格式、同一条款的对应关系时，只能说来源定位未完成，不能把“候选参照有差异”写成“已核实共同实质改写”“共同偏离招标”。正确参照的页码和原文必须随判断一起交接，不能只传变更词。读者报告不得一面称已排除、一面在局限里承认来源未核；意见、证据段和局限要保持同一口径。
同一响应中“我公司”“我方”“投标人”可能指向同一主体，第一人称与第三人称交替本身不是主语漂移；只有实际主体或义务不同才可评价错误。合同约定转为投标承诺时，主体代词与证明材料所在文书的适配不自动构成异常，须核义务、对象、时点、条件有无实质变化。条目名称之后以同一对象作句子主语也是正常语法，不能因归一化成重词就视为共同错误；是否错误与该句是否同源是两回事。若引用品牌型号作为正常解释，必须有当前项目实际投报材料支持，不能由共享技术文字推断两家品牌相同；未核实品牌则不写该解释。
M03查看requirement_origin_scope_observations：未验证招标来源的requirement只能称“投标书自列要求”，不能在正文改称“招标要求原文”。响应与本方要求相同只证明转抄关系，不能证明该要求由采购人统一给出；未见独立新增可以如实说明，但不能因此宣称已验证公共来源或已排除全部风险。遇到原件指向另附采购需求说明而现有清单没有对应资料，应如实说明参照范围不足，不能把未提供附件都归因于OCR缺页。对清单外来源保留未决即可，无需证明所有外部公版均不存在；重复搜索同一文件不能补出清单外资料。是否继续核查其他候选或生成披露局限的报告仍由Central自主决定。
涉及共同错字或符号，应分别核双方原图；一方原图与另一方OCR一致，不足以写成双方原图均已确认。招标要求格式自拟只说明本招标未给正文，不能证明该措辞不是行业常用语，也不能证明一方改写了另一方。保留真实长段共文和编号平移等线索，区分共同来源现象与串通归因。"""

COMMON += """
【原件版面、文本通道与事实核对】OCR/RAG分块、PDF文本层及按字符提取可能共用同一种错误阅读顺序；两个文字通道一致不等于原图独立验证，章节级重排、跨栏穿插、表格内容提前/滞后、日期漏字也可能由解析产生。不能用“不像随机OCR错字”推断是原件编辑异常。若要把顺序/错列/缺字写成风险，须基于实际可见原图逐项记录编号、相邻标题与位置关系；只读到文字通道或图片调用失败，均只能列解析待核，不能以此增强同源风险。不要把归一化替换计数说成同一技术段中实际发生的改名次数；需要检查这些字段实际位于哪里。
实际投报品牌型号与某份附件出现的厂家名称、通用技术描述分开。用报价表/实际响应的品牌型号列核对每家；尚未核实就省略共同厂家解释及针对该厂商的后续建议，不能从技术承诺函共文反推共用品牌。Reporter按关键原始事实校对报告正文、标题和建议，不把前轮摘要视为核验完成；图片工具成功回执与原图内容认定分开，不编造工具失败。
M03的requirement_origin_scope_observations是基础检测初始状态；response_public_source_observations另提供现有招标原文与页码。请核对这些实际引用，不能只复述初始未核数量而将已经定位的交货、付款等条款写成招标来源未找到。来源候选只解释对齐的那部分，具体数值、条件、义务不同仍须单独核查；未定位的技术要求继续保留来源未确认。某段没有品牌字段不证明已排除厂家公版或行业模板。不同商务条款分别评价，不把其中未定位一条推广为整组都无来源。
完整读过现有参照仍没有对应条款且明确指向清单外附件时，如实保留局限；重复换词检索不能补出未提供附件。对原图已证的共同异常可同时保留真实线索与公共来源未确认，不能要求先证明所有外部来源均不存在，也不因来源待核直接升级为串通结论。审核深度、是否再取证及何时报告仍由Central自主决定。
"""

RULES = {
    "M01": (
        "重点覆盖非模板正文，也覆盖资格声明、承诺、商务条款中偏离公共来源的共同改写。查看common_reference_differences；相"
        "同内容位于表格或资格章节不能直接排除。稀有共同差异比整段相似率更有解释力，但仍需原图和公共来源复核。招标只要求提交某种声明函，不等于提供了该"
        "函全文模板。资格与承诺中的共同时间、责任、条件改写应保留具体双端文字；可认为信号弱或来源未知，不能仅以法条/章节标题把已观察的业务差异整体排"
        "除。目录、投标函固定套语及姓名日期填空不能仅因字面残余或位置顺序相同便升级为私有底稿；须指出超出公共来源的实质文字，公共来源未定位时保留未知。"
    ),
    "M02": (
        "分别判断投标人之间的替换与多个投标人相对公共来源的共同替换；第一人称转换可正常，不能据此排除同段其他异常。查询时点、履约条件或责任承担方的变化属于业务含义变化，不能仅因主体转为第一人称便认定等义；逐项保留原文、差异和未验证的公共来源，不预设风险结论。公司名、姓名、日期、地址的对应变化本身不支持把另一方底稿换名的推断，即使多次重复也不成为多份独立证据；查看replacement_interpretation_facts并另外核实非模板实质句段或异常编辑副作用。证照字段通用与归属主体分开，厂商证照不可当投标人自有材料。"
    ),
    "M03": ("先逐列确认要求、实际响应、偏离标记和支持材料。仅复述采购要求、加品牌或支持/不支持不构成独立原创近同。共享新增措辞、配置和实际值另核；不要把要求列混入响应。风格不同不证明独立编写，只能说明所比较的文字有差异。"),
    "M04": (
        "同标负偏离、同向偏离和同实值必须分开。先分已证同类、已证不同、待核三组，再写总意见；未取得实际值不等于已确认双方不同。查看categorical_response_observations，结构类别相同与附加安全特征、措辞或数值幅度不同应分别陈述，不能用后一项直接否定前一项。单位经代码计算后再比较，类别名称的修饰语或品牌型号不同不能单独证明类别不同；非数值结构先按物理含义分组，不能因没有数值幅度或词形不同便排除同类负向响应。响应与证明材料冲突时核原图，不把截断或错列当实际参数。"
    ),
    "M05": ("同写不适用或空白不能单独认定共用底稿；核表体上下文和适用条件，未看的观察项保留覆盖局限。"),
    "M06": (
        "候选数、不同页数、实际逐对看图数分别统计。对未查看图像只说明未核；同类公版界面不能自动排除全部候选。优先核相同特有缺陷、签章、业务标识和非公版区域，正确区分主体。像素哈希不同不能排除压缩、缩放或裁切后的复用，须根据可见内容差异或有据的公共来源说明排除范围。"
    ),
    "M07": (
        "共同错误必须在双方同一处成立；单侧错误、正常选项填写和共同删去不等号后填写合格边界值不能自动当作共错。同时查看delimiter_structure_findings；分散在整句、网址前后或嵌套中的少开多闭、少闭和交叉错配也可能构成同位共错，不能只查连续符号。逐端抄录异常片段与招标对应片段，说明同一语义位置的新增/继承差异；公共文件另一处也有括号错误不能排除此处共同异常。局部配对正常不等于整段配对正确；结合reference_unit_delimiter_events及原图，分别描述网址等局部和段首、段尾的开闭关系。公共来源同段可有另一种缺口，不能写成整段全部正确，也不能据此排除不同位置的新增异常。全半角字形差异本身不等于配对错误。先核原图排除OCR丢符号、跨单元/跨页截断和正常列表序号。来源未确认不等于已排除，也不等于已证私有底稿。结合common_reference_"
        "differences核共同改写，涉及错字和表格位置以原图为准。先证明错误，再讨论共享原因。如实申报负偏离不等于共同编辑错误，普通产品参数不足或共有配置不能自动升级为高特异编辑指纹；需要另有可核的错字、错置或非模板编辑副作用。各投标人证照、审计报告、授权书填写各自公司、人员"
        "、地址，或标准认证范围不同，不能说成把对方材料换成自己：替换次数也不是独立证据数量。需要额外核出非模板正文或不正常编辑副作用，才可评价派生特"
        "异性；仅标准字段差异不支持高特异归因。"
    ),
    "M08": (
        "异常标点必须比较同一语义位置和源模板；不同段落各有标点不能构成共同异常。查看delimiter_structure_findings及对应源单位观测：检测已补充整段内不相邻的多闭/少开、少闭及嵌套交叉。分别比较同一语义位置的双端符号、正确配对方式和招标基线，不仅看左右括号总数；模板别处的标点异常不能排除投标端新增错配。局部配对正常不等于整段配对正确；结合reference_unit_delimiter_events及原图，分别描述网址等局部和段首、段尾的开闭关系。公共来源同段可有另一种缺口，不能写成整段全部正确，也不能据此排除不同位置的新增异常。全半角字形差异本身不等于配对错误。用原图核验，不把OCR缺字、章节/页段截断、正常序号或HTML结构符号当作纸面共错。检测零命中不能推广为全量格式无异常。经原图确认的共同结构异常应明确保留风险线索，外部模板尚未找到只能列来源待核，不能当作正常解释已证实。"
    ),
    "M09": ("共享客户、共享厂家证件、采购人账户、发证机关代码分开。shared_customer_observations只表示同名客户，需核合同/项目/时间/主体；不同项目不等于同一合同，附属机构也不自动合并。"),
    "M10": (
        "相同软件或默认用户名只属弱信号；不同日期不能证明不同作者。未验证作者编号的分配机制与重复普遍性，不把看似特殊的编号称为唯一用户会话、独立制作几乎不可能或极低碰撞概率。引用pdf_timestamp_observations的原始值、时区及代码UTC换算；无时区不得猜测。PDF二进制元数据不能用OCR或页面水印替代，未扫描文件应明确范围，不反复查文字寻找隐藏元数据。"
    ),
    "M11": ("按真实企业组合统计字体前缀和嵌入字节，前缀非全局随机唯一；不同字节不能排除文本同源。字体未嵌入不等于解析失败；按实际提取回执分别统计未嵌入、提取失败和已比较对象。"),
    "M12": ("共享PDF字典、资源名、跳转名先追踪其引用对象和页面语义。编码块零交集不能推出没有文本复制，任何报告段落均须保持这一边界。内部对象编号没有稀有度模型，不能直接称高特异；未解析的对象保留未决，不重复读取同一摘要冒充复核。"),
    "M13": ("先确认报价归属、币种、数量、含税和供货范围，用实际金额核算价差；不把总价或不同配置的价差混称可比单价。"),
    "M14": ("联结两轮同一主体、标的、数量和版本；缺第二轮资料为不适用/不足，不能把招标模板里的重招词当真实轮次。跨轮金额不变或变化本身均不单独证明协同。"),
    "M15": (
        "主报价只有一行不排除附件有多行耗材报价，应分别说明适用范围；不同品牌型号不直接证明功能、单位或配置不可比，未核可比性须保留待核。最高限价/上限允许更低价格，不是固定价。reference_price_constraints中upper_bound不强制同价；多个投标人"
        "恰好同报上限仍是已观测同价，应结合公开来源、市场和配置说明信号强弱，不能用有上限直接排除，也不能仅同价认定串标。没有市场资料时不得写市场预期"
        "集中、市场价合理或顶格报价是常见策略等既证结论；只能列为尚未验证的解释。"
    ),
}


def guidance(rule_id):
    return COMMON + "\n【本规则重点】" + RULES.get(rule_id, "只按当前完整规则核实证据。")


COLLECTIONS = (
    "requirement_origin_scope_observations",
    "response_public_source_observations",
    "reference_source_observations",
    "material_scope_observations",
    "delimiter_structure_findings",
    "replacement_interpretation_facts",
    "common_reference_differences",
    "quantity_computations",
    "response_quantity_comparisons",
    "categorical_response_observations",
    "reference_price_constraints",
    "shared_customer_observations",
    "pdf_named_destination_observations",
    "pdf_timestamp_observations",
)


def fact_navigation(packet, virtual_root):
    import json

    from .full_results import unpack_complete_result

    collections = unpack_complete_result(packet)["collections"]
    result = {}
    for name in COLLECTIONS:
        rows = collections.get(name, [])
        if not rows:
            continue
        if name == "reference_source_observations":
            rows = sorted(
                rows,
                key=lambda row: (
                    not any(r.get("match_kind") == "explicit_template_slots" and r.get("query_fully_explained_by_literal_template") for r in row.get("reference_matches", [])),
                    not any(r.get("reference_compatibility", {}).get("status") == "same_purpose_observed" for r in row.get("reference_matches", [])),
                    not any(r.get("query_fully_explained_by_literal_template") for r in row.get("reference_matches", [])),
                ),
            )
            # Preview distinct source locations before duplicate observations;
            # all rows and their original order remain in the backing collection.
            seen, distinct, repeated = set(), [], []
            for row in rows:
                signature = tuple((r.get("reference_document_id"), tuple(r.get("reference_source", {}).get("physical_pages") or []), r.get("match_kind")) for r in row.get("reference_matches", [])[:1])
                (repeated if signature in seen else distinct).append(row)
                seen.add(signature)
            rows = distinct + repeated
        if name == "response_public_source_observations":
            seen, distinct, repeated = set(), [], []
            for row in rows:
                signature = tuple(item.get("original_query") for item in row.get("located_texts", []))
                (repeated if signature in seen else distinct).append(row)
                seen.add(signature)
            rows = distinct + repeated
        if name == "material_scope_observations":
            import re

            def material_priority(row):
                text = "\n".join(row.get("scope_markers", [])) + "\n" + "\n".join(x.get("text", "") for x in row.get("source_context", []))
                return (bool(re.search(r"合同编号\s*[:：]\s*[A-Za-z0-9][A-Za-z0-9-]{3,}", text)), bool(re.search(r"(?:采购人[（(]甲方[）)]|甲方|乙方)\s*[:：]\s*[^_\s]{3,}", text)), "政府采购合同" in text)

            rows = sorted(rows, key=material_priority, reverse=True)
        if name == "replacement_interpretation_facts":
            rows = sorted(
                rows,
                key=lambda row: (
                    bool(row.get("person_role_field_context_observed")),
                    bool(row.get("identity_or_contact_field_context_observed") and not row.get("bidder_identity_fragment_substitution") and not row.get("contract_or_response_role_words")),
                ),
                reverse=True,
            )
        if name == "response_quantity_comparisons":
            rows = sorted(rows, key=lambda row: any(q.get("conversion_applied") for endpoint in row.get("comparisons", []) for q in endpoint.get("computed_response_quantities", [])), reverse=True)
        if name == "categorical_response_observations":
            rows = sorted(rows, key=lambda row: bool(row.get("shared_explicit_structure_labels")), reverse=True)
        selected = []
        size = 0
        for row in rows:
            length = len(json.dumps(row, ensure_ascii=False))
            if len(selected) >= 3 or size + length > 16000:
                break
            selected.append(row)
            size += length
        source_index = []
        if name == "common_reference_differences":
            for offset, row in enumerate(rows):
                source_index.append(
                    {
                        "offset": offset,
                        "document_ids": row.get("document_ids"),
                        "shared_reference_edits": row.get("shared_reference_edits"),
                        "selected_reference": {"document_id": row.get("reference_document_id"), "physical_pages": row.get("reference_source", {}).get("physical_pages", []), "quote": row.get("reference_source", {}).get("text", "")},
                        "reference_candidates": row.get("public_source_candidates", []),
                        "sources": [{"document_id": endpoint.get("document_id"), "physical_pages": endpoint.get("physical_pages"), "page": endpoint.get("page")} for endpoint in row.get("bid_sources", [])],
                        "source_excerpt": row.get("bid_sources", [{}])[0].get("text", "")[:90],
                    }
                )
        result[name] = {
            "source_observation_index": source_index,
            "source_observation_index_complete": bool(source_index),
            "count": len(rows),
            "shown": len(selected),
            "records": selected,
            "full_collection_path": virtual_root + "/" + name + ".jsonl",
            "preview_is_not_complete_review": len(selected) < len(rows),
        }
    return result


def decision_source_card(navigation):
    """Small factual discrepancies remain visible beside longer stage opinions."""
    rows = navigation.get("common_reference_differences", {}).get("source_observation_index", [])
    groups = {}
    for row in rows:
        edits = row.get("shared_reference_edits") or []
        if not edits:
            continue
        key = tuple((e["shared"], e["reference"]) for e in edits)
        group = groups.setdefault(key, {"shared_reference_edits": edits, "collection_offsets": [], "document_ids": set()})
        locations = group.setdefault("reference_source_locations", [])
        selected_reference = row.get("selected_reference")
        if selected_reference and selected_reference not in locations:
            locations.append(selected_reference)
        for match in row.get("reference_candidates", []):
            loc = {
                "document_id": match.get("reference_document_id"),
                "physical_pages": match.get("reference_source", {}).get("physical_pages", []),
                "quote": match.get("matched_template_quote", ""),
                "match_kind": match.get("match_kind"),
                "compatibility": match.get("reference_compatibility", {}).get("status"),
            }
            if loc not in locations and len(locations) < 3:
                locations.append(loc)
        group["reference_correspondence_verified"] = False
        group["collection_offsets"].append(row["offset"])
        group["document_ids"].update(row.get("document_ids") or [])
    return [{**group, "document_ids": sorted(group["document_ids"]), "note": "词法共同改动，须核实原图及该段公共来源；章节或文件名称本身不能证明这些改动由模板规定，不自动判风险。"} for group in groups.values()]
