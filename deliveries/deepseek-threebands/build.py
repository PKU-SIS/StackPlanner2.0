"""Archive tested three-band source and independently verify saved results. Offline only."""
from pathlib import Path
import json,hashlib,datetime,shutil
OUT=Path(__file__).resolve().parent; ROOT=OUT.parent; REPO=Path('/data2/pkuiti/zmy/projects/reviews/StackPlanner2-sp2DeepResearch-20261103')
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
profiles={10000:ROOT/'outline-self-budget-10k-fourcases-20261117/case3',20000:ROOT/'autism-case24-20k-entry-fixed-rerun-20261117',30000:ROOT/'outline-self-budget-newcase33-20261117'}
profile_info={}
for band,src in profiles.items():
 m=read(src/'manifest.json');dst=OUT/'profiles'/str(band);dst.mkdir(parents=True,exist_ok=True)
 shutil.copytree(src/'code',dst/'code',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','.ruff_cache','.deer-flow','.env','.env.*','config.yaml','local-storage'))
 assert all(sha(dst/'code'/rel)==h for rel,h in m['runtime_hashes'].items())
 save(dst/'RUNTIME_HASHES.json',m['runtime_hashes'])
 save(dst/'PROFILE.json',dict(final_contract=m['final_contract'],body_range=m['body_range'],model=m['model'],runtime_hashes_source=str(src/'manifest.json'),source_manifest_sha256=sha(src/'manifest.json'),source_file_count=len(m['runtime_hashes']),writer_model_unchanged=True,archive_is_actual_tested_source=True))
 shutil.copy2(src/'manifest.json',dst/'ORIGINAL_MANIFEST.json')
 driver=dst/'runner';driver.mkdir(exist_ok=True)
 for filename in m['helper_hashes']:
  assert sha(src/filename)==m['helper_hashes'][filename]
  shutil.copy2(src/filename,driver/filename)
 for f in ('pyproject.toml','uv.lock'):shutil.copy2(REPO/'backend'/f,dst/'code/backend'/f)
 cell=m['cells'][0];save(dst/'EXAMPLE_QUERY.json',read(src/'runs'/cell['name']/'query.json'))
 profile_info[band]=dict(directory=f'profiles/{band}',source=str(src),runtime_files=len(m['runtime_hashes']),source_manifest_sha256=sha(src/'manifest.json'),final_contract=m['final_contract'],body_range=m['body_range'])
rows=[];variants={}
specs=[(10000,i,label,ROOT/'outline-self-budget-10k-fourcases-20261117'/f'case{i}') for i,label in ((3,'中国金融细分领域前景'),(17,'低代码／无代码平台'),(27,'AI与人工心理咨询结合'),(36,'单件小批制造自动化'))]
specs += [(20000,i,label,ROOT/'outline-self-budget-20k-fourcases-20261117'/f'case{i}') for i,label in ((7,'房地产与地方财政'),(19,'Prometheus高流失率'))]
specs += [(20000,24,'自闭症学生课堂参与',ROOT/'autism-case24-20k-entry-fixed-rerun-20261117'),(20000,34,'MoS₂接触电阻',ROOT/'outline-self-budget-20k-fourcases-20261117/case34')]
specs += [(30000,21,'AI在教育中的应用与人才培养',ROOT/'outline-self-budget-20261116'),(30000,6,'全球具身智能技术路线',ROOT/'outline-self-budget-threecases-20261117/case6'),(30000,25,'中性粒细胞与脑缺血',ROOT/'outline-self-budget-threecases-20261117/case25'),(30000,30,'全球南方',ROOT/'global-south-continuation-20261117'),(30000,35,'市政污水治理与效率',ROOT/'outline-self-budget-threecases-20261117/case35'),(30000,33,'先进芯片工艺的金属薄膜沉积',ROOT/'outline-self-budget-newcase33-20261117')]
def event_usage(path):
 attempts={}
 for line in path.read_text().splitlines():
  e=json.loads(line);d=e.get('data') or {}
  if e['mode']=='custom' and d.get('type')=='task_completed' and d.get('usage'):
   key=(d.get('task_id') or d.get('a2a_task_id'),(d.get('result_envelope') or {}).get('task_id'));assert key[1]
   assert key not in attempts or attempts[key]==d['usage']
   attempts[key]=d['usage']
 return sum(v['total_tokens'] for v in attempts.values())
def central(path):return sum((m.get('usage_metadata') or {}).get('total_tokens',0) for m in read(path).get('messages',[]) if isinstance(m,dict))
for band,case_id,label,src in specs:
 m=read(src/'manifest.json');r=read(src/'results.json');is_cont=case_id==30 and band==30000
 run=src if is_cont else src/'runs'/m['cells'][0]['name']
 if is_cont:
  oldrun=Path(m['source_run']);original=central(oldrun/'state.json')+event_usage(oldrun/'events.jsonl');additional=central(src/'state.json')-central(src/'original-state.json')+event_usage(src/'events.jsonl');assert original==r['original_generation_tokens'] and additional==r['additional_generation_tokens'];tokens=original+additional
 else:tokens=central(run/'state.json')+event_usage(run/'events.jsonl')
 assert tokens==r['generation_tokens'],(case_id,tokens,r['generation_tokens'])
 report=run/'evaluated-report.md';assert sha(report)==r['report_sha256']
 assert all(sha(src/'code'/rel)==h for rel,h in m['runtime_hashes'].items())
 race=src/'race'
 race=race/('global-south-recovered' if is_cont else m['cells'][0]['name'])
 race_result=read(race/'result.json');assert race_result['report_sha256']==r['report_sha256']
 calls=[read(p) for p in (race/'calls').glob('*.json') if not p.name.endswith(('.pending.json','.error.json'))];assert sum(x['usage']['total_tokens'] for x in calls)==r['race_tokens']
 expected=read(OUT/'profiles'/str(band)/'RUNTIME_HASHES.json');actual=m['runtime_hashes'];differences=[p for p,h in actual.items() if expected.get(p)!=h];removed=sorted(set(expected)-set(actual));variant=None
 if differences or removed:
  fingerprint=hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest()[:16];variant=f'band{band}-{fingerprint}'
  if variant not in variants:
   vdir=OUT/'historical-runtime-variants'/variant
   for rel in differences:
    target=vdir/'overrides'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src/'code'/rel,target)
   save(vdir/'VARIANT.json',dict(base_profile=str(band),runtime_hashes=actual,copy_overrides=differences,delete_runtime_files=removed,source=str(src),note='Copy base profile code, remove delete_runtime_files, then copy overrides preserving relative paths; reconstructs tested runtime hashes.'))
   variants[variant]=dict(base_profile=band,override_files=differences,deleted_files=removed)
 note='普通单次生成'
 if case_id==24:note='入口修复后从头重跑；原失败11.44万Token另计'
 if is_cont:note='缺章修复后续跑；Token含原始405.05万＋续跑54.83万'
 if case_id==36:note='补研引用校验失败记录保留；Token已按原生attempt复核'
 if case_id==6 and band==30000:note='旧题复测；RACE从保存响应离线恢复，未追加调用'
 row=dict(band=band,case_id=case_id,label=label,words=r['words'],race=r['race_overall_percent'],generation_tokens=tokens,race_tokens=r['race_tokens'],deviation_percent=r['deviation_percent'],formal_delivered=r['formal_delivered'],notes=note,source=str(src),source_results_sha256=sha(src/'results.json'),report_sha256=r['report_sha256'],report_source=str(report),historical_runtime_variant=variant)
 rows.append(row)
 evidence=OUT/'results'/str(band)/f'case{case_id}';evidence.mkdir(parents=True,exist_ok=True);shutil.copy2(src/'results.json',evidence/'ORIGINAL_RESULTS.json');save(evidence/'ROW.json',row);shutil.copy2(run/'query.json' if not is_cont else src/'query.json',evidence/'QUERY.json');shutil.copy2(race/'result.json',evidence/'RACE_RESULT.json')
