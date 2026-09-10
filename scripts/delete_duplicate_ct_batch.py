"""Delete an upload batch whose photos duplicate another batch at the same location.

Why this exists
---------------
`process_single_photo` treats (location, captured_at, original_filename) as the
identity of a photo. A card re-uploaded under different filenames — padded vs
unpadded, `IMG_0006.JPG` vs `IMG_6.JPG` — is therefore not a duplicate by that
key, and the second upload builds a complete parallel set of series for capture
events that are already on the site. Drevlianskyi NR 0403 has one:
`02847c73` (2026-07-08) duplicates `cca991a1` (2026-06-29), verified against
the park's own card.

Only the batch's invisible half shows up in the broken-photo diagnostic, so
deleting that alone leaves the visible duplicates behind, silently
double-counting capture events in every statistic that counts series. This
script removes the batch as a unit.

Safety
------
The analysis is redone here rather than trusted, and every check can only
refuse:

  * the batch must belong to exactly one location;
  * for every photo in it, OTHER batches at that location must hold at least as
    many photos at the same `captured_at` — that is what "duplicate" means
    operationally, and it is checked per photo, not in aggregate;
  * those covering photos must have a real file on disk (a 0-byte twin does
    not cover anything), unless they are archived, where a missing file is
    expected and the row is the record;
  * no photo in the batch may be a favourite;
  * observations are deleted only when the batch's photos are ALL of their
    photos; a series fed by two batches is left alone and reported.

If any check fails the script stops and deletes nothing.

Everything removed is written to a JSON backup first: photo rows, series,
identifications and AI predictions, enough to reconstruct them if the call
turns out to be wrong.

Usage
-----
    venv/bin/python -m scripts.delete_duplicate_ct_batch --batch 02847c73-...
    venv/bin/python -m scripts.delete_duplicate_ct_batch --batch 02847c73-... --apply
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.camera_traps.database import get_ct_engine  # noqa: E402


def thumbnail_is_usable(row, thumb_dir):
    """True if this row can stand in for another photo of the same instant.

    An archived row legitimately has no file — archiving deletes it on purpose
    — and the row itself is the surviving record, so it still counts. Anything
    else must have a non-empty thumbnail: a 0-byte twin covers nothing.
    """
    if row.status == 'archived':
        return True
    try:
        return os.path.getsize(os.path.join(thumb_dir, row.system_filename)) > 0
    except OSError:
        return False


def find_coverage_gaps(victims, others, usable):
    """Capture instants where `others` cannot account for `victims`.

    Returns [(instant, needed, available)] — empty means every photo of the
    batch has a usable counterpart at the same instant elsewhere, which is
    what makes the batch a duplicate rather than a loss. Compared per instant,
    never in aggregate: a surplus at one second must not excuse a shortfall at
    another.
    """
    cover = collections.Counter(r.captured_at for r in others if usable(r))
    need = collections.Counter(v.captured_at for v in victims)
    return sorted(((t, n, cover.get(t, 0)) for t, n in need.items()
                   if cover.get(t, 0) < n), key=lambda g: g[0])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--batch', required=True, help='upload_batch id to remove')
    ap.add_argument('--apply', action='store_true',
                    help='actually delete (default: report only)')
    ap.add_argument('--backup', help='where to write the JSON backup '
                                     '(default: alongside the CWD)')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        config = app.config['CAMERA_TRAP_CONFIG']
        thumb_dir = os.path.join(config['UPLOAD_PATH'], 'pending_photos', 'thumbnails')
        engine = get_ct_engine()

        with engine.connect() as conn:
            batch = conn.execute(text("""
                SELECT id, location_id, status, total_files, created_at
                  FROM upload_batches WHERE id = :b
            """), {'b': args.batch}).first()
            if batch is None:
                print('no such batch: %s' % args.batch, file=sys.stderr)
                return 2

            victims = conn.execute(text("""
                SELECT p.id, p.original_filename, p.system_filename, p.captured_at,
                       p.observation_id, p.status, p.is_favorite,
                       o.location_id, o.status AS obs_status
                  FROM photos p
                  JOIN observations o ON o.id = p.observation_id
                 WHERE p.upload_batch_id = :b
            """), {'b': args.batch}).fetchall()

        if not victims:
            print('batch %s has no photos with an observation — nothing to do'
                  % args.batch)
            return 0

        locs = {v.location_id for v in victims}
        print('batch          : %s' % batch.id)
        print('declared files : %s' % batch.total_files)
        print('uploaded       : %s' % batch.created_at)
        print('photo rows     : %d' % len(victims))
        print('locations       : %s' % sorted(locs))
        if len(locs) != 1:
            print('REFUSED: the batch spans several locations', file=sys.stderr)
            return 3
        location_id = locs.pop()

        with engine.connect() as conn:
            location = conn.execute(text(
                'SELECT id, name, latitude, longitude, photo_count '
                '  FROM locations WHERE id = :i'), {'i': location_id}).first()
            others = conn.execute(text("""
                SELECT p.id, p.system_filename, p.captured_at, p.status,
                       p.upload_batch_id
                  FROM photos p
                  JOIN observations o ON o.id = p.observation_id
                 WHERE o.location_id = :loc
                   AND (p.upload_batch_id IS NULL OR p.upload_batch_id <> :b)
            """), {'loc': location_id, 'b': args.batch}).fetchall()
        print('location       : %s (%s, %s)'
              % (location.name, location.latitude, location.longitude))
        print('photos at this location from other batches: %d' % len(others))

        # ── coverage: per capture instant, do other batches hold as many? ────
        def usable(row):
            return thumbnail_is_usable(row, thumb_dir)

        unusable = sum(1 for r in others if not usable(r))
        need = collections.Counter(v.captured_at for v in victims)
        gaps = find_coverage_gaps(victims, others, usable)

        print()
        print('capture instants in the batch                  : %d' % len(need))
        print('other batches unusable (0-byte, not archived)  : %d' % unusable)
        print('instants NOT covered by other batches          : %d' % len(gaps))
        for t, n, c in gaps[:20]:
            print('   %s  batch=%d  others=%d' % (t, n, c))

        favourites = [v.id for v in victims if v.is_favorite]
        print('favourites in the batch                        : %d' % len(favourites))

        # ── series: only those wholly owned by this batch may go ─────────────
        with engine.connect() as conn:
            totals = dict(conn.execute(text("""
                SELECT observation_id, count(*) FROM photos
                 WHERE observation_id = ANY(:ids) GROUP BY observation_id
            """), {'ids': sorted({v.observation_id for v in victims})}).fetchall())
        mine = collections.Counter(v.observation_id for v in victims)
        whole = [o for o in mine if mine[o] == totals[o]]
        shared = [o for o in mine if mine[o] < totals[o]]
        print('series touched                                 : %d' % len(mine))
        print('  wholly this batch (will be deleted)          : %d' % len(whole))
        print('  shared with another batch (left in place)    : %d' % len(shared))
        for o in shared[:10]:
            print('     series %s: %d of %d rows are this batch\'s'
                  % (o, mine[o], totals[o]))

        photo_ids = [v.id for v in victims]
        with engine.connect() as conn:
            n_ident = conn.execute(text(
                'SELECT count(*) FROM identifications WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).scalar()
            n_ai = conn.execute(text(
                'SELECT count(*) FROM ai_predictions WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).scalar()
            n_ai_obs = conn.execute(text(
                'SELECT count(*) FROM ai_predictions WHERE observation_id = ANY(:ids)'),
                {'ids': whole}).scalar()
        print('identifications attached                       : %d' % n_ident)
        print('AI predictions (photo-level)                   : %d' % n_ai)
        print('AI predictions (series-level, whole series)     : %d' % n_ai_obs)

        if gaps or favourites:
            print()
            print('REFUSED — the batch is not a pure duplicate '
                  '(uncovered instants and/or favourites).', file=sys.stderr)
            return 4

        print()
        print('VERDICT: every photo of this batch is covered by another batch '
              'at the same location and the same instant.')

        if not args.apply:
            print('\nreport only. Rerun with --apply to delete.')
            return 0

        # ── backup ───────────────────────────────────────────────────────────
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        backup_path = args.backup or ('ct_batch_%s_%s.json'
                                      % (args.batch[:8], stamp))
        with engine.connect() as conn:
            idents = [dict(r._mapping) for r in conn.execute(text("""
                SELECT * FROM identifications WHERE photo_id = ANY(:ids)
            """), {'ids': photo_ids})]
            preds = [dict(r._mapping) for r in conn.execute(text("""
                SELECT * FROM ai_predictions
                 WHERE photo_id = ANY(:ids) OR observation_id = ANY(:obs)
            """), {'ids': photo_ids, 'obs': whole})]
            behaviours = [dict(r._mapping) for r in conn.execute(text("""
                SELECT * FROM identification_behaviors
                 WHERE identification_id IN (
                       SELECT id FROM identifications WHERE photo_id = ANY(:ids))
            """), {'ids': photo_ids})]
            obs_rows = [dict(r._mapping) for r in conn.execute(text("""
                SELECT * FROM observations WHERE id = ANY(:ids)
            """), {'ids': whole})]
            photo_rows = [dict(r._mapping) for r in conn.execute(text("""
                SELECT * FROM photos WHERE id = ANY(:ids)
            """), {'ids': photo_ids})]
        payload = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'batch': dict(batch._mapping),
            'location': dict(location._mapping),
            'photos': photo_rows,
            'observations': obs_rows,
            'identifications': idents,
            'identification_behaviors': behaviours,
            'ai_predictions': preds,
        }
        with open(backup_path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
        print('backup written: %s (%d bytes)'
              % (backup_path, os.path.getsize(backup_path)))

        # ── delete ───────────────────────────────────────────────────────────
        with engine.begin() as conn:
            d_beh = conn.execute(text("""
                DELETE FROM identification_behaviors
                 WHERE identification_id IN (
                       SELECT id FROM identifications WHERE photo_id = ANY(:ids))
            """), {'ids': photo_ids}).rowcount
            d_ident = conn.execute(text(
                'DELETE FROM identifications WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).rowcount
            d_ai = conn.execute(text(
                'DELETE FROM ai_predictions WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).rowcount
            d_photo = conn.execute(text(
                'DELETE FROM photos WHERE id = ANY(:ids)'),
                {'ids': photo_ids}).rowcount
            d_ai_obs = conn.execute(text(
                'DELETE FROM ai_predictions WHERE observation_id = ANY(:ids)'),
                {'ids': whole}).rowcount
            d_obs = conn.execute(text(
                'DELETE FROM observations WHERE id = ANY(:ids)'),
                {'ids': whole}).rowcount
            recount = conn.execute(text("""
                UPDATE observations o
                   SET photo_count = (SELECT count(*) FROM photos p
                                       WHERE p.observation_id = o.id)
                 WHERE o.id = ANY(:ids)
            """), {'ids': shared}).rowcount
            # locations.photo_count is a cached total; keep it honest.
            newcount = conn.execute(text("""
                UPDATE locations l
                   SET photo_count = (
                       SELECT count(*) FROM photos p
                         JOIN observations o ON o.id = p.observation_id
                        WHERE o.location_id = l.id)
                 WHERE l.id = :i
             RETURNING photo_count
            """), {'i': location_id}).scalar()
            # Leave the batch row as provenance, but say what happened to it.
            conn.execute(text("""
                UPDATE upload_batches
                   SET error_message = COALESCE(error_message || ' | ', '')
                       || :note
                 WHERE id = :b
            """), {'b': args.batch,
                   'note': ('duplicate of another batch at this location; '
                            '%d photos and %d series deleted %s'
                            % (d_photo, d_obs, stamp))})

        # Remove the files of the deleted rows (only the batch's own).
        removed = 0
        for v in victims:
            path = os.path.join(thumb_dir, v.system_filename)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
            except OSError as exc:
                print('  could not remove %s: %s' % (path, exc))

        print()
        print('identification_behaviors : %d' % d_beh)
        print('identifications          : %d' % d_ident)
        print('ai_predictions (photo)   : %d' % d_ai)
        print('ai_predictions (series)  : %d' % d_ai_obs)
        print('photos                   : %d' % d_photo)
        print('observations             : %d' % d_obs)
        print('series recounted         : %d' % recount)
        print('files removed from disk  : %d' % removed)
        print('locations.photo_count    : %s -> %s'
              % (location.photo_count, newcount))
        print('\nRecalculate CT analytics for this location afterwards.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
