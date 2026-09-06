"""OMP JSONL and read-only SQLite adapters. Credentials stay in memory."""
import hashlib,json,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from identity import from_token,stable_id

def connect(root=None):
    path=Path(root or (Path.home()/'.omp'/'agent'))/'agent.db'
    return sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)

def identity(root=None):
    try:
        with connect(root) as con:
            row=con.execute("SELECT data FROM auth_credentials WHERE provider='openai-codex' AND credential_type='oauth' AND disabled_cause IS NULL ORDER BY updated_at DESC LIMIT 1").fetchone()
        data=json.loads(row[0]) if row else {}
        raw=data.get('accountId','')
        return from_token(raw,data.get('access'))
    except (sqlite3.Error,ValueError,OSError):return {}

def history(root=None):
    result=[]
    try:
        with connect(root) as con:
            rows=con.execute("SELECT recorded_at,account_id,email,used_fraction,resets_at FROM usage_history WHERE provider='openai-codex' AND window_label='7 days' ORDER BY recorded_at").fetchall()
        for recorded,raw,email,used,reset in rows:
            if not raw or not reset:continue
            result.append({'account':stable_id(raw,email or ''),
                'ts':datetime.fromtimestamp(recorded/1000,timezone.utc).isoformat(),
                'usedPercent':used*100 if used is not None else None,'resetsAt':reset/1000})
    except (sqlite3.Error,ValueError,OSError):pass
    return result

def files(root):return (Path(root)/'sessions').rglob('*.jsonl')

def parse(path):
    events=[]
    with path.open(encoding='utf-8',errors='replace') as handle:
        for line in handle:
            try:row=json.loads(line)
            except ValueError:continue
            m=row.get('message') or {};usage=m.get('usage') or {}
            if m.get('role')!='assistant' or not usage:continue
            numbers=[max(0,int(usage.get(k,0) or 0)) for k in ['input','cacheRead','cacheWrite','output']]
            if not sum(numbers):continue
            ts=row.get('timestamp')
            if not ts and m.get('timestamp'):ts=datetime.fromtimestamp(m['timestamp']/1000,timezone.utc).isoformat()
            if not ts:continue
            # OMP input is already uncached; reasoning is a subset of output.
            events.append({'id':str(m.get('responseId') or row.get('id') or ts),'ts':ts,
                'app':'OMP','account':'unknown','model':m.get('model','unknown'),
                'input':numbers[0],'cached':numbers[1],'write':numbers[2],'output':numbers[3],
                'total':sum(numbers),'provider':m.get('provider','')})
    return events,[]
