#!/usr/bin/env python3
"""Real trained English tokenizer + ROUGE/METEOR regression; no model/API."""
import json,copy,sys
from common import ROOT,validate_prediction
import nltk
nltk.data.path.insert(0,str(ROOT/'resources/nltk_data'))
from metric_core import align
from score import TextMetrics,summarize

def gt(*pairs):return [dict(file_name='fixture.txt',perturbation=[dict(changed_text=a,contradicted_text=b) for a,b in pairs])]
def pred(*texts):return [dict(text=t,explanation='Synthetic regression fixture.') for t in texts]
def main():
    a='Alpha payment is required.';b='Beta delivery is required.';c='Gamma notice is required.';d='Delta approval is required.'
    cases={
        'one_to_many':(gt((a+' '+b,c)),pred(a,b)),
        'two_sides':(gt((a,b)),pred(a,b)),
        'one_to_one':(gt((a,b)),pred(a)),
        'half_missed_extra':(gt((a,b),(c,d)),pred(a,'Omega costs are excluded.')),
        'no_predictions':(gt((a,b)),[]),
        'punctuation_mismatch':(gt((a,b)),pred(a[:-1]+'!')),
        'inner_whitespace_mismatch':(gt((a,b)),pred('Alpha  payment is required.')),
        'boilerplate_bridge':(gt(('Common rule applies. '+a,b),('Common rule applies. '+c,d)),pred('Common rule applies.')),
        'duplicate_predictions':(gt((a,b)),pred(a,a)),
        'duplicate_GT_sides':(gt((a,a)),pred(a)),
        'empty_changed':(gt(('',a)),pred(a)),
    }
    result={name:align(g,p) for name,(g,p) in cases.items()}
    assert result['one_to_many']['groups'][0]['pred_text']==b+' '+a
    assert len(result['two_sides']['groups'])==2
    assert result['half_missed_extra']['miss']==.5 and result['half_missed_extra']['extra']==.5
    assert result['no_predictions']['miss']==1 and result['no_predictions']['extra']==0
    assert result['punctuation_mismatch']['miss']==1 and result['inner_whitespace_mismatch']['miss']==1
    assert result['boilerplate_bridge']['n']==2 and result['boilerplate_bridge']['miss']==0
    assert result['duplicate_predictions']['extra']==0
    assert result['empty_changed']['miss']==0 and result['empty_changed']['n']==1
    calc=TextMetrics(without_bert=True)
    for name,v in result.items():
        for group in v['groups']:group['text_scores']=calc.score(group['gt_text'],group['pred_text'])
    assert result['one_to_many']['groups'][0]['text_scores']['rouge-l']==.5
    assert result['one_to_many']['groups'][0]['text_scores']['rouge-2']==.8571
    assert result['half_missed_extra']['groups'][0]['text_scores']['rouge-l']==1
    assert calc.score('The car is large.','The automobile is big.')['meteor']>0
    assert validate_prediction([])==[]
    for bad in [None,{},[{}],[dict(text='',explanation='x')],[dict(text='x',explanation=1)]]:
        try:validate_prediction(bad)
        except ValueError:pass
        else:raise AssertionError('invalid JSON shape accepted')
    docs=[dict(n=1,m=1,FN=0,FP=0,miss=0.,extra=0.,text_scores={'rouge-1':1.},matched_groups=1),dict(n=3,m=1,FN=3,FP=1,miss=1.,extra=1.,text_scores=None,matched_groups=0)]
    summary=summarize(docs)
    assert summary['miss']==.5 and summary['text_scores']['rouge-1']==1 and summary['text_score_documents']==1
    print(json.dumps(dict(status='PASS',matching_cases=len(cases),real_rouge_and_meteor=True,trained_punkt=True,wordnet=True,bert_weights_executed=False,llm_judge_executed=False),indent=2))
if __name__=='__main__':main()
