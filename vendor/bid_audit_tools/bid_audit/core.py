from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path

from pypdf import PdfReader
from docx import Document

EXTRACT_VERSION = 'text-1.0.0'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def norm(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temp, path)


def contained(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path escapes permitted root')
    return path


class Context:
    def __init__(self, manifest_path, cache_dir, permitted_root=None):
        self.manifest_path = Path(manifest_path).resolve()
        if permitted_root:
            contained(self.manifest_path, permitted_root)
        self.manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        if self.manifest.get('schema_version') != '1.0':
            raise ValueError('manifest.schema_version must be 1.0')
        if not self.manifest.get('project_id'):
            raise ValueError('project_id required')
        self.root = (self.manifest_path.parent / self.manifest.get('data_root', '.')).resolve()
        if permitted_root:
            contained(self.root, permitted_root)
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.docs = []
        self.warnings = []
        ids = set()
        assignments = set()
        for raw in self.manifest.get('documents', []):
            doc = dict(raw)
            if not doc.get('id') or doc['id'] in ids:
                raise ValueError('Missing or duplicate document id')
            ids.add(doc['id'])
            if doc.get('role') not in ('tender', 'bid', 'quote_detail', 'quote_opening', 'public_template'):
                raise ValueError('Only original tender/bid/quote/public_template documents are accepted')
            if doc['role'] in ('bid', 'quote_detail', 'quote_opening') and not doc.get('bidder'):
                raise ValueError('bidder required for bidding documents')
            doc.setdefault('round', '1')
            doc.setdefault('lot', '1')
            if not isinstance(doc['round'], str) or not isinstance(doc['lot'], str):
                raise ValueError('round and lot must be strings')
            path = contained(self.root / doc['path'], self.root)
            if path.suffix.lower() not in ('.pdf', '.docx', '.txt') or not path.is_file():
                raise ValueError(f'Unsupported or missing document: {doc["id"]}')
            doc['_path'] = path
            assignment=(str(path),doc['round'],doc['lot'])
            if assignment in assignments:raise ValueError('Same source file assigned more than once in one round/lot')
            assignments.add(assignment)
            doc['sha256'] = file_hash(path)
            if doc.get('expected_sha256') and doc['expected_sha256'] != doc['sha256']:
                raise ValueError(f'Source hash mismatch: {doc["id"]}')
            doc['path'] = str(path.relative_to(self.root))
            self.docs.append(doc)
        if not self.docs:
            raise ValueError('No documents supplied')
        self.by_id = {d['id']: d for d in self.docs}
        self._texts = {}
        self.params = self.manifest.get('parameters', {})
        if not isinstance(self.params, dict):
            raise ValueError('parameters must be an object')
        bounds={'template_min_exact_chars':(12,100,True),'min_residual_chars':(12,1200,True),
                'response_similarity':(0,1,False),'image_pair_limit':(1,2000000,True),
                'image_dhash_distance':(0,16,True)}
        for key,value in self.params.items():
            if key not in bounds:raise ValueError('Unknown parameter: '+key)
            low,high,integer=bounds[key]
            if type(value) not in ((int,) if integer else (int,float)) or not low<=value<=high:
                raise ValueError(f'Invalid parameter {key}: expected {low}..{high}')

    def documents(self, *roles):
        return [d for d in self.docs if d['role'] in roles]

    def pages(self, doc):
        if doc['id'] in self._texts:
            return self._texts[doc['id']]
        key = digest((doc['sha256'] + EXTRACT_VERSION).encode())
        cache = self.cache / (key + '.json')
        if cache.exists():
            result = json.loads(cache.read_text(encoding='utf-8'))
        else:
            path = doc['_path']
            if path.suffix.lower() == '.pdf':
                reader = PdfReader(path)
                if reader.is_encrypted:
                    raise ValueError('Encrypted PDF requires an explicitly decrypted source')
                pages = [{'page': i, 'text': p.extract_text() or ''} for i, p in enumerate(reader.pages, 1)]
            elif path.suffix.lower() == '.docx':
                f = Document(path)
                text = '\n'.join(p.text for p in f.paragraphs)
                text += '\n' + '\n'.join('\t'.join(c.text for c in r.cells) for t in f.tables for r in t.rows)
                pages = [{'page': None, 'text': text, 'locator_kind': 'docx_body'}]
            else:
                pages = [{'page': None, 'text': path.read_text(encoding='utf-8'), 'locator_kind': 'text_body'}]
            result = {'extract_version': EXTRACT_VERSION, 'sha256': doc['sha256'], 'pages': pages}
            dump(cache, result)
        pages = result['pages']
        selected = doc.get('pages')
        if selected is not None:
            if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
                    or not all(type(x) is int and 1 <= x <= len(pages) for x in selected)):
                raise ValueError(f'Invalid page selection: {doc["id"]}')
            pages = [p for p in pages if p['page'] in selected]
            self.warnings.append(f'{doc["id"]}: selected pages only; excluded pages were not audited')
        def substantive_chars(p):
            lines=[x for x in p['text'].splitlines() if not re.match(r'^\s*(广东政府采购智慧云平台|第\s*\d+\s*页)',x)
                   and not re.fullmatch(r'[\s—\-\d]+',x)
                   and not (doc.get('bidder') and doc['bidder'] in x and re.search(r'20\d{2}[-年]\d',x))]
            return len(norm(''.join(lines)))
        low = [p['page'] for p in pages if substantive_chars(p) < 40]
        if low:
            self.warnings.append(f'{doc["id"]}: {len(low)} pages have little/no extractable text; no OCR performed')
        self._texts[doc['id']] = pages
        return pages

    def evidence(self, doc, page, quote='', **extra):
        return {'document_id': doc['id'], 'path': doc['path'], 'sha256': doc['sha256'],
                'bidder': doc.get('bidder'), 'round': doc['round'], 'lot': doc['lot'],
                'page': page, 'quote': quote, **extra}

    def verify_quote(self, ref):
        doc = self.by_id[ref['document_id']]
        text = norm(ref.get('quote', ''))
        if not text or not any(text in norm(p['text']) for p in self.pages(doc) if p['page'] == ref.get('page')):
            raise ValueError('Structured evidence quote was not found on the declared original page')
        return self.evidence(doc, ref.get('page'), ref['quote'])

    def source_inventory(self):
        return [{k: v for k, v in d.items() if not k.startswith('_')} for d in self.docs]


def groups(docs):
    out = {}
    for d in docs:
        out.setdefault((d['round'], d['lot']), []).append(d)
    return out


def outcome(metrics=None, findings=None, status='ok', limitations=None, **extra):
    return {'status': status, 'metrics': metrics or {}, 'findings': findings or [],
            'limitations': limitations or [], **extra}
