#!/usr/bin/env python3
"""Default: test split, Miss/Extra + five local metrics; NO LLM Judge.

Missing/invalid predictions fail the run before summary creation unless the user
explicitly requests a diagnostic partial evaluation. No hidden downloads.
"""
import argparse,json,statistics,sys,importlib.metadata,os,hashlib
from pathlib import Path
from collections import Counter,defaultdict
from common import ROOT,records,dump,validate_prediction
os.environ.setdefault('HF_HUB_OFFLINE','1');os.environ.setdefault('TRANSFORMERS_OFFLINE','1')
import nltk
nltk.data.path.insert(0,str(ROOT/'resources/nltk_data'))
from nltk.translate.meteor_score import meteor_score
from rouge_score import rouge_scorer
from metric_core import align

class TextMetrics:
    def __init__(self,without_bert=False,model_dir=None,device=None,batch_size=8):
        self.rouge=rouge_scorer.RougeScorer(['rouge1','rouge2','rougeL'],use_stemmer=True)
        self.bert=None;self.truncated_groups=0
        if not without_bert:
            model_dir=Path(model_dir or ROOT/'resources/models/roberta-large').resolve()
            if not (model_dir/'config.json').is_file():raise FileNotFoundError(f'BERTScore model missing: {model_dir}; run tools/prepare_resources.py --download-bert, or use --without-bert for explicitly partial metrics')
            from bert_score import BERTScorer
            self.bert=BERTScorer(model_type=str(model_dir),num_layers=17,lang='en',idf=False,rescale_with_baseline=False,device=device,batch_size=batch_size,use_fast_tokenizer=False)
    def score(self,gt,pred):
        rs=self.rouge.score(gt,pred)
        result={k:round(rs[src].fmeasure,4) for k,src in [('rouge-1','rouge1'),('rouge-2','rouge2'),('rouge-l','rougeL')]}
        result['meteor']=round(meteor_score([nltk.word_tokenize(gt)],nltk.word_tokenize(pred)),4)
        if self.bert is not None:
            tok=self.bert._tokenizer
            # BERTScore v0.3.13 truncates at tokenizer.model_max_length. Count and disclose it.
            lengths=[len(tok.encode(t.strip(),add_special_tokens=True,add_prefix_space=True,truncation=False,verbose=False)) for t in [gt,pred]]
            trunc=any(l>tok.model_max_length for l in lengths)
            self.truncated_groups+=int(trunc)
            _,_,f1=self.bert.score([pred],[gt],batch_size=self.bert.batch_size)
            result['bert_score_f1']=round(f1.mean().item(),4)
            result['_bert_truncated']=trunc
        return result

def average(rows):
    keys=['rouge-1','rouge-2','rouge-l','meteor','bert_score_f1']
    out={k:round(statistics.mean(r[k] for r in rows if k in r),4) for k in keys if any(k in r for r in rows)}
    return out