failures=[dict(band=20000,case_id=24,status='original_attempt_no_report',words=None,race=None,generation_tokens=114403,race_tokens=0,source=str(ROOT/'outline-self-budget-20k-fourcases-20261117/case24'),included_in_main_rows=False),dict(band=30000,case_id=30,status='original_attempt_missing_n9',words=None,race=None,generation_tokens=4050514,race_tokens=0,source=str(ROOT/'outline-self-budget-threecases-20261117/case30'),included_in_main_rows=True,inclusion='already included in continuation total; do not add again')]
save(OUT/'RESULTS.json',dict(reports=rows,prior_failed_attempts=failures))
lines=['# SP2 Outline自选预算版：1万／2万／3万档实测汇总','', '截至2026-11-17，共14篇完整报告。Token统一以万计；生成Token含缓存输入和原生修复，RACE评分Token另计。全文字数为最终引用显示后的mixed-word-estimate-v1估算，非Microsoft Word实测。','', '| 档位 | 题目 | 全文字数 | RACE | 生成Token/万 | 评分Token/万 |','|---|---|---:|---:|---:|---:|']
for x in rows:lines.append(f"| {x['band']//10000}万 | {x['label']}{'（重跑）' if x['case_id']==24 else '（续跑）' if x['case_id']==30 else ''} | {x['words']:,} | {x['race']:.2f} | {x['generation_tokens']/10000:.2f} | {x['race_tokens']/10000:.2f} |")
lines+=['','| 档位 | Outline正文预算区间／默认 | 完整报告数 | 全文字数在±10%内 | 生成Token合计/万 | 评分Token合计/万 |','|---|---|---:|---:|---:|---:|']
for band in profiles:
 rr=[x for x in rows if x['band']==band];pr=profile_info[band]['body_range'];lines.append(f"| {band//10000}万 | {pr['min_words']:,}–{pr['max_words']:,}／{pr['default_words']:,} | {len(rr)} | {sum(abs(x['deviation_percent'])<=10 for x in rr)}/{len(rr)} | {sum(x['generation_tokens'] for x in rr)/10000:.2f} | {sum(x['race_tokens'] for x in rr)/10000:.2f} |")
