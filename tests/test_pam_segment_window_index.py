"""Exercise the rendered window page with Chrome and an isolated local archive."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest


@pytest.mark.parametrize('indexed', [True, False])
@pytest.mark.parametrize('viewport', [(1440, 1000), (480, 900)])
def test_segment_window_cuts_local_files_in_browser(auth_client, mock_pam_conn, tmp_path, indexed, viewport):
    chrome = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    if not chrome.is_file():
        pytest.skip('Local Chrome is unavailable')
    models = [{'model_id': 1, 'label': 'BirdNET'}, {'model_id': 2, 'label': 'Perch'}]
    with mock_pam_conn(), patch('app.pam.routes.get_models_list', return_value=models):
        rendered = auth_client(role='admin').get('/uk/pam/verification/segment-window').get_data(as_text=True)
    # Use the actual page controls, styles and script with isolated local files.
    body = re.sub(r'<script\b[^>]*>.*?</script>', '', rendered, flags=re.S)
    inline = [script for script in re.findall(r'<script\b[^>]*>(.*?)</script>', rendered, re.S)
              if 'const URL_PLAN' in script][0]
    bootstrap = r"""
    const realFetch = window.fetch.bind(window);
    let planRequests = 0, uploads = 0, lookupRequests = 0;
    window.fetch = async (url, options) => {
        if (String(url).includes('/sample/plan-window')) {
            planRequests++;
            return new Response(JSON.stringify({success: true, limit: 200, segments: [{
                detection_id: 1, recording_id: 2, species_id: 3, location_id: 121,
                recording_filename: 'TEST_20250508_035000.wav',
                segment_filename: '0.950_TEST_20250508_035000_sec0_part1.flac',
                start_s: 0, confidence: 0.95, model_id: 1
            }]}), {headers: {'Content-Type': 'application/json'}});
        }
        if (String(url).includes('/sample/recording-locations')) {
            lookupRequests++;
            throw Error('A completed index should not query recording locations');
        }
        if (String(url).includes('/sample/upload-segment')) {
            const clip = options.body.get('segment');
            if (!(clip instanceof Blob) || clip.size <= 44) throw Error('Missing audio clip');
            uploads++;
            return new Response(JSON.stringify({success: true, status: 'saved'}));
        }
        return realFetch(url, options);
    };
    window.addEventListener('error', event => report('FAIL: ' + event.message));
    window.addEventListener('unhandledrejection', event => report('FAIL: ' + event.reason));
    function report(value) { return realFetch('/result', {method: 'POST', body: value}); }
    async function waitFor(predicate) {
        for (let n = 0; n < 300; n++) {
            if (predicate()) return;
            await new Promise(resolve => setTimeout(resolve, 20));
        }
        throw Error('Timed out waiting for page state');
    }
    async function write(dir, name, contents) {
        const handle = await dir.getFileHandle(name, {create: true});
        const writer = await handle.createWritable();
        await writer.write(contents);
        await writer.close();
    }
    const get = id => document.getElementById(id);
    async function testPage() {
        await waitFor(() => get('sw-plan-body').children.length === 1);
        await waitFor(() => getComputedStyle(document.querySelector('.sw-page')).maxWidth === '960px');
        const page = document.querySelector('.sw-page').getBoundingClientRect();
        if (Math.abs(page.left + page.width / 2 - document.documentElement.clientWidth / 2) > 2) {
            throw Error('Page content is not centered');
        }
        if (page.width > document.documentElement.clientWidth) throw Error('Page exceeds viewport');
        const fileName = 'TEST_20250508_035000.wav';
        const audio = PamSegmentCutter.encodeWav([new Float32Array(24000)], 24000);
        if (INDEXED) {
            const root = await navigator.storage.getDirectory();
            const folder = await root.getDirectoryHandle('recordings', {create: true});
            await write(folder, fileName, audio);
            const locations = [{location_id: 121, lat: 49.9, lon: 23.7}];
            await write(folder, 'location.json', JSON.stringify({pam_version: 1, leaf: true, locations}));
            await write(root, '.pam-location-folders-v1.json',
                JSON.stringify({pam_version: 1, folders: [{path: ['recordings'], locations}]}));
            window.showDirectoryPicker = async () => root;
            if (!get('sw-use-index').checked) throw Error('Index mode was not selected');
            get('sw-index-folder').click();
        } else {
            get('sw-use-index').checked = false;
            get('sw-use-index').dispatchEvent(new Event('change'));
            const transfer = new DataTransfer();
            transfer.items.add(new File([audio], fileName));
            get('sw-folder').files = transfer.files;
            get('sw-folder').dispatchEvent(new Event('change'));
        }
        await waitFor(() => !get('sw-run').disabled);
        if ((getComputedStyle(get('sw-folder')).display === 'none') !== INDEXED) {
            throw Error('File input visibility does not follow index mode');
        }
        if ((getComputedStyle(get('sw-index-folder')).display === 'none') === INDEXED) {
            throw Error('Index picker visibility does not follow index mode');
        }
        if (!get('sw-search-archive').hidden) throw Error('Unexpected archive scan');
        if (getComputedStyle(get('sw-search-archive')).display !== 'none') {
            throw Error('Hidden archive button is visible');
        }
        if (INDEXED) {
            get('sw-search-archive').hidden = false;
            const picker = get('sw-index-folder').getBoundingClientRect();
            const search = get('sw-search-archive').getBoundingClientRect();
            if (search.left - picker.right < 10 && search.top - picker.bottom < 10) {
                throw Error('Folder buttons lack spacing');
            }
            get('sw-search-archive').hidden = true;
        }
        // Changing the model must resolve files again for the new plan.
        if (get('sw-model').tagName === 'SELECT') {
            get('sw-model').value = '2';
            get('sw-model').dispatchEvent(new Event('change'));
            await waitFor(() => planRequests === 2 && !get('sw-run').disabled);
        }
        get('sw-run').click();
        await waitFor(() => get('sw-stat-saved').textContent === '1' && !get('sw-run').disabled);
        if (uploads !== 1 || lookupRequests !== 0) throw Error('Unexpected requests');
        await report('PASS');
    }
    setTimeout(() => testPage().catch(error => report('FAIL: ' + error.stack)), 0);
    """.replace('INDEXED', json.dumps(indexed))
    html = body + '<script src="/segment_cutter.js"></script><script src="/recording_index.js"></script>'
    html += '<script>' + bootstrap + '</script><script>' + inline + '</script>'
    finished = threading.Event()
    results = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            request_path = urlsplit(self.path).path
            assets = {
                '/segment_cutter.js': (Path('app/pam/static/js/segment_cutter.js'), 'text/javascript'),
                '/recording_index.js': (Path('app/pam/static/js/recording_index.js'), 'text/javascript'),
                '/uk/pam-static/css/pam_style.css': (Path('app/pam/static/css/pam_style.css'), 'text/css'),
                '/static/css/style.css': (Path('app/static/css/style.css'), 'text/css'),
            }
            asset = assets.get(request_path)
            self.send_response(200)
            self.send_header('Content-Type', asset[1] if asset else 'text/html; charset=utf-8')
            self.end_headers()
            content = asset[0].read_bytes() if asset else html.encode('utf-8')
            self.wfile.write(content)

        def do_POST(self):
            results.append(self.rfile.read(int(self.headers['Content-Length'])).decode())
            self.send_response(200)
            self.end_headers()
            finished.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = subprocess.Popen([
        str(chrome), '--headless', '--disable-gpu', '--no-first-run',
        f'--window-size={viewport[0]},{viewport[1]}',
        '--user-data-dir=' + str(tmp_path / 'chrome'),
        f'http://127.0.0.1:{server.server_port}/?species=Test&location_ids=121'
        '&from_ts=2025-05-08T03:50:00&to_ts=2025-05-08T03:51:00',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        assert finished.wait(20), 'Browser did not finish'
        assert results == ['PASS']
    finally:
        process.terminate()
        process.wait(timeout=10)
        server.shutdown()
        server.server_close()
