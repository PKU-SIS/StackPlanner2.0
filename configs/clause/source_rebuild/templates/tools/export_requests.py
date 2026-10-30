#!/usr/bin/env python3
"""Export messages only. No GT, category hints or model API calls."""
import argparse,json
from pathlib import Path
from common import ROOT,records
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--split',choices=['train','validation','test'],default='test');p.add_argument('--level',choices=['l1','l2'],default='l1');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    prompt=(ROOT/f'prompts/{args.level}_system.txt').read_text(encoding='utf-8')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('w',encoding='utf-8') as out:
        for r in records(args.split):
            document=(ROOT/r['text_path']).read_text(encoding='utf-8')
            item=dict(record_id=r['record_id'],messages=[dict(role='system',content=prompt),dict(role='user',content='DOCUMENT START\n'+document+'\nDOCUMENT END')])
            # Escape Unicode line separators too, so each JSON object is one physical line.
            out.write(json.dumps(item,ensure_ascii=True)+'\n')
    print(args.output)
if __name__=='__main__':main()
