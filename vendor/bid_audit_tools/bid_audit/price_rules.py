from collections import defaultdict
from decimal import Decimal, localcontext
from functools import reduce
from itertools import combinations
from math import gcd
import json
import re

import pdfplumber
from .core import digest, dump, norm, outcome
from .tables import clean_page


def money(s):
    s=norm(s).replace('，',',')
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?',s):
        raise ValueError(f'Unrecognized monetary value: {s!r}')
    d=Decimal(s.replace(',',''))
    if d<0:raise ValueError('Negative amount')
    return int(d*100)


def yuan(c):return f'{Decimal(c)/100:.2f}'


def metrics(prices):
    if len(prices)<2 or any(type(p) is not int or p<=0 for p in prices):
        raise ValueError('At least two strictly positive integer-cent prices required')
    prices=sorted(prices)
    with localcontext() as c:
        c.prec=40;n=Decimal(len(prices));mean=sum(map(Decimal,prices))/n
        sse=sum((Decimal(p)-mean)**2 for p in prices)
        pop=(sse/n).sqrt();sample=(sse/(n-1)).sqrt()
        gaps=[b-a for a,b in zip(prices,prices[1:])];base=reduce(gcd,gaps)
        return {'n':len(prices),'mean_yuan':str(mean/100),'population_sd_yuan':str(pop/100),
                'sample_sd_yuan':str(sample/100),'population_cv_percent':str(pop/mean*100),
                'sample_cv_percent':str(sample/mean*100),'range_yuan':yuan(prices[-1]-prices[0]),
                'range_over_mean_percent':str(Decimal(prices[-1]-prices[0])/mean*100),
                'adjacent_gaps_yuan':[yuan(g) for g in gaps],'gap_gcd_yuan':yuan(base),
                'gap_multiples':[g//base for g in gaps] if base else None,
                'strict_positive_arithmetic_progression':len(prices)>=3 and base>0 and len(set(gaps))==1,
                'all_equal':len(set(prices))==1,'all_whole_10000_yuan':all(p%1000000==0 for p in prices)}


def quote_table(ctx,doc):
    key=digest((doc['sha256']+'quotes-v5').encode());cache=ctx.cache/(key+'.quotes.json')
    if cache.exists():return json.loads(cache.read_text())
    raw='\n'.join(p['text'] for p in ctx.pages(doc));compact=norm(raw)
    if doc['role']=='quote_detail' and ('人民币/元' not in compact or '价款形式:总价' not in compact):
        raise ValueError(f'{doc["id"]}: CNY yuan total-price convention not verified')
    projects=set(re.findall(r'项目编号[：:]\s*([.A-Za-z0-9_-]+)',raw))
    bidders=set(re.findall(r'投标人名称[：:]\s*([^\n]+)',raw))
    lots=set(re.findall(r'包号[：:]\s*(\d+)',raw))
    if not lots:lots=set(re.findall(r'第\s*(\d+)\s*采购包',raw))
    if len(projects)!=1 or len(bidders)!=1 or len(lots)!=1:
        raise ValueError(f'{doc["id"]}: ambiguous project/bidder/lot identity')
    result={'source_project_id':next(iter(projects)),'source_bidder':next(iter(bidders)).strip(),
            'source_lot':next(iter(lots)),'currency':'CNY','rows':[]}
    required={'品目号','单价','数量','总价'} if doc['role']=='quote_detail' else set()
    with pdfplumber.open(doc['_path']) as pdf:
        active_headers=None
        for pn,page in enumerate(pdf.pages,1):
            for table in clean_page(page).find_tables():
                data=table.extract()
                if not data:continue
                headers=[norm(v) for v in data[0]]
                body=data[1:]
                if doc['role']=='quote_detail':
                    if required.issubset(headers):active_headers=headers
                    elif (active_headers and len(headers)==len(active_headers) and
                          (not headers[0] or re.fullmatch(r'\d+-\d+',headers[0]))):
                        headers=active_headers;body=data
                    else:continue
                elif not any(x in headers for x in ('投标总报价(元)','投标报价(元/%)','投标报价(元)')):continue
                if len(headers)!=len(set(headers)):raise ValueError('Duplicate quote headers')
                for ri,row in enumerate(body,1):
                    if not any(norm(v) for v in row):continue
                    cells=dict(zip(headers,row))
                    if doc['role']=='quote_detail' and norm(cells.get('品目号')) in ('','合计','总计'):
                        if '合计' in ''.join(norm(v) for v in row):continue
                        if (result['rows'] and not any(norm(cells.get(k)) for k in ('序号','单价','数量','总价'))):
                            previous=result['rows'][-1]
                            for k,v in cells.items():
                                if norm(v):previous['cells'][k]=str(previous['cells'].get(k) or '')+str(v)
                            previous.setdefault('continuations',[]).append({'page':pn,'table_bbox':list(table.bbox),'row':ri})
                            continue
                        raise ValueError('Unidentified/continued detail row; explicit adapter required')
                    result['rows'].append({'cells':cells,'page':pn,'table_bbox':list(table.bbox),'row':ri})
            page.close()
    if not result['rows']:raise ValueError(f'{doc["id"]}: supported quote table not found')
    dump(cache,result);return result


def quotes(ctx):
    paired=defaultdict(dict)
    for d in ctx.documents('quote_detail','quote_opening'):
        if d['_path'].suffix.lower()!='.pdf':raise ValueError('Quote adapter supports digital PDF only')
        scope=(d['round'],d['lot'],d['bidder'])
        if d['role'] in paired[scope]:raise ValueError('Duplicate quote document in same bidder/round/lot')
        paired[scope][d['role']]=d
    out=[]
    for (rnd,lot,bidder),pair in paired.items():
        if set(pair)!= {'quote_detail','quote_opening'}:raise ValueError(f'Missing paired quote: {bidder}')
        d,o=pair['quote_detail'],pair['quote_opening'];dt,ot=quote_table(ctx,d),quote_table(ctx,o)
        if any(dt[k]!=ot[k] for k in ('source_project_id','source_bidder','source_lot')):
            raise ValueError('Opening/detail identity mismatch')
        if norm(dt['source_bidder'])!=norm(bidder) or dt['source_lot']!=lot:raise ValueError('Manifest and quote identity mismatch')
        if len(ot['rows'])!=1:raise ValueError('Opening summary must contain exactly one lot row')
        oc=ot['rows'][0]['cells']
        opening=money(next(v for k,v in oc.items() if k in ('投标总报价(元)','投标报价(元/%)','投标报价(元)')))
        items=[]
        for r in dt['rows']:
            cells=r['cells'];q=re.fullmatch(r'(\d+(?:\.\d+)?)\(([^()]+)\)',norm(cells['数量']))
            if not q or Decimal(q[1])<=0:raise ValueError('Invalid quantity/unit')
            unit=money(cells['单价']);total=money(cells['总价'])
            if Decimal(unit)*Decimal(q[1])!=total:raise ValueError('unit_price * quantity != line_total')
            item_id=norm(cells['品目号'])+'/'+norm(cells.get('序号'))
            unit_needs_review=len(q[2])>8 or '公司' in q[2]
            if unit_needs_review:ctx.warnings.append(f'{d["id"]} item {item_id}: suspicious quantity unit; excluded from list-price alignment')
            items.append({'item_id':item_id,'source_item_id':norm(cells['品目号']),'source_sequence':norm(cells.get('序号')),'product':norm(cells.get('货物名称')),
                          'model':norm(cells.get('规格型号')),'brand':norm(cells.get('品牌')),
                          'manufacturer':norm(cells.get('制造商名称')),'quantity':q[1],'unit':q[2],
                          'unit_price_cents':unit,'total_cents':total,'unit_needs_review':unit_needs_review,
                          'evidence':ctx.evidence(d,r['page'],table_bbox=r['table_bbox'],table_row=r['row'],cells=cells,continuations=r.get('continuations',[]))})
        if len({i['item_id'] for i in items})!=len(items):raise ValueError('Duplicate item ids')
        if sum(i['total_cents'] for i in items)!=opening:raise ValueError(f'{bidder}: detail sum != opening total')
        if opening<=0:raise ValueError('Nonpositive overall quote')
        out.append({'bidder':bidder,'round':rnd,'lot':lot,'source_project_id':dt['source_project_id'],'currency':'CNY',
                    'total_cents':opening,'total_yuan':yuan(opening),'items':items,
                    'opening_evidence':ctx.evidence(o,ot['rows'][0]['page'],table_bbox=ot['rows'][0]['table_bbox']),
                    'validation':{'identities_match':True,'line_arithmetic_matches':True,'opening_total_matches':True}})
    return out


def m13(ctx):
    rows=quotes(ctx);groups=defaultdict(list);results=[]
    for r in rows:groups[(r['round'],r['lot'],r['currency'],r['source_project_id'])].append(r)
    for scope,rr in groups.items():
        if len(rr)<2:continue
        rr=sorted(rr,key=lambda r:r['total_cents'])
        results.append({'kind':'descriptive_price_statistics','round':scope[0],'lot':scope[1],
                        'statistics':metrics([r['total_cents'] for r in rr]),
                        'sorted_quotes':[{'bidder':r['bidder'],'total_yuan':r['total_yuan']} for r in rr],
                        'pairwise_gaps':[{'bidder_a':a['bidder'],'bidder_b':b['bidder'],
                                         'gap_yuan':yuan(abs(a['total_cents']-b['total_cents']))} for a,b in combinations(rr,2)],
                        'evidence':[r['opening_evidence'] for r in rr]})
    return outcome({'validated_bidder_quotes':len(rows)},results,records=rows,
                   status='ok' if results else 'insufficient_data',
                   limitations=['Statistics describe all supplied bidders in the same round/lot; no concealed outlier or subgroup removal.',
                                'Product configuration, public prices, caps and budgets require separate verified context.',
                                'Every integer gap set has a GCD. GCD, round-number endings and low CV are not collusion verdicts.'])


def m14(ctx):
    rows=quotes(ctx);index=defaultdict(list);findings=[];incomparable=[]
    for r in rows:index[(r['bidder'],r['lot'],r['currency'])].append(r)
    for _,rr in index.items():
        for a,b in combinations(sorted(rr,key=lambda r:r['round']),2):
            if a['round']==b['round']:continue
            sig=lambda r:sorted((i['item_id'],i['product'],i['model'],i['quantity'],i['unit']) for i in r['items'])
            if sig(a)!=sig(b):
                incomparable.append({'bidder':a['bidder'],'rounds':[a['round'],b['round']],'reason':'Product/model/quantity/unit changed'});continue
            rank=lambda x:1+sum(y['total_cents']<x['total_cents'] for y in rows if (y['round'],y['lot'])==(x['round'],x['lot']))
            delta=b['total_cents']-a['total_cents']
            findings.append({'kind':'cross_round_quote_comparison','bidder':a['bidder'],'lot':a['lot'],
                             'round_a':a['round'],'round_b':b['round'],'quote_a_yuan':a['total_yuan'],'quote_b_yuan':b['total_yuan'],
                             'delta_yuan':yuan(delta),'unchanged':delta==0,'rank_a':rank(a),'rank_b':rank(b),
                             'evidence':[a['opening_evidence'],b['opening_evidence']]})
    return outcome({'comparisons':len(findings),'unchanged_comparisons':sum(x['unchanged'] for x in findings)},findings,
                   incomparable=incomparable,records=rows,status='ok' if findings else 'insufficient_data',
                   limitations=['Manifest project_id is the caller-confirmed cross-round project identity; source procurement IDs may change.',
                                'Unchanged prices alone are not suspicious without changed competition/cost conditions. No fixed-price exclusion is inferred.'])


def m15(ctx):
    rows=quotes(ctx);findings=[];na=[]
    for a,b in combinations(rows,2):
        if (a['round'],a['lot'],a['source_project_id'])!=(b['round'],b['lot'],b['source_project_id']):continue
        key=lambda i:(i.get('source_item_id',i['item_id']),i['product'],i['unit'])
        def unique_items(row):
            index=defaultdict(list)
            for item in row['items']:
                if item['unit_price_cents']>0 and not item.get('unit_needs_review'):index[key(item)].append(item)
            duplicate=[list(k) for k,v in index.items() if len(v)>1]
            return {k:v[0] for k,v in index.items() if len(v)==1},duplicate
        aa,amb_a=unique_items(a);bb,amb_b=unique_items(b)
        common=sorted(aa.keys()&bb.keys())
        if len(common)<2:
            eligibility='not_applicable' if min(len(a['items']),len(b['items']))<2 else 'insufficient_data'
            na.append({'bidder_a':a['bidder'],'bidder_b':b['bidder'],'round':a['round'],'lot':a['lot'],
                       'comparable_items':len(common),'eligibility_status':eligibility,
                       'reason':'Fewer than two priced aligned items; inspect records for excluded units or ambiguous keys'});continue
        matches=[k for k in common if aa[k]['unit_price_cents']==bb[k]['unit_price_cents']]
        findings.append({'kind':'aligned_line_price_comparison','bidder_a':a['bidder'],'bidder_b':b['bidder'],
                         'round':a['round'],'lot':a['lot'],'n':len(common),'m':len(matches),
                         'equal_fraction':len(matches)/len(common),'all_aligned_prices_equal':len(matches)==len(common),
                         'ambiguous_repeated_item_keys_a':amb_a,'ambiguous_repeated_item_keys_b':amb_b,
                         'unmatched_items_a':[list(k) for k in sorted(aa.keys()-bb.keys())],
                         'unmatched_items_b':[list(k) for k in sorted(bb.keys()-aa.keys())],
                         'lines':[{'key':list(k),'unit_price_a_yuan':yuan(aa[k]['unit_price_cents']),
                                   'unit_price_b_yuan':yuan(bb[k]['unit_price_cents']),
                                   'equal':k in matches,'model_a':aa[k]['model'],'model_b':bb[k]['model'],
                                   'evidence':[aa[k]['evidence'],bb[k]['evidence']]} for k in common],
                         'evidence':[aa[k]['evidence'] for k in common]+[bb[k]['evidence'] for k in common]})
    return outcome({'eligible_pairs':len(findings),'inapplicable_pairs':len(na)},findings,not_applicable_pairs=na,records=rows,
                   status='ok' if findings else 'not_applicable' if na and all(x['eligibility_status']=='not_applicable' for x in na) else 'insufficient_data',
                   limitations=['Alignment uses procurement item code, name and unit, independent of row order. Repeated ambiguous keys and suspicious units are excluded; differing models/specifications require review.',
                                'Fixed/regulated prices and standard template quantities need model exclusion; zero prices are omitted.',
                                'All aligned prices equal describes only the compared item intersection, not an entire list with unmatched items.'])
