"""Source retention tests; fixtures are never emitted as training data."""
from pathlib import Path
import json

import pytest

from pipeline.collect_detection_sources import SOURCES, canonical, immutable, sha


def test_immutable_archive_preserves_previous_bytes(tmp_path):
    path = tmp_path / 'objects/source'
    immutable(path, b'first source')
    immutable(path, b'first source')
    with pytest.raises(ValueError, match='Existing archive differs'):
        immutable(path, b'changed source')
    assert path.read_bytes() == b'first source'
    assert not list(path.parent.glob('.download-*'))


def test_canonical_receipt_has_no_nonfinite_numbers():
    assert canonical({'b': 2, 'a': 1}) == canonical({'a': 1, 'b': 2})
    with pytest.raises(ValueError):
        canonical({'value': float('nan')})


def test_collected_papers_are_references_not_training_observations():
    root = Path(__file__).resolve().parents[2]
    folder = root / 'data/research/detection_sources_2026'
    receipt_id = '6d70dc3a056fd858f7caceb10412246efe36bc81a8b263c5661af73df49c8282'
    raw = (folder / 'receipts' / (receipt_id + '.json')).read_bytes()
    assert sha(raw) == receipt_id
    receipt = json.loads(raw)
    assert receipt['collector_sha256'] == sha((root / 'pipeline/collect_detection_sources.py').read_bytes())
    assert receipt['training_rows_emitted'] == 0
    assert {s['key'] for s in receipt['sources']} == set(SOURCES)
    assert sum(len(s['pages']) for s in receipt['sources']) == 74
    for s in receipt['sources']:
        content = (folder / s['path']).read_bytes()
        assert content.startswith(b'%PDF-')
        assert sha(content) == s['sha256'] and len(content) == s['bytes']
        assert s['role'] == 'LITERATURE_REVIEW_ONLY'
        assert s['training_rows_emitted'] == 0
        assert s['reuse_license'] == 'NOT_VERIFIED_DO_NOT_REDISTRIBUTE'
        assert s['embedded_file_names'] == []
