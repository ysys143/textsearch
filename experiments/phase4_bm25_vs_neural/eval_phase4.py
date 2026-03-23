import json, sys, os
sys.path.insert(0, '/Users/jaesolshin/Documents/GitHub/textsearch')
from benchmark.eval import compute_ndcg, compute_recall, compute_mrr

os.chdir('/Users/jaesolshin/Documents/GitHub/textsearch')

with open('data/queries_dev.json') as f:
    queries = json.load(f)

relevant = {str(q['query_id']): [str(r) for r in q['relevant_ids']] for q in queries}

summary = {}
for encoder in ['splade-ko', 'bge-m3-sparse']:
    path = f'results/phase4/phase4_{encoder}.json'
    try:
        with open(path) as f:
            results = json.load(f)
        ranked_map = {str(r['query_id']): [str(x) for x in r['ranked']] for r in results}
        ndcg_scores, recall_scores, mrr_scores = [], [], []
        for qid, ranked_ids in ranked_map.items():
            rel = set(relevant.get(qid, []))
            ndcg_scores.append(compute_ndcg(ranked_ids, rel, k=10))
            recall_scores.append(compute_recall(ranked_ids, rel, k=10))
            mrr_scores.append(compute_mrr(ranked_ids, rel))
        ndcg = sum(ndcg_scores) / len(ndcg_scores)
        recall = sum(recall_scores) / len(recall_scores)
        mrr = sum(mrr_scores) / len(mrr_scores)
        print(f'[phase4-{encoder}] ndcg@10={ndcg:.4f}  recall@10={recall:.4f}  mrr={mrr:.4f}')
        summary[encoder] = {'ndcg_at_10': ndcg, 'recall_at_10': recall, 'mrr': mrr}
    except Exception as e:
        print(f'[phase4-{encoder}] SKIP: {e}')
        summary[encoder] = {'error': str(e)}

os.makedirs('results/phase4', exist_ok=True)
with open('results/phase4/summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
print('Summary written to results/phase4/summary.json')
