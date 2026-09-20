import hashlib
import json
import zipfile
from pathlib import Path
from pipeline.collect_yosar import FIELDS, profile


def test_yosar_original_hashes_minimization_and_quarantine():
    folder=Path(__file__).resolve().parents[2]/'data/research/yosar_endpoints_2000_2010'
    manifest=json.loads((folder/'manifest.json').read_text())
    assert manifest['license']=='CC-BY-NC-SA-4.0'
    assert manifest['trained_bundle_modified'] is False
    for f in manifest['files']:
        assert hashlib.sha256((folder/f['path']).read_bytes()).hexdigest()==f['sha256']
    data=json.loads((folder/'endpoint_links.minimized.json').read_text())
    assert all(set(row['attributes'])<=set(FIELDS) for row in data['features'])
    report,rows=profile(data)
    assert report==json.loads((folder/'quality_report.json').read_text())
    assert report['source_rows']==218 and report['all_lines_are_two_vertex_endpoint_links']
    assert report['total_fields']['TotalTim']['negative']==7
    assert all(not r['time_training_eligible'] and not r['trajectory_training_eligible'] for r in rows)


def test_offtrail_collection_preserves_author_archive_not_training_labels():
    folder=Path(__file__).resolve().parents[2]/'data/research/offtrail_lidar_2025'
    m=json.loads((folder/'manifest.json').read_text())
    assert m['license']=='CC-BY-4.0' and m['trained_bundle_modified'] is False
    assert m['status']=='COLLECTED_NOT_USED_FOR_TRAINING'
    for f in m['files']:
        assert hashlib.sha256((folder/f['path']).read_bytes()).hexdigest()==f['sha256']
    archive=folder/'trajectories.zip'
    assert 'md5:'+hashlib.md5(archive.read_bytes()).hexdigest()==m['files'][1]['author_checksum']
    with zipfile.ZipFile(archive) as z:
        for f in m['archive_inventory']:
            content=z.read(f['name'])
            assert hashlib.sha256(content).hexdigest()==f['sha256']
            assert (content[:4]==b'LASF')==f['las_signature']
    assert m['las_files']==165 # Actual archive inventory, not the paper's number of analyzed tracks.
