"""Conservative adapters for ruled digital PDF tables; no OCR inference."""
import re
import pdfplumber
from .core import digest, dump, norm

TABLE_VERSION = 'ruled-tables-1.0.1'


def clean_page(page):
    # Only table parsing drops oblique text. Original PDF and raw text stay intact.
    return page.filter(lambda x: x.get('object_type') != 'char' or
                       (abs(x.get('matrix', (1, 0, 0, 1))[1]) < .01 and
                        abs(x.get('matrix', (1, 0, 0, 1))[2]) < .01))


def response_rows(ctx):
    rows = []
    for doc in ctx.documents('bid'):
        if doc['_path'].suffix.lower() != '.pdf':
            ctx.warnings.append(doc['id'] + ': automatic response-table extraction supports PDF only')
            continue
        selected = [p['page'] for p in ctx.pages(doc) if '偏离' in norm(p['text'])]
        key = digest((doc['sha256'] + TABLE_VERSION + str(selected)).encode())
        cache = ctx.cache / (key + '.responses.json')
        if cache.exists():
            extracted = __import__('json').loads(cache.read_text())
        else:
            extracted = []
            active = None
            last_page = None
            with pdfplumber.open(doc['_path']) as pdf:
                for pnum in selected:
                    if last_page is None or pnum != last_page + 1:
                        active = None
                    page = pdf.pages[pnum - 1]
                    for table in clean_page(page).find_tables():
                        data = table.extract()
                        if not data: continue
                        header = [norm(v) for v in data[0]]
                        req = next((i for i, v in enumerate(header) if '采购文件规定' in v or '招标要求' in v), None)
                        resp = next((i for i, v in enumerate(header) if '投标文件响应' in v or '实际响应' in v), None)
                        dev = next((i for i, v in enumerate(header) if '偏离' in v), None)
                        if req is not None and resp is not None:
                            active = (req, resp, dev, len(header)); data = data[1:]
                        if active is None: continue
                        req, resp, dev, width = active
                        for ri, raw in enumerate(data):
                            if len(raw) != width: continue
                            q, a = str(raw[req] or '').strip(), str(raw[resp] or '').strip()
                            if not q or not a: continue
                            item = norm(raw[0])
                            ref = {'page': pnum, 'table_bbox': list(table.bbox), 'table_row': ri + 1}
                            starts_clause = bool(re.match(r'^[★▲△]*\s*\d+[)）.、]', q))
                            if not item and extracted and ri == 0 and last_page == pnum - 1 and not starts_clause:
                                # Only contiguous continuation rows with no row number.
                                extracted[-1]['requirement'] += '\n' + q
                                extracted[-1]['response'] += '\n' + a
                                extracted[-1]['locations'].append(ref)
                            elif not item or re.fullmatch(r'\d+(?:\.\d+)*', item):
                                extracted.append({'item_no': item or 'merged-column', 'requirement': q, 'response': a,
                                                  'declared_deviation': str(raw[dev] or '') if dev is not None else '',
                                                  'locations': [ref]})
                    last_page = pnum
                    page.close()
            dump(cache, extracted)
        for row in extracted:
            rows.append({**row, 'bidder': doc['bidder'], 'round': doc['round'], 'lot': doc['lot'],
                         'evidence': [ctx.evidence(doc, r['page'], row['response'], table_bbox=r['table_bbox'],
                                                  table_row=r['table_row'], extraction='ruled_table') for r in row['locations']]})
    # Optional agent-reviewed alignments retain original-page verification.
    for row in ctx.manifest.get('response_items', []):
        ref = ctx.verify_quote(row['source'])
        reqref = ctx.verify_quote(row['requirement_source'])
        if norm(row['response']) not in norm(ref['quote']) or norm(row['requirement']) not in norm(reqref['quote']):
            raise ValueError('Response item values must occur in their source quotations')
        doc = ctx.by_id[ref['document_id']]
        if doc['role'] != 'bid': raise ValueError('Response must be grounded in a bid')
        rows.append({**row, 'bidder': doc['bidder'], 'round': doc['round'], 'lot': doc['lot'],
                     'declared_deviation': row.get('declared_deviation', ''), 'evidence': [ref, reqref]})
    return rows


def requirement_key(text):
    # Strip a clause ordinal, not any substantive digits or numerical operators.
    return re.sub(r'^[★▲△\d.、)）(（]+', '', norm(text)).rstrip('。；;.')