lines+=['','说明：','', '1. 自闭症列入口修复后从头重跑结果23,985字；此前未进入报告流程，失败耗用11.44万Token另计，两次合计358.18万Token。','2. 全球南方列缺章修复后续跑结果31,377字；459.88万生成Token包含原运行405.05万及续跑54.83万，不重复加计原失败。','3. 制造业题补研引用协议校验失败记录保留，后续报告完成并交付；287.53万生成Token已按不同原生attempt复核，包含被旧计量方式覆盖的63.14万。','4. 本表汇总同一Outline自选预算方法的各次实测，不是完全相同源码的一轮测试。入口修复、缺章兜底以及各档提示词演进记录均保留；3万档具身智能、全球南方、脑缺血、污水为此前已测题复测，不能当作全新题。','5. RACE采用固定官方评分资料与DeepSeek评委，非官方榜单成绩，FACT未运行。各题单次结果不能证明重复运行稳定性。','', '快照：profiles/10000、profiles/20000、profiles/30000，各保存该档最近实际测试的源码、预算合同、运行器、依赖清单和文件SHA。历史测试所用源码与最新快照的差异另保存在historical-runtime-variants，逐题对应见RESULTS.json。']
(OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
(OUT/'README.md').write_text('三个档位的已测快照与结果\n\n先看REPORT.md。profiles目录分别保存1万/2万/3万档最近实际测试源码，不在归档时补改代码。1万与2万快照含显式report_length入口修复；3万快照来自case33，含缺章兜底、尚未应用后来新增的入口修复。1万恢复归档简洁Writer提示；2万保留其测试时Writer提示，3万保留3万专用提示。不能将三档视为逐字相同源码。\n\nPROFILE.json保存篇幅合同、正文推荐预算、模型设置及来源。ORIGINAL_MANIFEST.json和RUNTIME_HASHES.json用于核验来源；runner为实际运行脚本，它仍引用原实验环境的路径，迁移时需配置对应原仓库、依赖与凭据。代码快照包含当时冻结的backend/scripts、harness、tests、skills和query数据，原项目Gateway/frontend等未包含；不是独立部署包。backend/pyproject.toml及uv.lock附作依赖参考，不执行安装或新实验。\n\n历史数据引用多个修复阶段。historical-runtime-variants保存相对主快照的运行源码覆盖文件、删除列表及完整runtime SHA，可还原较早测试的实际运行源码；原始测试记录均未覆盖。RESULTS.json列出逐题源码差异、报告SHA、原始数据路径与哈希。\n\n包内不含模型凭据、运行时私有状态、事件日志或检查点。本文汇总仅做离线核验，没有新增生成、评分或付费重跑。\n')
save(OUT/'MANIFEST.json',dict(created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),profiles={str(k):v for k,v in profile_info.items()},historical_runtime_variants=variants,report_count=len(rows),new_model_calls=0,new_race_calls=0,all_report_and_race_hashes_verified=True,all_generation_token_totals_recomputed_by_unique_native_attempt=True))
print('Saved',OUT,'reports',len(rows),'profiles',len(profile_info),'historical runtime variants',len(variants))
