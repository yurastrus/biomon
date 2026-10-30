# SPDX-License-Identifier: AGPL-3.0-only
"""Snapshot every camera-trap statistic so two code versions can be diffed.

Built for the multi-species change: take a snapshot with the old code, change
the code, take another one on the SAME data, then compare. Any difference on
data that has no multi-species votes is a regression.

SAFETY: refuses to run unless CT_DATABASE_URL points at the sandbox cluster
(server port 5544, data directory ms_sandbox). The `--rebuild` step rewrites
the precomputed analytics tables, which is only acceptable there.

Usage (tunnel: ssh -N -L 5545:localhost:5544 yurastrus.dev):
    CT_DATABASE_URL=postgresql://ct_user@localhost:5545/ct_db \
        venv/Scripts/python -m scripts.snapshot_ct_stats OUT_DIR [--rebuild]
    venv/Scripts/python -m scripts.snapshot_ct_stats --diff OLD_DIR NEW_DIR
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

YEARS = ('2020-08-01', '2026-12-31')
D = f"start_date={YEARS[0]}&end_date={YEARS[1]}"

# (name, url) pairs, all GET, all as admin.
URLS = [
    ('top_species', f'/uk/camera-traps/api/stats/top-species?{D}'),
    ('top_contributors', f'/uk/camera-traps/api/stats/top-contributors?{D}'),
    ('locations', f'/uk/camera-traps/api/stats/locations?{D}'),
    ('distribution_map_sp1', f'/uk/camera-traps/api/stats/distribution-map?{D}&species_id={{SP1}}'),
    ('distribution_map_sp2', f'/uk/camera-traps/api/stats/distribution-map?{D}&species_id={{SP2}}'),
    ('distribution_map_sp5', f'/uk/camera-traps/api/stats/distribution-map?{D}&species_id={{SP5}}'),
    ('daily_activity', f'/uk/camera-traps/api/stats/daily-activity?{D}&species_ids={{SP}}'),
    ('activity_heatmap', f'/uk/camera-traps/api/stats/activity-heatmap?{D}&species_id={{SP1}}'),
    ('activity_heatmap_sp2', f'/uk/camera-traps/api/stats/activity-heatmap?{D}&species_id={{SP2}}'),
    ('species_dynamics', '/uk/camera-traps/api/stats/species-dynamics?species_id={SP1}&start_year=2020&end_year=2026'),
    ('gallery_photos_sp1', '/uk/camera-traps/api/gallery/photos?species_id={SP1}'),
    ('gallery_photos_sp2', '/uk/camera-traps/api/gallery/photos?species_id={SP2}'),
    ('behavior_data', f'/uk/camera-traps/api/behavior/data?{D}&species_id={{SP1}}'),
    ('behavior_species', '/uk/camera-traps/api/behavior/species-with-behaviors'),
    ('identification_stats', '/uk/camera-traps/api/identification-stats'),
    ('review_filters', '/uk/camera-traps/api/review-filters'),
    ('gap_grid', '/uk/camera-traps/api/gap-analysis/grid'),
    ('gap_kernel', '/uk/camera-traps/api/gap-analysis/kernel'),
    ('locations_with_status', '/uk/camera-traps/api/locations-with-status'),
    ('export_consensus', f'/uk/camera-traps/api/data-download?{D}&export_mode=consensus&filter_type=all'),
    ('export_human_ai', f'/uk/camera-traps/api/data-download?{D}&export_mode=human_ai&filter_type=all'),
    ('export_species_agg', f'/uk/camera-traps/api/data-download?{D}&export_mode=consensus&aggregation=daily'),
    ('page_dashboard', '/uk/camera-traps/dashboard'),
    ('page_contributors', '/uk/camera-traps/contributors'),
    ('page_institution_stats', '/uk/camera-traps/institution-stats'),
    ('page_expertise', '/uk/camera-traps/expertise'),
    ('page_species_dashboard', '/uk/camera-traps/analysis/species-dashboard'),
    ('page_species_detailed', '/uk/camera-traps/analysis/species-detailed'),
    ('page_ai_accuracy', '/uk/camera-traps/admin/ai/accuracy'),
    ('page_data_quality', '/uk/camera-traps/data-quality'),
    ('page_gallery', '/uk/camera-traps/gallery'),
]

# Noise that differs between two runs regardless of code: CSRF tokens, nonces,
# cache-busting query strings, "generated at" stamps.
NOISE = [
    (re.compile(r'name="csrf_token"[^>]*value="[^"]*"'), 'name="csrf_token"'),
    (re.compile(r'"csrf_token":\s*"[^"]*"'), '"csrf_token":""'),
    (re.compile(r'csrf-token" content="[^"]*"'), 'csrf-token"'),
    (re.compile(r'nonce="[^"]*"'), 'nonce=""'),
    (re.compile(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?'), '<ts>'),
]

TABLE_DUMPS = {
    'tbl_location_monthly_activity':
        'SELECT species_id, location_id, year, month, detection_count, trap_days '
        'FROM location_monthly_activity ORDER BY 1,2,3,4',
    'tbl_species_yearly_trends':
        'SELECT * FROM species_yearly_trends ORDER BY 1,2,3,4',
    'tbl_location_stats': 'SELECT * FROM location_stats ORDER BY 1',
    'tbl_observation_status':
        'SELECT status, COUNT(*) FROM observations GROUP BY 1 ORDER BY 1',
    'tbl_ai_was_correct':
        'SELECT was_correct, COUNT(*) FROM ai_predictions GROUP BY 1 ORDER BY 1',
}


def _assert_sandbox(engine):
    from sqlalchemy import text
    with engine.connect() as c:
        port, ddir = c.execute(text(
            "SELECT inet_server_port(), current_setting('data_directory')")).fetchone()
    if port != 5544 or 'ms_sandbox' not in ddir:
        sys.exit(f'REFUSING: CT database is not the sandbox (port {port}, {ddir})')
    print(f'sandbox OK: port {port}, {ddir}')


def _normalise(body, is_json):
    if is_json:
        try:
            return json.dumps(json.loads(body), sort_keys=True, ensure_ascii=False, indent=1)
        except ValueError:
            pass
    for rx, rep in NOISE:
        body = rx.sub(rep, body)
    return body


def snapshot(out_dir, rebuild):
    if 'CT_DATABASE_URL' not in os.environ:
        sys.exit('Set CT_DATABASE_URL to the sandbox explicitly (the .env one is PROD).')
    from sqlalchemy import text
    from app import create_app
    from app.extensions import db
    from app.models import User
    from app.camera_traps.database import get_ct_engine

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        _assert_sandbox(engine)

        if rebuild:
            from app.camera_traps.analytics_calculator import update_analytics_tables
            print('rebuilding analytics tables in the sandbox...')
            update_analytics_tables(force_run=True)
            from app.camera_traps.service_analytics import update_all_location_stats
            update_all_location_stats(force_run=True)
            # The main-DB connection idles through the ~15 min rebuild and the
            # tunnel drops it; start the URL pass on fresh connections.
            try:
                db.session.remove()
            except Exception:
                pass  # the dropped connection cannot even roll back
            db.engine.dispose()

        with engine.connect() as c:
            top = [r[0] for r in c.execute(text(
                """SELECT i.species_id FROM identifications i WHERE i.species_id > 0
                   GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 5""")).fetchall()]
            for name, sql in TABLE_DUMPS.items():
                try:
                    rows = c.execute(text(sql)).fetchall()
                    (out / f'{name}.txt').write_text(_normalise(
                        '\n'.join('\t'.join(map(str, r)) for r in rows), False),
                        encoding='utf-8')
                except Exception as e:  # table missing on this install
                    c.rollback()
                    (out / f'{name}.txt').write_text(f'ERROR {e}', encoding='utf-8')

        admin = (User.query.filter(User.roles.any(name='admin')).order_by(User.id).first())
        client = app.test_client()
        with client.session_transaction() as s:
            s['_user_id'] = str(admin.id)
            s['_fresh'] = True

        sp = ','.join(map(str, top))
        manifest = {}
        for name, url in URLS:
            url = (url.replace('{SP}', sp).replace('{SP1}', str(top[0]))
                   .replace('{SP2}', str(top[1])).replace('{SP5}', str(top[4])))
            resp = client.get(url)
            is_json = resp.mimetype == 'application/json'
            body = _normalise(resp.get_data(as_text=True), is_json)
            ext = 'json' if is_json else ('csv' if 'csv' in resp.mimetype else 'html')
            (out / f'{name}.{ext}').write_text(body, encoding='utf-8')
            manifest[name] = {'url': url, 'status': resp.status_code,
                              'sha': hashlib.sha256(body.encode()).hexdigest()[:12],
                              'bytes': len(body)}
            print(f'{resp.status_code} {name:28s} {len(body):>9} B')
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
        db.session.remove()


def diff(old_dir, new_dir):
    import difflib
    old, new = Path(old_dir), Path(new_dir)
    names = sorted({p.name for p in old.iterdir()} | {p.name for p in new.iterdir()})
    changed = 0
    for n in names:
        if n == 'manifest.json':
            continue
        a = (old / n).read_text(encoding='utf-8') if (old / n).exists() else '<missing>'
        b = (new / n).read_text(encoding='utf-8') if (new / n).exists() else '<missing>'
        if a == b:
            print(f'same     {n}')
            continue
        changed += 1
        d = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm='', n=0))
        print(f'CHANGED  {n}: {sum(1 for l in d if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))} lines')
        for line in d[:20]:
            print('    ' + line[:200])
    print(f'\n{changed} file(s) differ')
    return changed


if __name__ == '__main__':
    args = sys.argv[1:]
    if args[:1] == ['--diff']:
        sys.exit(1 if diff(args[1], args[2]) else 0)
    snapshot(args[0], '--rebuild' in args)
