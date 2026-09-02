# SPDX-License-Identifier: AGPL-3.0-only
"""Nightly per-institution CSV export of camera-trap occurrence data.

Why this exists
---------------
`full_backup.sh` dumps ct_db every night, but a `.sql.gz` only helps someone who
can restore it into a running biomon. This layer writes the same table the
data-export page hands out, one CSV per institution, so the data survives in a
form a person can open — and so each park effectively holds its own copy.

How it stays cheap
------------------
The exporter hashes the rendered CSV and compares it with the hash stored in the
destination's ``manifest.json``. Identical bytes mean nothing in the database
changed for that institution, and the file is not written at all. A quiet park
therefore costs one query per night and no disk churn — which matters on a
volume that already sits at 96%.

Reuse, not reimplementation
---------------------------
Rows come from ``app.camera_traps.data_export.get_ct_occurrence_data`` — the very
function behind ``/api/data-download``. Column order and content therefore cannot
drift from what the UI produces. The only thing decided here is which filters a
*backup* should use: whole history, every institution, maximum completeness.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from datetime import date

from sqlalchemy import text

from .storage import build_backends, slugify_folder

logger = logging.getLogger(__name__)

#: Filters that define "a complete backup" as opposed to a user's ad-hoc export.
#: ``human_ai`` keeps consensus rows, every competing identification of an
#: unresolved series, and AI-only series; ``all`` keeps non-animal records
#: (empty frames, people, vehicles) too. Nothing in ct_db is silently dropped.
BACKUP_EXPORT_MODE = 'human_ai'
BACKUP_FILTER_TYPE = 'all'
BACKUP_START_DATE = '1900-01-01'

FILE_STEM = 'ct_occurrence'


class InstitutionExport:
    """Result of handling one institution — what the CLI prints and tests assert."""

    __slots__ = ('institution_id', 'code', 'folder', 'filename',
                 'row_count', 'skipped_unchanged', 'locations', 'errors')

    def __init__(self, institution_id, code, folder):
        self.institution_id = institution_id
        self.code = code
        self.folder = folder
        self.filename = None
        self.row_count = 0
        self.skipped_unchanged = False
        self.locations = []
        self.errors = []

    def __repr__(self):
        state = 'unchanged' if self.skipped_unchanged else f'{self.row_count} rows'
        return f'<InstitutionExport {self.code} {state}>'


def list_ct_institutions(ct_engine, main_session):
    """Institutions that actually own camera-trap locations.

    The link table lives in ct_db while the institution names live in the main
    database, so this is deliberately two queries rather than a join. Parks with
    no locations are skipped: an empty folder on Google Drive only makes the
    backup harder to read.
    """
    with ct_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT li.institution_id
            FROM location_institutions li
            JOIN locations l ON l.id = li.location_id
        """)).fetchall()
    ct_ids = {r[0] for r in rows if r[0] is not None}
    if not ct_ids:
        return []

    from app.models import Institution
    institutions = (main_session.query(Institution)
                    .filter(Institution.id.in_(sorted(ct_ids)))
                    .all())
    known = {i.id for i in institutions}
    for missing in sorted(ct_ids - known):
        logger.warning('[ct-csv-backup] institution_id %s has CT locations but no '
                       'row in the main database — skipped', missing)
    return sorted(institutions, key=lambda i: (i.code or '', i.id))


def render_csv(rows):
    """Serialise occurrence rows to CSV bytes, or return None for an empty set.

    Header order follows the first row's keys, exactly as ``/api/data-download``
    does, so the backup file and a hand-made download are byte-comparable.
    """
    if not rows:
        return None
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()),
                            lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8')


def _folder_for(institution):
    """Directory name for an institution: English name, ASCII, no separators."""
    return slugify_folder(institution.name_en or institution.name_uk,
                          fallback=f'institution_{institution.id}')


