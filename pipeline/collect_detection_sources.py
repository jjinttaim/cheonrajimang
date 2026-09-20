"""Archive selected public POD papers for source review, NOT model training.

Run with the document runtime (pypdf); no application dependencies are changed.
The index inventories pages, hyperlinks and embedded files. None of these prove
that raw observations do not exist elsewhere, or grant reuse rights. No graph is
digitized and no reported model prediction is promoted to an observed label.
"""
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/detection_sources_2026'
SOURCES = {
    'chiacchia_houlahan_2023': {
        'url': 'https://journalofsar.com/wp-content/uploads/2023/01/jsar_v6i1-chiaccchia.pdf',
        'doi': '10.61618/NPEN4588', 'expected_pages': 36,
    },
    'chiacchia_scelza_2023': {
        'url': 'https://journalofsar.com/wp-content/uploads/2023/10/jsar-v6i2-1-chiacchia-truncated-exercise.pdf',
        'doi': '10.61618/GNLN7584', 'expected_pages': 11,
    },
    'chiacchia_billings_houlahan_2025': {
        'url': 'https://journalofsar.com/wp-content/uploads/2025/08/v8i1_chiacchia_v3.pdf',
        'doi': '10.61618/FLEB2002', 'expected_pages': 27,
    },
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def immutable(path, raw):
    """Publish once; never overwrite a previously archived source/receipt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Existing archive differs: ' + str(path))
        return
    fd, name = tempfile.mkstemp(prefix='.download-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
        # link is exclusive: a concurrent publication cannot be overwritten.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('Concurrent archive differs: ' + str(path))
    finally:
        temporary.unlink(missing_ok=True)


def inspect_pdf(raw):
    from pypdf import PdfReader
    if not raw.startswith(b'%PDF-'):
        raise ValueError('Not a PDF')
    reader = PdfReader(BytesIO(raw), strict=True)
    pages, links = [], []
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ''
        pages.append({'page': number, 'text_characters': len(text),
                      'extracted_text_sha256': sha(text.encode())})
        for ref in page.get('/Annots', []):
            action = ref.get_object().get('/A')
            if action:
                uri = action.get_object().get('/URI')
                if uri:
                    links.append({'page': number, 'uri': str(uri)})
    return {'pages': pages, 'uri_annotations': links,
            'embedded_file_names': list(reader.attachments.keys())}


def main():
    records = []
    for key, source in SOURCES.items():
        # Public fixed publisher URLs only; no mission data is transmitted.
        raw = subprocess.run([
            '/usr/bin/curl', '-fsSL', '--proto', '=https', '--proto-redir', '=https',
            '--max-time', '45', '--max-filesize', '10000000', source['url'],
        ], check=True, capture_output=True).stdout
        if len(raw) > 10_000_000:
            raise ValueError('Document too large')
        index = inspect_pdf(raw)
        if len(index['pages']) != source['expected_pages']:
            raise ValueError('Publisher document changed; review before accepting')
        filename = f'objects/{sha(raw)}.pdf'
        immutable(OUT / filename, raw)
        records.append({'key': key, **source, 'path': filename, 'bytes': len(raw),
                        'sha256': sha(raw), **index,
                        'role': 'LITERATURE_REVIEW_ONLY',
                        'training_rows_emitted': 0,
                        'reuse_license': 'NOT_VERIFIED_DO_NOT_REDISTRIBUTE'})
        print(json.dumps({'source': key, 'pages': len(index['pages']),
                          'embedded_files': len(index['embedded_file_names']),
                          'bytes': len(raw)}, ensure_ascii=False), flush=True)
    receipt = {
        'schema': 'detection-literature-review-v1',
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'collector_sha256': sha(Path(__file__).read_bytes()),
        'sources': records, 'training_rows_emitted': 0,
        'limitations': [
            'A PDF/table/graph is not a row-level observation archive.',
            'Absence of embedded data/links is not proof that data do not exist elsewhere.',
            'Open access reading is not an assumed license for redistribution.',
            'No model file, mission record or default parameter is changed.',
        ],
    }
    encoded = canonical(receipt)
    path = OUT / 'receipts' / (sha(encoded) + '.json')
    immutable(path, encoded)
    print(json.dumps({'receipt': str(path), 'training_rows_emitted': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
