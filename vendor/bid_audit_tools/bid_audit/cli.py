import argparse
import json
from .runner import run_rule, read_result
from .registry import NAMES


def main():
    p=argparse.ArgumentParser(description='M01-M15 evidence tools; outputs never make a collusion verdict')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    run=sub.add_parser('run');run.add_argument('--rule',choices=NAMES,required=True)
    run.add_argument('--manifest',required=True);run.add_argument('--output',required=True);run.add_argument('--cache')
    read=sub.add_parser('read');read.add_argument('--run-directory',required=True)
    read.add_argument('--collection',default='findings');read.add_argument('--offset',type=int,default=0);read.add_argument('--limit',type=int,default=5)
    a=p.parse_args()
    try:
        if a.command=='list':result={'rules':[{'rule_id':k,'name':v} for k,v in NAMES.items()]}
        elif a.command=='run':result=run_rule(a.rule,a.manifest,a.output,cache_dir=a.cache)
        else:result=read_result(a.run_directory,a.collection,a.offset,a.limit)
    except Exception as e:result={'status':'error','error':{'type':type(e).__name__,'message':str(e)}}
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return 2 if result.get('status')=='error' else 0

if __name__=='__main__':raise SystemExit(main())