def summarize(docs):
    matched=[d for d in docs if d['text_scores'] is not None]
    return dict(documents=len(docs),ground_truth=sum(d['n'] for d in docs),predictions=sum(d['m'] for d in docs),FN=sum(d['FN'] for d in docs),FP=sum(d['FP'] for d in docs),miss=round(statistics.mean(d['miss'] for d in docs),4) if docs else None,extra=round(statistics.mean(d['extra'] for d in docs),4) if docs else None,text_scores=average([d['text_scores'] for d in matched]),text_score_documents=len(matched),no_match_documents=len(docs)-len(matched),aligned_groups=sum(d['matched_groups'] for d in docs))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--split',choices=['train','validation','test'],default='test')
    p.add_argument('--predictions',type=Path,required=True,help='One <record_id>.json per expected document')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--without-bert',action='store_true',help='Explicitly omit BERTScore; never fake it as zero')
    p.add_argument('--bert-model-dir',type=Path);p.add_argument('--device');p.add_argument('--bert-batch-size',type=int,default=8)
    p.add_argument('--allow-partial',action='store_true',help='Diagnostic subset only; not an official/full-split score')
    args=p.parse_args();rows=records(args.split)
    if args.output.exists():raise SystemExit('Output already exists; choose a new result path to avoid mixing runs')
    loaded=[];failures=[]
    for r in rows:
        path=args.predictions/(r['record_id']+'.json')
        try:
            pred=validate_prediction(json.loads(path.read_text(encoding='utf-8')))
            loaded.append((r,pred))
        except (OSError,ValueError,TypeError) as e:failures.append(dict(record_id=r['record_id'],error=type(e).__name__+': '+str(e)))
    if failures and not args.allow_partial:
        dump(args.output.with_name(args.output.name+'.preflight_errors.json'),dict(expected=len(rows),valid=len(loaded),failures=failures))
        raise SystemExit(f'{len(failures)} missing/invalid predictions; no score generated. See preflight_errors. Do not replace failures with [].')
    if not loaded:raise SystemExit('No valid predictions')
    # Require real pretrained Punkt and WordNet even for apparently trivial fixtures.
    nltk.sent_tokenize('A sentence. Another sentence.');nltk.corpus.wordnet.synsets('contract')
    calc=TextMetrics(args.without_bert,args.bert_model_dir,args.device,args.bert_batch_size)
    docs=[];details=args.output.with_name(args.output.stem+'_per_document.jsonl');details.parent.mkdir(parents=True,exist_ok=True)
    with details.open('w',encoding='utf-8') as out:
        for index,(r,pred) in enumerate(loaded,1):
            gt=json.loads((ROOT/r['ground_truth_path']).read_text(encoding='utf-8'));a=align(gt,pred)
            for group in a['groups']:
                raw=calc.score(group['gt_text'],group['pred_text']);group['text_scores']={k:v for k,v in raw.items() if not k.startswith('_')}
                group['bert_truncated']=raw.get('_bert_truncated',False)
            # Upstream rounds group values, then each document's macro mean.
            text=average([g['text_scores'] for g in a['groups']]) if a['groups'] else None
            doc={k:v for k,v in a.items() if k!='groups'}|dict(record_id=r['record_id'],source=r['source'],category=r['category'],matched_groups=len(a['groups']),text_scores=text)
            docs.append(doc)
            out.write(json.dumps(doc|dict(alignments=a['groups']),ensure_ascii=False)+'\n');out.flush()
            if index%25==0 or index==len(loaded):print(f'Scored {index}/{len(loaded)}',file=sys.stderr)
    by={}
    for src in ['CUAD','NLI']:
        by[src]={cat:summarize([d for d in docs if d['source']==src and d['category']==cat]) for cat in ['ambiguity','inconsistency','terminology','omission','structural']}
    source_means={}
    for src,cs in by.items():
        active=[v for v in cs.values() if v['documents']]
        source_means[src]={k:round(statistics.mean(v[k] for v in active),4) for k in ['miss','extra']} if active else {}
        source_means[src]['text_scores']=average([v['text_scores'] for v in active])
        source_means[src]['categories_with_documents']=len(active)
        source_means[src]['text_metric_category_denominators']={k:sum(k in v['text_scores'] for v in active) for k in source_means[src]['text_scores']}
    versions={}
    for name in ['nltk','rouge-score','numpy','bert-score','torch','transformers','huggingface-hub']:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    revision_file=Path(args.bert_model_dir or ROOT/'resources/models/roberta-large')/'MODEL_REVISION.json'
    revision=json.loads(revision_file.read_text()) if revision_file.exists() else None
    report=dict(protocol='intext-yes-whole-document-v1 / GPT-DFS alignment / no Judge',split=args.split,split_manifest_sha256=hashlib.sha256((ROOT/f'splits/{args.split}.jsonl').read_bytes()).hexdigest(),complete_split=not failures,diagnostic_partial=bool(failures),expected_documents=len(rows),scored_documents=len(docs),missing_or_invalid_predictions=failures,metrics_enabled=['miss','extra','rouge-1','rouge-2','rouge-l','meteor']+([] if args.without_bert else ['bert_score_f1']),llm_judge_enabled=False,bert=dict(enabled=not args.without_bert,model='roberta-large',layers=17,rescale=False,idf=False,truncated_groups=calc.truncated_groups,weight_manifest=revision),all_documents_macro=summarize(docs),by_source_category=by,source_five_category_equal_macro=source_means,versions=versions,per_document_file=str(details))
    dump(args.output,report);print(json.dumps(report['all_documents_macro'],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
