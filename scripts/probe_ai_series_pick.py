"""Compare the series-level AI pick, old vs new code, on the real DB (read-only).

Loads ai_runner.py / data_export.py either as installed (old) or from
``--new-dir`` (a folder holding the new copies), then times:
  - the /identify hint for one series (and prints what it suggests),
  - the AI filter options (species list with counts) as admin,
  - the /identify queue with an AI species filter (``next_observation``),
  - the CSV-backup export (human_ai, all rows) of one institution.
Nothing is written: every call is a read.

Usage (on the server, from /var/www/biomon):
    venv/bin/python -m scripts.probe_ai_series_pick <username> <obs_id> <species_id> <institution_code> [--new-dir DIR]
"""
import importlib.util
import sys
import time


def _load_new(new_dir):
    """Swap app.camera_traps.ai_runner / data_export for the copies in new_dir."""
    import app.camera_traps as pkg
    for name in ('ai_runner', 'data_export'):
        full = f'app.camera_traps.{name}'
        spec = importlib.util.spec_from_file_location(full, f'{new_dir}/{name}.py',
                                                      submodule_search_locations=None)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = 'app.camera_traps'
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, name, mod)


def _timed(label, fn, runs=3):
    times, out = [], None
    for _ in range(runs):
        t0 = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t0)
    times.sort()
    print(f'  {label:28} median {times[len(times)//2]*1000:7.0f} ms  min {times[0]*1000:7.0f}')
    return out


def main():
    args = [a for a in sys.argv[1:]]
    new_dir = None
    if '--new-dir' in args:
        i = args.index('--new-dir')
        new_dir = args[i + 1]
        del args[i:i + 2]
    username, obs_id, species_id, inst_code = args[0], int(args[1]), int(args[2]), args[3]

    from app import create_app
    app = create_app()
    with app.app_context():
        if new_dir:
            _load_new(new_dir)
        print('NEW code' if new_dir else 'OLD (installed) code')
        from flask import g
        from flask_login import login_user
        from app.models import User, Institution
        from app.camera_traps import routes
        from app.camera_traps.ai_runner import get_ai_filter_options, get_observation_ai_prediction
        from app.camera_traps.data_export import get_ct_occurrence_data
        from app.camera_traps.database import close_ct_session

        pred = _timed('hint', lambda: get_observation_ai_prediction(obs_id))
        print(f'    hint for {obs_id}: species {pred and pred["species_id"]} '
              f'({pred and pred["species_label"]}), score {pred and pred["score"]}')

        opts = _timed('filter options (admin)', lambda: get_ai_filter_options(is_admin=True))
        hit = [o['text'] for o in opts['items'] if o['id'] == species_id]
        print(f'    species {species_id} option: {hit}; groups {opts["group_counts"]}')
        close_ct_session()

        user = User.query.filter_by(username=username).first()

        def queue():
            url = f'/uk/camera-traps/api/next-observation-for-identification?ai_species_id={species_id}'
            with app.test_request_context(url):
                g.lang_code = 'uk'
                login_user(user)
                return routes.next_observation_for_identification.__wrapped__.__wrapped__('uk')
        _timed(f'queue, AI filter {species_id}', queue)

        inst = Institution.query.filter_by(code=inst_code).first()
        filters = {
            'species_ids': [], 'genus': None, 'family': None, 'order': None, 'class': None,
            'start_date': '1900-01-01', 'end_date': '2100-01-01', 'aggregation': 'none',
            'institution_code': inst_code, 'filter_type': 'all', 'export_mode': 'human_ai',
            'institution_ids': [inst.id], 'qc_exclude': [],
        }
        rows = _timed(f'export {inst_code} human_ai', lambda: get_ct_occurrence_data(filters)['data'], runs=1)
        ai_rows = [r for r in rows if str(r.get('identificationVerificationStatus', '')).lower().startswith('ai')
                   or r.get('ai_confidence') is not None]
        print(f'    export rows {len(rows)}')
        close_ct_session()


if __name__ == '__main__':
    main()