def export_institution(institution, backends, keep, run_date, dry_run=False):
    """Render one institution's CSV and hand it to every backend.

    A backend that rejects the file is logged and the run continues: losing the
    cloud copy is bad, losing the local one because the cloud was unreachable
    would be worse.
    """
    from app.camera_traps.data_export import get_ct_occurrence_data

    code = institution.code or f'ID{institution.id}'
    folder = _folder_for(institution)
    result = InstitutionExport(institution.id, code, folder)

    filters = {
        'species_ids': [],
        'genus': None, 'family': None, 'order': None, 'class': None,
        'start_date': BACKUP_START_DATE,
        'end_date': run_date.isoformat(),
        'aggregation': 'none',
        'institution_code': code,
        'filter_type': BACKUP_FILTER_TYPE,
        'export_mode': BACKUP_EXPORT_MODE,
        'institution_ids': [institution.id],
        'qc_exclude': [],
    }
    rows = get_ct_occurrence_data(filters, limit=None)['data']
    result.row_count = len(rows)

    payload = render_csv(rows)
    if payload is None:
        logger.info('[ct-csv-backup] %s: no occurrence rows, nothing to export', code)
        return result

    digest = hashlib.sha256(payload).hexdigest()
    filename = f'{code}_{FILE_STEM}_{run_date.isoformat()}.csv'
    result.filename = filename
    pattern = f'{code}_{FILE_STEM}_*.csv'

    for backend in backends:
        manifest = backend.read_manifest(folder)
        if manifest.get('sha256') == digest:
            # Same bytes as last time = no change in the database for this park.
            result.skipped_unchanged = True
            logger.info('[ct-csv-backup] %s: unchanged since %s, skipping %s',
                        code, manifest.get('generated_at', '?'), backend.name)
            continue
        if dry_run:
            result.locations.append(f'{backend.name}:(dry-run) {folder}/{filename}')
            continue
        try:
            location = backend.put(folder, filename, payload)
            backend.write_manifest(folder, {
                'sha256': digest,
                'file': filename,
                'generated_at': run_date.isoformat(),
                'row_count': len(rows),
                'institution_code': code,
                'institution_id': institution.id,
                'export_mode': BACKUP_EXPORT_MODE,
                'filter_type': BACKUP_FILTER_TYPE,
            })
            removed = backend.rotate(folder, pattern, keep)
            result.locations.append(location)
            if removed:
                logger.info('[ct-csv-backup] %s: rotated out %s', code, ', '.join(removed))
        except Exception as exc:  # backend-specific failures must not abort the run
            result.errors.append(f'{backend.name}: {exc}')
            logger.error('[ct-csv-backup] %s: backend %s failed: %s',
                         code, backend.name, exc, exc_info=True)

    return result


def run_backup(config, keep=None, run_date=None, dry_run=False, only_codes=None):
    """Export every camera-trap institution. Returns the list of per-institution results.

    Args:
        config: the ``CT_CSV_BACKUP`` dict from the app config.
        keep: override for how many versions of each file to retain.
        run_date: override for the date stamped into filenames (tests).
        dry_run: render and hash, but write nothing.
        only_codes: restrict the run to these institution codes.
    """
    from app.camera_traps.database import get_ct_engine
    from app.extensions import db

    run_date = run_date or date.today()
    keep = keep if keep is not None else config.get('KEEP_VERSIONS', 2)
    backends = build_backends(config.get('STORAGES'))
    if not backends:
        raise RuntimeError('CT_CSV_BACKUP: no storage backend is enabled')

    institutions = list_ct_institutions(get_ct_engine(), db.session)
    if only_codes:
        wanted = {c.strip().upper() for c in only_codes}
        institutions = [i for i in institutions if (i.code or '').upper() in wanted]

    results = []
    for institution in institutions:
        try:
            results.append(export_institution(
                institution, backends, keep, run_date, dry_run=dry_run))
        except Exception as exc:
            # One park's bad data must not cost every other park its backup.
            failed = InstitutionExport(institution.id, institution.code or '?',
                                       _folder_for(institution))
            failed.errors.append(str(exc))
            results.append(failed)
            logger.error('[ct-csv-backup] institution %s failed: %s',
                         institution.code, exc, exc_info=True)
    return results
