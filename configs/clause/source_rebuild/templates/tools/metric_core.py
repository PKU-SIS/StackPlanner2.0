"""GPT-version matching and string assembly, without any Judge call."""
from collections import defaultdict
import nltk

def get_sentence_sets(data):
    for item in data:
        sentences=nltk.sent_tokenize(item['text'])
        item['sentences']={s.strip().lower() for s in sentences if s.strip()}
    return data

def find_match_groups(gt_data,pred_data):
    all_items=gt_data+pred_data
    item_map={item['id']:item for item in all_items}
    adj=defaultdict(list)
    for gt_item in gt_data:
        for pred_item in pred_data:
            if gt_item['sentences'] & pred_item['sentences']:
                adj[gt_item['id']].append(pred_item['id'])
                adj[pred_item['id']].append(gt_item['id'])
    visited=set();groups=[]
    for item_id in item_map:
        if item_id not in visited:
            current_group=[];stack=[item_id];visited.add(item_id)
            while stack:
                node_id=stack.pop();current_group.append(item_map[node_id])
                for neighbor_id in adj[node_id]:
                    if neighbor_id not in visited:
                        visited.add(neighbor_id);stack.append(neighbor_id)
            groups.append(current_group)
    return groups

def align(ground_truth,prediction):
    gt=[];pred=[]
    for i,item in enumerate(ground_truth[0]['perturbation']):
        for suffix,key in [('changed','changed_text'),('contradicted','contradicted_text')]:
            if item.get(key):gt.append(dict(id=f'gt_{i}_{suffix}',original_id=f'gt_{i}',text=item[key]))
    for i,item in enumerate(prediction):pred.append(dict(id=f'pred_{i}',text=item['text']))
    groups=find_match_groups(get_sentence_sets(gt),get_sentence_sets(pred))
    matches=[];hit_gt=set();hit_pred=set()
    for group in groups:
        gs=[x for x in group if x['id'].startswith('gt_')]
        ps=[x for x in group if x['id'].startswith('pred_')]
        if not(gs and ps):continue
        hit_gt.update(x['original_id'] for x in gs);hit_pred.update(x['id'] for x in ps)
        matches.append(dict(gt_ids=sorted({x['original_id'] for x in gs}),gt_node_ids=[x['id'] for x in gs],pred_ids=[x['id'] for x in ps],gt_text=' '.join(x['text'] for x in gs),pred_text=' '.join(x['text'] for x in ps)))
    n=len({x['original_id'] for x in gt});m=len(pred)
    return dict(n=n,m=m,FN=n-len(hit_gt),FP=m-len(hit_pred),miss=round((n-len(hit_gt))/n,4) if n else 0.0,extra=round((m-len(hit_pred))/m,4) if m else 0.0,signed_balance=round((m-n)/(m+n),4) if m+n else 0.0,normalized_gap=round(abs(m-n)/max(m,n),4) if max(m,n) else 0.0,groups=matches)
