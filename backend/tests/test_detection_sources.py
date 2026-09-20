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


ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'data/research/detection_sources_2026'
RECEIPT_ID = '6d70dc3a056fd858f7caceb10412246efe36bc81a8b263c5661af73df49c8282'


def load_receipt():
    raw = (FOLDER / 'receipts' / (RECEIPT_ID + '.json')).read_bytes()
    assert sha(raw) == RECEIPT_ID
    return json.loads(raw)


def test_collected_papers_are_references_not_training_observations():
    receipt = load_receipt()
    assert receipt['collector_sha256'] == sha((ROOT / 'pipeline/collect_detection_sources.py').read_bytes())
    assert receipt['training_rows_emitted'] == 0
    assert {s['key'] for s in receipt['sources']} == set(SOURCES)
    assert sum(len(s['pages']) for s in receipt['sources']) == 74
    for s in receipt['sources']:
        assert s['role'] == 'LITERATURE_REVIEW_ONLY'
        assert s['training_rows_emitted'] == 0
        assert s['reuse_license'] == 'NOT_VERIFIED_DO_NOT_REDISTRIBUTE'
        assert s['embedded_file_names'] == []


# 원본 PDF(objects/)는 재배포 조건 미확인(NOT_VERIFIED_DO_NOT_REDISTRIBUTE)이라 깃허브 저장소에 없다.
# 수집한 컴퓨터에서만 바이트 일치를 검사하고, 없으면 건너뛴다.
@pytest.mark.skipif(not (FOLDER / 'objects').exists(), reason='탐지 연구 PDF 원본(objects/) 없음 — 로컬 전용, 저장소 미포함')
def test_collected_paper_bytes_match_receipt():
    receipt = load_receipt()
    for s in receipt['sources']:
        content = (FOLDER / s['path']).read_bytes()
        assert content.startswith(b'%PDF-')
        assert sha(content) == s['sha256'] and len(content) == s['bytes']
