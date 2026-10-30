"""Local audit: compares upstream matching, then exercises the portable CLI.
All generated predictions are synthetic fixtures, NEVER benchmark results.
"""
import ast,contextlib,copy,hashlib,io,json,os,subprocess,sys,tempfile
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1];PACK=ROOT/'CLAUSE_Eval3_InText_ready'
sys.path[:0]=[str(ROOT/'audit/vendor'),str(PACK/'tools')]
os.environ['NLTK_DATA']=str(PACK/'resources/nltk_data')
import nltk
from metric_core import align,get_sentence_sets,find_match_groups
from score import TextMetrics
from self_test import gt,pred

def main():
    source=ROOT/'upstream/CLAUSE/All_Exp_codes/Text_Alignment_&_Exp_Match.py'
    tree=ast.parse(source.read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['get_sentence_sets','find_match_groups','calculate_text_metrics']]
    class F1:
        def mean(self):return self
        def item(self):return .1234
    seen=[]
    def bert_stub(cands,refs,**kwargs):seen.append([cands,refs,kwargs]);return None,None,F1()
    from rouge_score import rouge_scorer
    from nltk.translate.meteor_score import meteor_score
    ns=dict(nltk=nltk,defaultdict=defaultdict,rouge_scorer=rouge_scorer,meteor_score=meteor_score,bert_score_calc=bert_stub)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
    a='Alpha payment is required.';b='Beta delivery is required.';c='Gamma notice is required.'
    fixtures=[(gt((a+' '+b,c)),pred(a,b)),(gt((a,b)),pred(a,b)),(gt((a,a)),pred(a)),(gt((a,b)),[]),(gt((a,b)),pred(a[:-1]+'!')),(gt((a,b)),pred(a,a)),(gt(('',a)),pred(a))]
    calc=TextMetrics(without_bert=True);pairs=0
    for g,p in fixtures:
        gs=[];ps=[]
        for i,item in enumerate(g[0]['perturbation']):
            for suffix,key in [('changed','changed_text'),('contradicted','contradicted_text')]:
                if item[key]:gs.append(dict(id=f'gt_{i}_{suffix}',original_id=f'gt_{i}',text=item[key]))
        ps=[dict(id=f'pred_{i}',text=item['text']) for i,item in enumerate(p)]
        upstream=ns['find_match_groups'](ns['get_sentence_sets'](copy.deepcopy(gs)),ns['get_sentence_sets'](copy.deepcopy(ps)))
        local=find_match_groups(get_sentence_sets(copy.deepcopy(gs)),get_sentence_sets(copy.deepcopy(ps)))
        assert [[x['id'] for x in z] for z in upstream]==[[x['id'] for x in z] for z in local]
        for group in align(g,p)['groups']:
            expected=ns['calculate_text_metrics'](group['gt_text'],group['pred_text'])
            actual=calc.score(group['gt_text'],group['pred_text'])
            assert all(actual[k]==expected[k] for k in actual);pairs+=1
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'audit/vendor')
    def run(*args,ok=True):
        result=subprocess.run([sys.executable,*map(str,args)],cwd=ROOT,env=env,capture_output=True,text=True)
        if ok:assert result.returncode==0,result.stderr+result.stdout
        else:assert result.returncode!=0
        return result
    rows=[json.loads(l) for l in (PACK/'splits/test.jsonl').read_text().splitlines()]
    with tempfile.TemporaryDirectory(prefix='clause_portable_smoke_') as temp:
        temp=Path(temp);empty=temp/'empty';empty.mkdir()
        for r in rows:(empty/(r['record_id']+'.json')).write_text('[]')
        out=temp/'all_empty.json';run(PACK/'tools/score.py','--predictions',empty,'--output',out,'--without-bert')
        report=json.loads(out.read_text())
        assert report['complete_split'] and report['scored_documents']==500
        assert report['all_documents_macro']['miss']==1 and report['all_documents_macro']['extra']==0
        assert report['all_documents_macro']['no_match_documents']==500 and report['all_documents_macro']['text_scores']=={}
        single=temp/'single';single.mkdir()
        for r in rows[:12]:
            ps=json.loads((PACK/r['ground_truth_path']).read_text())[0]['perturbation']
            prediction=[dict(text=ps[0]['changed_text'] or ps[0]['contradicted_text'],explanation=ps[0]['explanation'])]
            (single/(r['record_id']+'.json')).write_text(json.dumps(prediction))
        out2=temp/'partial.json';run(PACK/'tools/score.py','--predictions',single,'--output',out2,'--without-bert','--allow-partial')
        partial=json.loads(out2.read_text())
        assert partial['scored_documents']==12 and partial['diagnostic_partial'] and not partial['complete_split']
        assert partial['all_documents_macro']['text_score_documents']==12
        strict=temp/'strict.json';run(PACK/'tools/score.py','--predictions',single,'--output',strict,'--without-bert',ok=False);assert not strict.exists()
        bad=temp/'bad';bad.mkdir();(bad/(rows[0]['record_id']+'.json')).write_text('{}')
        badout=temp/'bad.json';run(PACK/'tools/score.py','--predictions',bad,'--output',badout,'--without-bert',ok=False);assert not badout.exists()
        requests=temp/'test_requests.jsonl';run(PACK/'tools/export_requests.py','--output',requests)
        rr=[json.loads(l) for l in requests.read_text().split('\n') if l]
        assert len(rr)==500 and all(set(r)=={'record_id','messages'} for r in rr)
        assert '<*$p$*>' not in requests.read_text() and all(set(r['messages'][0])=={'role','content'} for r in rr)
    result=dict(status='PASS',upstream_DFS_matching_comparison_cases=len(fixtures),real_rouge_meteor_pairs_compared=pairs,full_500_test_empty_prediction_smoke=True,real_data_12_document_lexical_diagnostic=True,missing_invalid_prediction_strict_rejection=True,test_request_export_records=500,trained_punkt_and_wordnet=True,bert_score_weights_executed=False,bert_call_signature_only_stubbed_in_upstream_comparison=True,model_inference_executed=False,llm_judge_executed=False,warning='Synthetic fixtures only; no model benchmark result was produced')
    (PACK/'provenance/local_workflow_verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
