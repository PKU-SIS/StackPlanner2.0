#!/usr/bin/env python3
"""Optional OpenAI-compatible endpoint runner, including local vLLM servers.

This calls the MODEL UNDER TEST, not a Judge. Never invoked automatically.
"""
import argparse,json,os,time,urllib.request,urllib.parse,hashlib
from pathlib import Path
from common import dump,validate_prediction
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--requests',type=Path,required=True);p.add_argument('--base-url',required=True,help='e.g. http://127.0.0.1:8000/v1');p.add_argument('--model',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--temperature',type=float,default=.2);p.add_argument('--top-p',type=float,default=1.0);p.add_argument('--max-tokens',type=int,default=8192);p.add_argument('--timeout',type=float,default=180);p.add_argument('--retries',type=int,default=2)
    args=p.parse_args();url=args.base_url.rstrip('/')+'/chat/completions'
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme not in ['http','https'] or parsed.username or parsed.query:raise SystemExit('Use an http(s) base URL without embedded credentials or query parameters')
    key=os.environ.get('MODEL_API_KEY','')
    config=dict(model=args.model,base_url=args.base_url,temperature=args.temperature,top_p=args.top_p,max_tokens=args.max_tokens,requests_sha256=hashlib.sha256(args.requests.read_bytes()).hexdigest())
    config_path=args.output/'run_config.json'
    if config_path.exists() and json.loads(config_path.read_text())!=config:raise SystemExit('Output directory already belongs to different requests/model/settings')
    dump(config_path,config);failures=[];ok=0;skipped=0
    for line in args.requests.open(encoding='utf-8'):
        if not line.strip():continue
        item=json.loads(line);rid=item['record_id']
        if not rid or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in rid):raise SystemExit('Unsafe record_id')
        pred_path=args.output/'predictions'/(rid+'.json')
        if pred_path.exists():validate_prediction(json.loads(pred_path.read_text()));skipped+=1;continue
        payload=dict(model=args.model,messages=item['messages'],temperature=args.temperature,top_p=args.top_p,max_tokens=args.max_tokens)
        for attempt in range(args.retries+1):
            try:
                headers={'Content-Type':'application/json'}
                if key:headers['Authorization']='Bearer '+key
                request=urllib.request.Request(url,data=json.dumps(payload).encode('utf-8'),headers=headers)
                with urllib.request.urlopen(request,timeout=args.timeout) as response:raw=json.load(response)
                dump(args.output/'raw_responses'/(rid+'.json'),raw)
                choice=raw['choices'][0]
                if choice.get('finish_reason')!='stop':raise ValueError('Unexpected finish_reason: '+str(choice.get('finish_reason')))
                pred=validate_prediction(json.loads(choice['message']['content']))
                dump(pred_path,pred);ok+=1;break
            except Exception as e:
                if attempt==args.retries:failures.append(dict(record_id=rid,error=type(e).__name__+': '+str(e)))
                else:time.sleep(min(2**attempt,8))
        print(f'new={ok} resumed={skipped} failed={len(failures)}',flush=True)
    dump(args.output/'inference_status.json',dict(successful_new=ok,resumed=skipped,failures=failures))
    if failures:raise SystemExit('Some inputs failed; fix them before scoring. Errors were not converted to [].')
if __name__=='__main__':main()
