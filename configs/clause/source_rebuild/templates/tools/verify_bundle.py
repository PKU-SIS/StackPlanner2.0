#!/usr/bin/env python3
"""Verify supplied files, YES-only data, input/GT pairing, and split leakage."""
import hashlib,json,re
from pathlib import Path
from collections import Counter
from common import ROOT,records,dump
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()
def main():
    errors=[];checksums=ROOT/'provenance/package_checksums.jsonl'
    if checksums.exists():
        for line in checksums.read_text().splitlines():
            r=json.loads(line);path=ROOT/r['path']
            if not path.is_file() or sha(path)!=r['sha256']:errors.append('checksum: '+r['path'])
    seen_ids=set();seen_groups={};seen_inputs={};counts={}
    for split in ['train','validation','test']:
        rows=records(split);np=0
        for r in rows:
            rid=r['record_id']
            if rid in seen_ids:errors.append('duplicate record_id '+rid)
            seen_ids.add(rid)
            if r['group_id'] in seen_groups and seen_groups[r['group_id']]!=split:errors.append('cross-split base group '+r['group_id'])
            seen_groups[r['group_id']]=split
            text=(ROOT/r['text_path']).read_text(encoding='utf-8')
            if '<*$p$*>' in text:errors.append('answer marker '+rid)
            if hashlib.sha256(text.encode()).hexdigest()!=r['input_sha256']:errors.append('input hash '+rid)
            norm=hashlib.sha256(re.sub(r'\s+',' ',text).strip().casefold().encode()).hexdigest()
            if norm in seen_inputs and seen_inputs[norm]!=split:errors.append('cross-split exact text '+rid)
            seen_inputs[norm]=split
            gt_path=ROOT/r['ground_truth_path']
            if sha(gt_path)!=r['packaged_annotation_sha256']:errors.append('GT hash '+rid)
            ps=json.loads(gt_path.read_text(encoding='utf-8'))[0]['perturbation'];np+=len(ps)
            if not ps or len(ps)!=r['perturbations']:errors.append('GT count '+rid)
            if any(p.get('contradiction_exists')!='YES' or not p.get('explanation','').strip() for p in ps):errors.append('quality policy '+rid)
        counts[split]=dict(documents=len(rows),perturbations=np,base_groups=len({r['group_id'] for r in rows}))
    expected=json.loads((ROOT/'provenance/filter_split_report.json').read_text())
    for split,v in counts.items():
        if v!={k:expected['splits'][split][k] for k in v}:errors.append('split counts '+split)
    ex=json.loads((ROOT/'prompts/provenance.json').read_text())
    if seen_groups[ex['l2_example_group']]!='train':errors.append('one-shot example leaked')
    report=dict(status='FAIL' if errors else 'PASS',splits=counts,documents=len(seen_ids),base_groups=len(seen_groups),errors=errors,semantic_validation='not performed; released YES metadata only')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if errors:raise SystemExit(1)
if __name__=='__main__':main()
