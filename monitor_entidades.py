#!/usr/bin/env python3
"""RadarDepositos: monitor adicional de WiZink, Cetelem, EBN y Openbank.

Trabaja SOBRE data/offers.json y no elimina ofertas ante una respuesta fallida.
Solo modifica tasas extraídas de la página oficial del producto, sin IA.
Las anomalías de >1 punto porcentual quedan pendientes de revisión.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
OFFER_PATH = BASE / 'data/offers.json'
STATE_PATH = BASE / 'data/monitor_state.json'
PENDING_PATH = BASE / 'data/pending.json'
NOW = datetime.now(timezone.utc).isoformat(timespec='seconds')
DATE = NOW[:10]
HEADERS = {'User-Agent': 'RadarDepositos/1.1 (public product rates research)', 'Accept-Language': 'es-ES,es;q=0.9'}
SOURCES = {
    'wizink': ('WiZink', 'https://www.wizink.es/public/productos-ahorro/deposito-plazo-fijo'),
    'cetelem': ('Cetelem', 'https://www.cetelem.es/ahorro-y-depositos-cetelem'),
    'ebn': ('EBN Banco', 'https://www.ebnbanco.com/ahorro/deposito-sinycon-plus/'),
    'openbank': ('Openbank', 'https://www.openbank.es/deposito-a-plazo-fijo/'),
}


def load(path, fallback):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else fallback


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def num(val):
    return float(str(val).strip().replace(',', '.'))


def html_text(url):
    r = requests.get(url, headers=HEADERS, timeout=25, allow_redirects=True)
    r.raise_for_status()
    if 'html' not in r.headers.get('content-type', 'text/html').lower():
        raise ValueError('Respuesta no HTML')
    if len(r.content) > 2_000_000:
        raise ValueError('Página excesivamente grande')
    soup = BeautifulSoup(r.text, 'html.parser')
    for node in soup(['script', 'style', 'noscript', 'svg']):
        node.decompose()
    return ' '.join(soup.stripped_strings)


def parse_cetelem(text):
    # El plazo debe aparecer DELANTE de la TAE en el mismo bloque de producto.
    matches = re.findall(
        r'Dep[oó]sito\s+a\s+(3|6|12|24)\s+meses\s+(.{0,160}?)\s*(\d[,.]\d{2})\s*%\s*TAE',
        text, re.I,
    )
    found = {}
    for months, nearby, rate in matches:
        # No se pretende captar ejemplos ajenos a los cuatro plazos ofrecidos.
        if len(nearby) > 90 or 'depósito a ' in nearby.lower():
            continue
        found[int(months)] = num(rate)
    return found


def parse_ebn(text):
    # La página oficial incluye ejemplos rotulados "2,40% TAE (3 meses)".
    return {int(m): num(r) for r, m in re.findall(
        r'(\d[,.]\d{2})\s*%\s*TAE\s*\(\s*(3|6|12|18|24|36|42|48)\s*meses\s*\)',
        text, re.I,
    )}


def parse_wizink(text):
    # Estructura oficial: "Depósito [Black Friday] 3,30% TAE ... 18 MESES".
    # Acota a un tramo de 100 caracteres y no mezcla productos.
    result = {}
    pattern = (r'Dep[oó]sito(?:\s+Black\s+Friday)?\s+(\d[,.]\d{2})\s*%\s*TAE'
               r'.{0,95}?\b(12|18|25|36)\s+MESES\b')
    for r, m in re.findall(pattern, text, re.I):
        result[int(m)] = num(r)
    return result


def parse_openbank(text):
    # Se requieren DOS tipos diferenciados y una fuente de producto no caducada.
    # Web puede presentar en paralelo versiones históricas, por tanto dejamos
    # Openbank en observación manual hasta disponer de parser verificado.
    return {}

PARSERS = {'wizink': parse_wizink, 'cetelem': parse_cetelem,
           'ebn': parse_ebn, 'openbank': parse_openbank}


def main():
    db = load(OFFER_PATH, None)
    if not db:
        print(f'No existe {OFFER_PATH}', file=sys.stderr)
        return 1
    pending = load(PENDING_PATH, {'candidates': []})
    state = load(STATE_PATH, {'sources': {}})
    state.setdefault('sources', {})
    db.setdefault('events', [])
    pending.setdefault('candidates', [])
    counts = {'ok': 0, 'errors': 0, 'changes': 0}
    for key, (bank, url) in SOURCES.items():
        try:
            content = html_text(url)
            rates = PARSERS[key](content)
            if key != 'openbank' and not rates:
                raise ValueError('No se pudo extraer ninguna TAE con suficiente fiabilidad')
            counts['ok'] += 1
            state['sources'][key] = {'checked_at': NOW,
                'result': 'ok' if rates else 'revision_manual', 'url': url,
                'verified_months': sorted(rates)}
            for row in db['offers']:
                if row.get('bank') != bank:
                    continue
                months = row.get('months')
                if months not in rates:
                    continue
                # No actualizar productos con variantes distintas del mismo plazo.
                if key == 'openbank':
                    continue
                old = float(row['tae']); new = rates[months]
                if not 0.1 <= new <= 10:
                    continue
                if abs(new - old) > 1:
                    candidate = {'bank':bank,'months':months,'old_tae':old,'tae':new,
                        'source_url':url,'status':'pendiente_revision','detected_at':NOW,
                        'notes':'Variación superior a un punto porcentual. No publicada.'}
                    if not any(x.get('bank') == bank and x.get('months') == months and x.get('tae') == new
                               for x in pending['candidates']):
                        pending['candidates'].append(candidate)
                    continue
                if abs(new - old) >= .005:
                    row['tae'] = new
                    # Si cambia la TAE, el TIN antiguo ya no es fiable.
                    row['tin'] = None
                    row['observed_at'] = DATE
                    db['events'].append({'bank':bank,'months':months,
                        'message':f'{old:.2f}% → {new:.2f}% TAE', 'at':NOW})
                    counts['changes'] += 1
                else:
                    row['observed_at'] = DATE
        except (requests.RequestException, ValueError, KeyError) as exc:
            counts['errors'] += 1
            state['sources'][key] = {'checked_at': NOW, 'result': 'error',
                'url': url, 'detail': str(exc)[:200]}
            print(f'AVISO {bank}: {exc}', file=sys.stderr)
    db.setdefault('meta', {})['last_checked'] = NOW
    db['meta']['monitor_enabled'] = True
    db['meta']['monitor_details'] = (
        f'Monitor original + ampliación española: {counts["ok"]} accesibles, '
        f'{counts["errors"]} fallos, {counts["changes"]} cambios. '
        'Openbank permanece en revisión manual por variantes de vinculación.')
    if counts['ok']:
        db['meta']['last_success'] = NOW
    db['events'] = db['events'][-80:]
    pending['candidates'] = pending['candidates'][-150:]
    save(OFFER_PATH, db); save(STATE_PATH, state); save(PENDING_PATH, pending)
    print(f'Monitor español: {counts}')
    return 0  # Un fallo de fuente no debe impedir publicar el resto.

if __name__ == '__main__':
    sys.exit(main())
