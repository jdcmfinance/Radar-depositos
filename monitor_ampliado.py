#!/usr/bin/env python3
"""Supervisa las nuevas fuentes sin modificar las tasas publicadas automáticamente.
Las discrepancias, variantes y cambios de contenido pasan a data/pending.json.
"""
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from monitor_entidades import html_text

ROOT = Path(__file__).resolve().parent
NOW = datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(name, fallback):
    path = ROOT / 'data' / name
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else fallback


def write(name, value):
    (ROOT / 'data' / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def extract_raisin(text):
    """Solo TAE seguidas inmediatamente de depósito a plazo en EUR.
    Conserva variantes de un mismo plazo como conjunto; nunca toma una cuenta de ahorro.
    """
    result = defaultdict(set)
    pattern = r'(\d{1,2}[,.]\d{1,2})\s*%\s*TAE\s*Dep[oó]sito\s+a\s+plazo\s*·\s*(\d+(?:[,.]\d+)?)\s*(mes(?:es)?|año(?:s)?)\s*·\s*En\s+euro'
    for rate, quantity, unit in re.findall(pattern, text, re.I):
        months = float(quantity.replace(',', '.')) * (12 if unit.lower().startswith('año') else 1)
        if months.is_integer() and 1 <= months <= 120:
            result[int(months)].add(float(rate.replace(',', '.')))
    return dict(result)


def inspect_source(item):
    url, rows = item
    entry = {'checked_at': NOW, 'url': url, 'bank': rows[0]['bank'], 'mode': 'review'}
    try:
        if urlparse(url).scheme != 'https':
            raise ValueError('Fuente no HTTPS')
        raw = html_text(url)
        if len(raw) < 300:
            raise ValueError('Texto insuficiente')
        entry['fingerprint'] = hashlib.sha256(raw.encode()).hexdigest()
        if rows[0]['source_type'] == 'distribuidor' and urlparse(url).hostname == 'www.raisin.com':
            rates = extract_raisin(raw)
            if not rates:
                raise ValueError('No se encontraron bloques TAE/plazo en euros')
            entry['result'] = 'revision_manual'
            entry['rates'] = {str(k): sorted(v) for k, v in sorted(rates.items())}
        else:
            entry['result'] = 'revision_manual'
        return entry
    except Exception as exc:
        entry.update(result='error', detail=str(exc)[:200])
        return entry


def add_pending(pending, candidate):
    signature = tuple(candidate.get(k) for k in ('source_url', 'months', 'kind', 'signature'))
    if not any(tuple(x.get(k) for k in ('source_url', 'months', 'kind', 'signature')) == signature for x in pending):
        pending.append(candidate)


def main():
    db = read('offers.json', {})
    state = read('monitor_state.json', {})
    sources = state.setdefault('sources', {})
    pending = read('pending.json', {'candidates': []})
    queue = pending.setdefault('candidates', [])
    groups = defaultdict(list)
    for row in db['offers']:
        if row.get('monitor_mode') == 'review':
            groups[row['source_url']].append(row)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(inspect_source, groups.items()))
    for (url, rows), entry in zip(groups.items(), results):
        key = 'ampliacion:' + url
        previous = sources.get(key, {})
        if entry['result'] != 'error':
            if 'rates' in entry:
                known = defaultdict(set)
                for row in rows:
                    known[row['months']].add(float(row['tae']))
                seen = {int(m): set(r) for m, r in entry['rates'].items()}
                for months, rates in seen.items():
                    old = known.get(months, set())
                    if rates != old:
                        add_pending(queue, {'bank': rows[0]['bank'], 'months': months, 'source_url': url,
                            'kind': 'cambio_o_nueva_variante', 'signature': repr((sorted(old), sorted(rates))),
                            'published_rates': sorted(old), 'observed_rates': sorted(rates), 'detected_at': NOW,
                            'status': 'pendiente_revision', 'notes': 'No publicado automáticamente; verificar producto, vigencia y condiciones.'})
                for months in known.keys() - seen.keys():
                    add_pending(queue, {'bank': rows[0]['bank'], 'months': months, 'source_url': url,
                        'kind': 'plazo_no_encontrado', 'signature': repr(sorted(known[months])),
                        'detected_at': NOW, 'status': 'pendiente_revision', 'notes': 'No retirar por ausencia en una consulta.'})
            elif previous.get('fingerprint') and previous['fingerprint'] != entry['fingerprint']:
                add_pending(queue, {'bank': rows[0]['bank'], 'source_url': url, 'kind': 'cambio_contenido',
                    'signature': entry['fingerprint'], 'detected_at': NOW, 'status': 'pendiente_revision',
                    'notes': 'La página cambió; puede ser un cambio ajeno al depósito. Revisar manualmente.'})
        elif previous.get('fingerprint'):
            entry['fingerprint'] = previous['fingerprint']
        sources[key] = entry
        print(f"{entry['bank']}: {entry['result']}")
    pending['candidates'] = queue[-500:]
    # Resumen del ciclo actual: no confundir página accesible con tasa comprobada.
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=20)
    current = []
    for item in sources.values():
        try:
            if datetime.fromisoformat(item['checked_at']) >= cutoff:
                current.append(item)
        except (KeyError, ValueError, TypeError):
            pass
    ok = sum(x.get('result') == 'ok' for x in current)
    manual = sum(x.get('result') == 'revision_manual' for x in current)
    errors = sum(x.get('result') == 'error' for x in current)
    meta = db.setdefault('meta', {})
    meta.update(last_checked=NOW, monitor_enabled=True,
                monitor_details=f'{ok} fuentes monitor anterior sin error, {manual} fuentes en revisión manual, {errors} fallos. Cambios nuevos pendientes de revisión.')
    if ok or manual:
        meta['last_success'] = NOW
    write('offers.json', db)
    write('monitor_state.json', state)
    write('pending.json', pending)


if __name__ == '__main__':
    main()
