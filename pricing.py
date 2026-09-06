"""Official pricing snapshot, refreshed from the documented Markdown table."""
import json, re, urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE = 'https://developers.openai.com/api/docs/pricing'
PATH = Path(__file__).with_name('prices.json')

ORIGINAL_RATES = {
    # Internal order: uncached input, cache read, cache write, output.
    'gpt-5.6-luna': [1.0, 0.1, 1.25, 6.0],
    'gpt-5.6-terra': [2.5, 0.25, 3.125, 12.5],
    'gpt-5.6-sol': [5.0, 0.25, 6.25, 30.0],
}

def infer_equal_cap_cached_rate(samples):
    """Fit A + rate*B = common weekly cap using least squares."""
    usable=[s for s in samples if s['cachedCap']>0]
    if len(usable)<2:return None
    mean_a=sum(s['baseCap'] for s in usable)/len(usable)
    mean_b=sum(s['cachedCap'] for s in usable)/len(usable)
    variance=sum((s['cachedCap']-mean_b)**2 for s in usable)
    if variance<=1e-12:return None
    rate=-sum((s['cachedCap']-mean_b)*(s['baseCap']-mean_a) for s in usable)/variance
    return rate if 0<=rate<=10 else None

def original_prices(astra_cached_rate=None, inference=None):
    models={model:{'short':rates,'long':None,'threshold':None} for model,rates in ORIGINAL_RATES.items()}
    models['gpt-6-astra']={'short':[10.0,astra_cached_rate,12.5,50.0],'long':None,'threshold':None}
    return {'source':'用户提供原价；Astra cache read 由本机周限额等量假设推测',
            'updatedAt':datetime.now(timezone.utc).isoformat(),
            'basis':'Original / USD per 1M tokens / input, cache read, cache write, output',
            'inference':inference or {},'models':models}

def parse_prices(text):
    section = text.split('### Standard pricing data', 1)[1].split('### Batch pricing data', 1)[0]
    models = {}
    for line in section.splitlines():
        cols = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cols) != 9 or not cols[1].startswith('$'):
            continue
        model = cols[0].split(' (')[0]
        rates = [None if c == '-' else float(c.replace('$', '').replace(',', '')) for c in cols[1:]]
        models[model] = {'short': rates[:4], 'long': rates[4:] if rates[4] is not None else None,
                         'threshold': 272000 if rates[4] is not None else None}
    if not all(m in models for m in ['gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna']):
        raise ValueError('Official table format changed; retaining verified snapshot')
    return {'source': SOURCE, 'updatedAt': datetime.now(timezone.utc).isoformat(),
            'basis': 'Standard API / USD per 1M tokens / current-price revaluation', 'models': models}

def refresh():
    request = urllib.request.Request(SOURCE + '.md', headers={'User-Agent':'LocalUsageDashboard/1.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = parse_prices(response.read().decode('utf-8'))
    temp = PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(PATH)
    return data

def value(event, prices):
    model = event['model']
    rates = prices['models'].get(model)
    if rates is None:
        # Only remove the official dated snapshot suffix; never guess model aliases.
        rates = prices['models'].get(re.sub(r'-\d{4}-\d{2}-\d{2}$', '', model))
    if rates is None: return None
    count = event['input'] + event['cached'] + event['write']
    rate = rates['long'] if rates.get('long') and count > rates['threshold'] else rates['short']
    amounts = [event['input'], event['cached'], event['write'], event['output']]
    if any(n and r is None for n,r in zip(amounts,rate)): return None
    return sum(n*(r or 0) for n,r in zip(amounts,rate))/1_000_000

if __name__ == '__main__':
    data = refresh()
    print('Updated official Standard prices:', len(data['models']), 'models')
