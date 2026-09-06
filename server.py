"""Local-only, read-only OAuth usage dashboard. Python standard library only."""
import argparse, hashlib, json, os, threading, time, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import codex_reader, pricing
from identity import from_token

BASE=Path(__file__).resolve().parent
if os.environ.get('CODEX_OMP_DASHBOARD_STATE'):
    STATE=Path(os.environ['CODEX_OMP_DASHBOARD_STATE']).expanduser()
elif os.environ.get('LOCALAPPDATA'):
    STATE=Path(os.environ['LOCALAPPDATA'])/'CodexOmpUsageDashboard'
else:
    STATE=Path.home()/'.local'/'state'/'codex-omp-usage-dashboard'
STATE.mkdir(parents=True,exist_ok=True)
CONFIG=BASE/'config.json'
LOCK=threading.Lock()
CACHE={}

def now(): return datetime.now(timezone.utc).isoformat()
def iso(t): return datetime.fromtimestamp(t,timezone.utc).isoformat()
def read_json(p, default):
    try: return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError,ValueError): return default
def save(p,data):
    payload=json.dumps(data,ensure_ascii=False)
    tmp=Path(f'{p}.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_text(payload,encoding='utf-8')
    try: os.replace(tmp,p)
    except OSError:
        # Some Windows EFS folders reject even same-directory atomic renames.
        tmp.unlink(missing_ok=True)
        Path(p).write_text(payload,encoding='utf-8')
def account_id(raw): return hashlib.sha256(raw.encode()).hexdigest()[:12] if raw else None

def cycle_estimates(cycles, observations, events, pricing_mode='current'):
    """Estimate a cycle's full value from local priced usage / observed usage share."""
    result=[]
    for cycle in cycles:
        start=datetime.fromisoformat(cycle['start'].replace('Z','+00:00'))
        end=datetime.fromisoformat(cycle['end'].replace('Z','+00:00'))
        end_ts=end.timestamp()
        matches=[]
        for observation in observations:
            if observation.get('account')!=cycle['account'] or observation.get('resetsAt') is None:continue
            reset=observation['resetsAt']
            reset_ts=float(reset) if isinstance(reset,(int,float)) else datetime.fromisoformat(reset.replace('Z','+00:00')).timestamp()
            if abs(reset_ts-end_ts)<120 and observation.get('ts'):matches.append(observation)
        latest=max(matches,key=lambda q:datetime.fromisoformat(q['ts'].replace('Z','+00:00')).timestamp(),default=None)
        observed_at=datetime.fromisoformat(latest['ts'].replace('Z','+00:00')) if latest else end
        cutoff=min(end,observed_at)
        rows=[e for e in events if e['account']==cycle['account'] and start<=datetime.fromisoformat(e['ts'].replace('Z','+00:00'))<=cutoff]
        def selected_cost(event):
            if pricing_mode=='current':return event.get('cost')
            return (event.get('costs') or {}).get(pricing_mode)
        known=[selected_cost(e) for e in rows if selected_cost(e) is not None]
        actual=sum(known) if known else None
        unknown=len(rows)-len(known)
        used=latest.get('usedPercent') if latest else None
        estimate=None
        if latest is None:status='no-observation'
        elif used is None or used<=0:status='zero-percent'
        elif not rows or actual is None:status='insufficient-events'
        else:
            estimate=actual/(used/100)
            status='partial' if unknown else 'estimated'
        result.append({'account':cycle['account'],'start':cycle['start'],'end':cycle['end'],
            'observedAt':latest.get('ts') if latest else None,'usedPercent':used,
            'actualCost':actual,'estimatedTotalCost':estimate,'coverageStatus':status,
            'eventCount':len(rows),'unknownPriceEvents':unknown})
    return sorted(result,key=lambda x:x['end'])

def codex_identity():
    data=read_json(Path.home()/'.codex'/'auth.json',{})
    t=data.get('tokens') or {}
    return from_token(t.get('account_id',''),t.get('access_token'))

def get_quota(identity):
    if not identity.get('token') or not identity.get('raw'): return None,'未找到可用的 OAuth 登录信息'
    headers={'Authorization':'Bearer '+identity['token'],'ChatGPT-Account-Id':identity['raw'],
             'User-Agent':'LocalUsageDashboard/1.0','Accept':'application/json'}
    request=urllib.request.Request('https://chatgpt.com/backend-api/wham/usage',headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=15) as response: data=json.load(response)
        limit=data.get('rate_limit') or {}
        for name in ['primary_window','secondary_window']:
            window=limit.get(name) or {}
            if window.get('limit_window_seconds')==604800:
                return {'usedPercent':window.get('used_percent'),'resetsAt':iso(window['reset_at']),
                        'observedAt':now(),'source':'OpenAI OAuth'},None
        return None,'官方响应没有 7 天额度窗口'
    except Exception as exc:
        # Never serialize request headers, tokens or HTTP response bodies.
        return None,'额度查询暂不可用（'+type(exc).__name__+'）'

def parsed_file(path,kind):
    stat=path.stat(); key=str(path); signature=[stat.st_size,stat.st_mtime_ns]
    existing=CACHE.get(key)
    if existing and existing['sig']==signature: return existing['events'],existing['quotas']
    disk=STATE/(hashlib.sha256(key.encode()).hexdigest()+'.json')
    existing=read_json(disk,{})
    if existing.get('sig')==signature:
        CACHE[key]=existing;return existing['events'],existing['quotas']
    events,quotas=codex_reader.parse(path) if kind=='Codex' else omp_reader.parse(path)
    existing={'sig':signature,'events':events,'quotas':quotas};CACHE[key]=existing;save(disk,existing)
    return events,quotas

def collect():
    import omp_reader
    globals()['omp_reader']=omp_reader
    config=read_json(CONFIG,{})
    warnings=[]; accounts=[]; events=[]; quota_history=[]
    omp_root=Path(config.get('ompRoot',str(Path.home()/'.omp'/'agent'))).expanduser()
    identities={'Codex':codex_identity(),'OMP':omp_reader.identity(omp_root)}
    stored=read_json(STATE/'accounts.json',{})
    bindings=read_json(STATE/'bindings.json',{})
    for app in ['Codex','OMP']:
        ident=identities[app]
        if app not in bindings and ident.get('id'):bindings[app]=ident['id']
        aid=bindings.get(app) or app.lower()+'-unavailable'
        label=config.get('labels',{}).get(app,app+' · Team 账户')
        q=stored.get(aid,{}).get('quota'); checked=stored.get(aid,{}).get('checked',0)
        if time.time()-checked>300:
            fresh,error=get_quota(ident) if ident.get('id')==aid else (None,'当前登录身份与首次绑定不同，请核对账户后再迁移绑定，历史归属保持原账户')
            if fresh:
                q=fresh
                hist=read_json(STATE/'cycles.json',[])
                if not any(x['account']==aid and x['end']==q['resetsAt'] for x in hist):
                    end=datetime.fromisoformat(q['resetsAt']).timestamp()
                    hist.append({'account':aid,'start':iso(end-604800),'end':q['resetsAt']})
                    save(STATE/'cycles.json',hist)
            if error: warnings.append(app+'：'+error)
            stored[aid]={'quota':q,'checked':time.time(),'error':error}
        elif stored.get(aid,{}).get('error'): warnings.append(app+'：'+stored[aid]['error'])
        accounts.append({'id':aid,'label':label,'quota':q,'app':app})
    save(STATE/'accounts.json',stored)
    save(STATE/'bindings.json',bindings)
    appids={a['app']:a['id'] for a in accounts}
    sources=[('Codex',codex_reader.files(config.get('codexRoot',str(Path.home()/'.codex')))),
             ('OMP',omp_reader.files(omp_root))]
    seen=set(); excluded=0; failures=0
    for app,paths in sources:
        for path in paths:
            try: rows,qs=parsed_file(path,app)
            except (OSError,ValueError,TypeError): failures+=1;continue
            # A fixed software-to-account mapping was explicitly confirmed by the owner.
            for row in rows:
                if app=='Codex' and row.get('origin')!='Codex Desktop': excluded+=1;continue
                if row.get('provider') not in ('openai','openai-codex','',None): excluded+=1;continue
                if app=='OMP' and row.get('provider')!='openai-codex':excluded+=1;continue
                if row['model'].startswith(('glm-','claude-','deepseek-','gemini-')):excluded+=1;continue
                event=dict(row);event['account']=appids[app]
                # Cloned/forked histories preserve timestamp and cumulative signature.
                key=(app,event['id']) if app=='OMP' else (app,event['ts'],event['model'],event['id'].split(':',1)[-1])
                if key in seen:continue
                seen.add(key);events.append(event)
            if app=='Codex' and rows and rows[0].get('origin')=='Codex Desktop':
                for q in qs: q=dict(q);q['account']=appids[app];quota_history.append(q)
    if failures: warnings.append(f'{failures} 个记录文件暂时无法读取，下次刷新会重试。')
    if excluded: warnings.append(f'已排除 {excluded:,} 条非 Codex Desktop 或非 OpenAI 来源记录。')
    # Historical windows are based on observations, never invented rolling weeks.
    cycles=[c for c in read_json(STATE/'cycles.json',[]) if c['account'] in appids.values()]
    ck={(c['account'],c['end']) for c in cycles}
    quota_history.extend(q for q in omp_reader.history(omp_root) if q['account'] in appids.values())
    for q in quota_history:
        end=iso(q['resetsAt']);key=(q['account'],end)
        if key not in ck:
            cycles.append({'account':q['account'],'start':iso(q['resetsAt']-604800),'end':end});ck.add(key)
        a=next(a for a in accounts if a['id']==q['account'])
        if not a['quota'] or datetime.fromisoformat(q['ts'].replace('Z','+00:00'))>datetime.fromisoformat(a['quota']['observedAt'].replace('Z','+00:00')):
            a['quota']={'usedPercent':q['usedPercent'],'resetsAt':end,'observedAt':q['ts'],'source':'本地配额快照'}
    # OMP's duration-derived reset estimates can differ by a few seconds.
    normalized=[]
    for c in sorted(cycles,key=lambda c:c['end'],reverse=True):
        end=datetime.fromisoformat(c['end']).timestamp()
        if not any(x['account']==c['account'] and abs(datetime.fromisoformat(x['end']).timestamp()-end)<120 for x in normalized):normalized.append(c)
    cycles=normalized
    save(STATE/'cycles.json',cycles)
    prices=read_json(pricing.PATH,{})
    if not prices: prices=pricing.refresh()
    if time.time()-datetime.fromisoformat(prices['updatedAt']).timestamp()>86400:
        try:prices=pricing.refresh()
        except Exception:warnings.append('官方价格刷新失败，正在使用页面标注日期的最近成功快照。')
    for e in events:
        e['cost']=pricing.value(e,prices)
        for k in ['id','origin','provider']:e.pop(k,None)
    events.sort(key=lambda e:e['ts'])
    for a in accounts:
        q=a.get('quota')
        if q and q.get('resetsAt') and q.get('observedAt'):
            quota_history.append({'account':a['id'],'ts':q['observedAt'],'usedPercent':q.get('usedPercent'),
                                  'resetsAt':datetime.fromisoformat(q['resetsAt'].replace('Z','+00:00')).timestamp()})
    estimates=cycle_estimates(cycles,quota_history,events)
    original_prices=pricing.original_prices()
    for e in events:e['costs']={'current':e['cost'],'original':pricing.value(e,original_prices)}
    original_estimates=cycle_estimates(cycles,quota_history,events,'original')
    unknown=sum(e['cost'] is None for e in events)
    if unknown:warnings.append(f'{unknown:,} 条记录的模型或计价类别没有官方可核实价格，价值合计仅包含已定价部分。')
    warnings.append('Astra 原价 cache read 按用户指定固定为 $1/1M tokens。')
    warnings.append('账户按你确认的固定软件对应关系归集；总用量仅涵盖本机保留的记录，不能代表云端、其他设备或已删除历史。')
    warnings.append('Codex 历史日志没有逐次 OAuth 身份字段，按 Desktop / OpenAI 来源归集；7 天窗口来自实际重置快照，提前重置的轮次起点只能按结束时间减 7 天估算。')
    warnings.append('整轮总可用等效价值按“本机已定价用量价值 ÷ 最后观测使用比例”推测；它受本机历史完整度和模型价值结构影响，不是官方额度或承诺价值。')
    return {'updatedAt':now(),'accounts':accounts,'events':events,'cycles':cycles,
            'cycleEstimates':estimates,'cycleEstimatesByPricing':{'current':estimates,'original':original_estimates},
            'prices':prices,'priceSchemes':{'current':prices,'original':original_prices},'warnings':warnings}

SNAPSHOT=None; LAST_SCAN=0
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        global SNAPSHOT,LAST_SCAN
        if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):
            self.send_error(403);return
        path=self.path.split('?')[0]
        if path=='/api/data':
            try:
                with LOCK:
                    if SNAPSHOT is None or time.time()-LAST_SCAN>45:
                        SNAPSHOT=collect();LAST_SCAN=time.time()
                    body=json.dumps(SNAPSHOT,ensure_ascii=False).encode()
                mime='application/json; charset=utf-8'
            except Exception as e:
                self.send_error(503,'Data collection unavailable: '+type(e).__name__);return
        elif path in ('/','/index.html'):
            body=(BASE/'index.html').read_bytes();mime='text/html; charset=utf-8'
        else:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--snapshot',action='store_true');args=parser.parse_args()
    if args.snapshot:
        data=collect();save(STATE/'snapshot.json',data)
        print(json.dumps({'events':len(data['events']),'accounts':len(data['accounts']),
              'models':sorted(set(e['model'] for e in data['events'])),'warnings':data['warnings']},ensure_ascii=False))
    else:
        print('Dashboard: http://127.0.0.1:'+str(args.port),flush=True)
        ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
