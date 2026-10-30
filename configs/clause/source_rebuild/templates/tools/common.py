"""Portable paths and strict prediction schema. No external services."""
import json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPLITS=['train','validation','test']
def records(split='test'):
    if split not in SPLITS:raise ValueError('Unknown split')
    return [json.loads(l) for l in (ROOT/f'splits/{split}.jsonl').read_text(encoding='utf-8').splitlines()]
def dump(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)
def validate_prediction(value):
    if not isinstance(value,list):raise ValueError('Prediction must be a JSON array; use [] for a valid empty answer')
    for i,item in enumerate(value):
        if not isinstance(item,dict) or set(item)!= {'text','explanation'}:raise ValueError(f'Item {i}: expected exactly text/explanation')
        if any(not isinstance(item[k],str) for k in ['text','explanation']):raise ValueError(f'Item {i}: fields must be strings')
        if not item['text'].strip():raise ValueError(f'Item {i}: text must not be blank')
    return value
