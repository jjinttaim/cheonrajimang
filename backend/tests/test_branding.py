import io
import json
import zipfile
from backend import main
from backend.tests.test_searchproof import client, current


def test_display_brand_and_legacy_identifiers_preserve_history(client):
    mid,before=current(client)
    assert '천라지망' in main.app.title
    assert client.get('/api/health').json()['engine'].startswith('searchproof-')
    report=client.get(f'/api/missions/{mid}/report')
    assert '<h1>천라지망 수색 상황보고서</h1>' in report.text
    package=client.get(f'/api/missions/{mid}/package.zip')
    assert package.status_code==200
    assert 'Cheonrajimang-v0.zip' in package.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(package.content)) as z:
        assert z.read('README.txt').decode().startswith('천라지망')
        assert json.loads(z.read('manifest.json'))['format']=='searchproof-package-v1'
    check=client.post('/api/verify',files={'file':('package.zip',package.content)})
    assert check.json()['valid']
    after=client.get('/api/missions/'+mid).json()
    assert after['receipt']['hash']==before['receipt']['hash']
    assert after['versions']==before['versions']
