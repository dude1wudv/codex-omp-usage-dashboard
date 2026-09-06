"""Read only usage envelopes; message content never leaves this parser."""
import json
from pathlib import Path

KEYS = ['input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens']

def parse(path):
    events, quotas = [], []
    model, session, origin, provider, account = 'unknown', path.stem, '', '', 'unknown'
    previous = [0]*4
    last_total = None
    with path.open('r', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if not any(t in line[:160] for t in ['session_meta','turn_context','event_msg']): continue
            try: row=json.loads(line)
            except (ValueError,TypeError): continue
            p=row.get('payload') or {}; typ=row.get('type'); ts=row.get('timestamp')
            if typ=='session_meta':
                session=p.get('id',p.get('session_id',session)); origin=p.get('originator','')
                provider=p.get('model_provider',''); account=p.get('account_id','unknown')
            elif typ=='turn_context':
                model=p.get('model',model)
                account=p.get('account_id',account)
            elif typ=='event_msg' and p.get('type')=='token_count':
                q=p.get('rate_limits') or {}
                # Limit bucket is account-wide; never add model buckets together.
                if q.get('limit_id') in (None,'codex'):
                    for key in ['primary','secondary']:
                        w=q.get(key) or {}
                        if w.get('window_minutes')==10080 and w.get('resets_at'):
                            quotas.append({'ts':ts,'account':account,'usedPercent':w.get('used_percent'),
                                           'resetsAt':w['resets_at'],'plan':q.get('plan_type')})
                info=p.get('info') or {}; total=info.get('total_token_usage')
                if not total or not ts: continue
                current=[max(0,int(total.get(k,0) or 0)) for k in KEYS]
                signature=tuple(current)
                if signature==last_total: continue
                if any(a<b for a,b in zip(current,previous)):
                    # Compaction/reset: use explicit last request, not the new cumulative total.
                    last=info.get('last_token_usage')
                    if not last: previous=current;last_total=signature;continue
                    delta=[max(0,int(last.get(k,0) or 0)) for k in KEYS]
                else: delta=[a-b for a,b in zip(current,previous)]
                previous=current;last_total=signature
                inp,cached,write,out=delta
                if not inp+out: continue
                # Codex input_tokens includes both cache categories; reasoning is in output.
                events.append({'id':f'{session}:{ts}:{signature}', 'ts':ts,'app':'Codex',
                    'account':account,'model':model,'input':max(0,inp-cached-write),
                    'cached':cached,'write':write,'output':out,'total':inp+out,
                    'origin':origin,'provider':provider})
    return events, quotas

def files(root):
    for folder in ['sessions','archived_sessions']:
        yield from (Path(root)/folder).rglob('*.jsonl')
