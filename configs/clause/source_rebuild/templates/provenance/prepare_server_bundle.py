"""Prepare a non-destructive, grouped 80/10/10 release from the pinned snapshot."""
import hashlib,json,re,unicodedata,shutil,math
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path(__file__).resolve().parents[1]
UP=ROOT/'upstream/CLAUSE';OUT=ROOT/'CLAUSE_Eval3_InText_ready'
SEED='20261030';CATS=['ambiguity','inconsistency','terminology','omission','structural']
COMMIT='c81c6a5251771cab3d9f3d081f71c388957cf936'
def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()
def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def jsonl(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
def canonical_name(s):
    s=unicodedata.normalize('NFKC',s).casefold().strip()
    while s.endswith(('.snippet','.txt')):s=s.rsplit('.',1)[0]
    return re.sub(r'\s+',' ',s)

def main():
    records=[json.loads(l) for l in (ROOT/'audit/dataset_records.jsonl').read_text().splitlines()]
    records=[m for m in records if m['kind']=='intext']
    accepted=[];excluded=[];all_labels=Counter();removed_labels=Counter()
    for m in records:
        gt=json.loads((UP/m['annotation']).read_text(encoding='utf-8'))
        ps=gt[0]['perturbation'];reasons=[]
        all_labels.update(str(p.get('contradiction_exists')) for p in ps)
        if not ps:reasons.append('empty_annotation')
        if any(p.get('contradiction_exists')!='YES' for p in ps):reasons.append('contains_non_YES_validation')
        if any(not isinstance(p.get('explanation'),str) or not p.get('explanation','').strip() for p in ps):reasons.append('missing_or_empty_explanation')
        if any(not isinstance(p.get(k),str) for p in ps for k in ['original_text','changed_text','contradicted_text']):reasons.append('invalid_span_schema')
        if any(not(p.get('changed_text','').strip() or p.get('contradicted_text','').strip()) for p in ps):reasons.append('no_matchable_GT_side')
        if not m.get('paired_text') or not (UP/m['paired_text']).is_file():reasons.append('missing_input')
        if reasons:
            excluded.append(dict(annotation=m['annotation'],source=m['source'],category=m['category'],perturbations=len(ps),reasons=reasons,validation_counts=dict(Counter(str(p.get('contradiction_exists')) for p in ps))))
            removed_labels.update(str(p.get('contradiction_exists')) for p in ps);continue
        source='CUAD' if m['source']=='CUAD_Dataset' else 'NLI'
        # No annotations are removed in isolation: the entire changed document is kept or dropped.
        clean=(UP/m['paired_text']).read_text(encoding='utf-8').replace('<*$p$*>','')
        rid=source.lower()+'_'+m['category']+'_'+digest(m['annotation'])[:16]
        group=source+':'+canonical_name(m['file_name'])
        accepted.append(dict(record_id=rid,source=source,category=m['category'],group_id=group,file_name=m['file_name'],perturbations=len(ps),text_path=f'data/documents/{rid}.txt',ground_truth_path=f'data/annotations/{rid}.json',input_sha256=digest(clean),annotation_sha256=hashlib.sha256((UP/m['annotation']).read_bytes()).hexdigest(),original_annotation=m['annotation'],original_input=m['paired_text'],_text=clean,_gt=gt))
    assert len(accepted)==5026 and sum(x['perturbations'] for x in accepted)==18192
    assert len({x['record_id'] for x in accepted})==len(accepted)
    # Explicit exact-duplicate guard, across source names as well as categories.
    parents={r['group_id']:r['group_id'] for r in accepted}
    def root(x):
        while parents[x]!=x:parents[x]=parents[parents[x]];x=parents[x]
        return x
    text_groups={};merges=[]
    for r in accepted:
        h=digest(re.sub(r'\s+',' ',r['_text']).strip().casefold());g=r['group_id']
        if h in text_groups and root(g)!=root(text_groups[h]):
            a,b=sorted([root(g),root(text_groups[h])]);parents[b]=a;merges.append([a,b])
        text_groups[h]=g
    for r in accepted:r['group_id']=root(r['group_id'])
    by_source=defaultdict(set)
    for r in accepted:by_source[r['source']].add(r['group_id'])
    assert not(by_source['CUAD'] & by_source['NLI']), 'Cross-source duplicate: use joint stratification before proceeding'
    assignment={};source_group_counts={}
    # Keep the paper's one-shot base contract in train to avoid example/test overlap.
    example=next(r for r in accepted if r['file_name'].startswith('2ThemartComInc') and r['category']=='ambiguity')
    for source,gs in sorted(by_source.items()):
        ordered=sorted(gs,key=lambda g:(digest(SEED+'|'+g),g))
        raw=[len(ordered)*.8,len(ordered)*.1,len(ordered)*.1];sizes=[math.floor(x) for x in raw]
        for i in sorted(range(3),key=lambda i:(-(raw[i]-sizes[i]),i))[:len(ordered)-sum(sizes)]:sizes[i]+=1
        if source=='CUAD':
            ex=example['group_id'];ordered.remove(ex);ordered.insert(0,ex)
        start=0;source_group_counts[source]={}
        for split,size in zip(['train','validation','test'],sizes):
            for g in ordered[start:start+size]:assignment[g]=split
            source_group_counts[source][split]=size;start+=size
    splits={s:[] for s in ['train','validation','test']}
    for r in accepted:
        r['split']=assignment[r['group_id']]
        text=r.pop('_text');gt=r.pop('_gt')
        path=OUT/r['text_path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
        dump(OUT/r['ground_truth_path'],gt)
        r['packaged_annotation_sha256']=hashlib.sha256((OUT/r['ground_truth_path']).read_bytes()).hexdigest()
        splits[r['split']].append(r)
    for split,rows in splits.items():jsonl(OUT/f'splits/{split}.jsonl',sorted(rows,key=lambda r:r['record_id']))
    jsonl(OUT/'provenance/excluded_documents.jsonl',excluded)
    jsonl(OUT/'provenance/group_assignments.jsonl',[dict(group_id=g,split=s) for g,s in sorted(assignment.items())])
    split_stats={}
    for split,rows in splits.items():
        split_stats[split]=dict(documents=len(rows),perturbations=sum(r['perturbations'] for r in rows),base_groups=len({r['group_id'] for r in rows}),by_source={source:dict(documents=sum(r['source']==source for r in rows),perturbations=sum(r['perturbations'] for r in rows if r['source']==source),base_groups=len({r['group_id'] for r in rows if r['source']==source})) for source in ['CUAD','NLI']},by_source_category={source:{cat:dict(documents=sum(r['source']==source and r['category']==cat for r in rows),perturbations=sum(r['perturbations'] for r in rows if r['source']==source and r['category']==cat)) for cat in CATS} for source in ['CUAD','NLI']})
    assert len(assignment)==1101
    reason_counts=Counter(reason for e in excluded for reason in e['reasons'])
    report=dict(version='intext-yes-whole-document-v1',created='2026-10-30',upstream_commit=COMMIT,scope='Eval 3 In-Text only',quality_policy='keep entire nonempty document only when every perturbation has exact YES and required span/explanation schema; never selectively delete individual GT from unchanged input',input_annotation_documents=len(records),input_perturbations=sum(m['perturbations'] for m in records),kept_documents=len(accepted),kept_perturbations=sum(r['perturbations'] for r in accepted),excluded_documents=len(excluded),excluded_reason_counts=dict(reason_counts),input_validation_counts=dict(all_labels),excluded_perturbation_validation_counts=dict(removed_labels),orphan_TXT_not_in_candidate_annotations=6,group_key='source + NFKC casefolded filename with .snippet/.txt removed and whitespace collapsed; exact normalized input duplicates unioned',exact_duplicate_group_merges=merges,seed=SEED,split_ratios=dict(train=.8,validation=.1,test=.1),split_algorithm='hash-ranked source-stratified base-contract groups with largest-remainder group counts; published one-shot source group forced into train',source_group_counts=source_group_counts,one_shot_train_record_id=example['record_id'],one_shot_train_group=example['group_id'],splits=split_stats,limitations=['YES is released model-validation metadata, not independent human certification','no exhaustive semantic reannotation or near-duplicate/template clustering','custom filtering and split; paper scores are not scores on this test split'])
    dump(OUT/'provenance/filter_split_report.json',report)
    (OUT/'prompts').mkdir(exist_ok=True)
    prompt='''You are a careful document consistency reviewer. Read the entire contract and identify all in-text discrepancies: statements, clauses, terms, definitions or cross-references that conflict with another part of the SAME document. Examine these five categories: ambiguity (conflicting interpretations within the document), inconsistency (mutually incompatible statements or obligations), misaligned terminology (inconsistent internal definitions or usage), omission (missing content required by another part of the document), and structural flaws (broken hierarchy or cross-references).
Use only the supplied document as evidence. Do not report external-law compliance issues or cite statutes or cases. A change from a hypothetical earlier version is not by itself an internal contradiction.
Return exactly one JSON array, without Markdown or commentary. Each object must have exactly two string fields: "text" (verbatim complete sentence(s) or passage from the input containing the issue) and "explanation" (identify the other relevant part of the same document and explain the incompatibility). Report all issues; do not impose a fixed count. If no issue is found, return []. Use English for explanations. Do not output category labels, law citations or extra keys.
'''
    (OUT/'prompts/l1_system.txt').write_text(prompt)
    example_gt=json.loads((OUT/example['ground_truth_path']).read_text())[0]['perturbation'][0]
    example_output=[dict(text=example_gt['changed_text'],explanation=example_gt['explanation'])]
    l2=prompt+'\nOne training example (partial context and one expected output item, not a test answer):\n'+json.dumps(dict(document_excerpt=[example_gt['changed_text'],example_gt['contradicted_text']],expected_output=example_output),ensure_ascii=False,indent=2)+'\nNow review the new document supplied by the user.\n'
    (OUT/'prompts/l2_system.txt').write_text(l2)
    dump(OUT/'prompts/provenance.json',dict(default='l1',status='adapted internal-only protocol; not byte-identical to the paper prompt',l2_example_record_id=example['record_id'],l2_example_group=example['group_id'],l2_example_split='train',prompt_sha256={lev:digest((OUT/f'prompts/{lev}_system.txt').read_text()) for lev in ['l1','l2']}))
    for name in ['paper_prompts_intext.tex','released_L1_system_prompt.txt','released_L2_system_prompt.txt','paper_results_intext.json','paper_table_verification.json']:
        (OUT/'provenance').mkdir(exist_ok=True);shutil.copy2(ROOT/'eval3_intext'/name,OUT/'provenance'/name)
    for name in ['Text_Alignment_&_Exp_Match.py','miss_extra.py','miss_extra_signed_normalized_avg.py','text_alignment_metric_avg_results.py']:
        target=OUT/'provenance/upstream_code'/name;target.parent.mkdir(exist_ok=True);shutil.copy2(UP/'All_Exp_codes'/name,target)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
