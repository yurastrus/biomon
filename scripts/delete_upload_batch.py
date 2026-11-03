"""Delete one CT upload batch completely: files on disk and every database row.

Written 2026-11-03 to remove frames a user cut from video with her own script
(batch a63eef06, Єланецький степ). Refuses a batch whose series also hold photos
from other batches, or that has favourites. Dry run by default; --apply deletes.
Run on the server, where the photo files are:

    venv/bin/python -m scripts.delete_upload_batch <batch_id>            # dry run
    venv/bin/python -m scripts.delete_upload_batch <batch_id> --apply
"""
import os
import sys

from sqlalchemy import text


def main(batch_id, apply):
    from app import create_app
    from app.camera_traps.database import get_ct_engine

    app = create_app()
    with app.app_context():
        upload_path = app.config['CAMERA_TRAP_CONFIG']['UPLOAD_PATH']
        engine = get_ct_engine()
        with engine.begin() as c:
            batch = c.execute(text('SELECT id, location_id, uploaded_by_id, status FROM upload_batches '
                                   'WHERE id = :b'), {'b': batch_id}).first()
            if batch is None:
                sys.exit(f'no batch {batch_id}')
            photos = c.execute(text('SELECT id, system_filename, observation_id, is_favorite '
                                    'FROM photos WHERE upload_batch_id = :b'), {'b': batch_id}).fetchall()
            photo_ids = [p.id for p in photos]
            obs_ids = sorted({p.observation_id for p in photos if p.observation_id})
            foreign = c.execute(text('SELECT count(*) FROM photos WHERE observation_id = ANY(:o) '
                                     'AND upload_batch_id IS DISTINCT FROM :b'),
                                {'o': obs_ids, 'b': batch_id}).scalar()
            if foreign:
                sys.exit(f'refusing: {foreign} photos of other batches share these series')
            if any(p.is_favorite for p in photos):
                sys.exit('refusing: the batch holds favourites')

            files = []
            for p in photos:
                for root, _dirs, names in os.walk(upload_path):
                    if p.system_filename in names:
                        files.append(os.path.join(root, p.system_filename))
            counts = {
                'ai_predictions': c.execute(text('SELECT count(*) FROM ai_predictions WHERE photo_id = ANY(:p) '
                                                 'OR observation_id = ANY(:o)'),
                                            {'p': photo_ids, 'o': obs_ids}).scalar(),
                'identifications': c.execute(text('SELECT count(*) FROM identifications WHERE photo_id = ANY(:p)'),
                                             {'p': photo_ids}).scalar(),
            }
            print(f'batch {batch_id} loc={batch.location_id} user={batch.uploaded_by_id} status={batch.status}')
            print(f'photos={len(photo_ids)} series={obs_ids} files={len(files)} {counts}')
            for f in files:
                print('  ', f)
            if not apply:
                print('dry run, nothing deleted')
                return

            c.execute(text('DELETE FROM ai_predictions WHERE photo_id = ANY(:p) OR observation_id = ANY(:o)'),
                      {'p': photo_ids, 'o': obs_ids})
            c.execute(text('DELETE FROM identifications WHERE photo_id = ANY(:p)'), {'p': photo_ids})
            c.execute(text('DELETE FROM photos WHERE id = ANY(:p)'), {'p': photo_ids})
            c.execute(text('DELETE FROM observations WHERE id = ANY(:o)'), {'o': obs_ids})
            c.execute(text('DELETE FROM upload_batches WHERE id = :b'), {'b': batch_id})
        # Files only after the rows are gone for good.
        for f in files:
            os.remove(f)
        print(f'deleted: {len(photo_ids)} photos, {len(obs_ids)} series, batch, {len(files)} files')


if __name__ == '__main__':
    main(sys.argv[1], '--apply' in sys.argv[2:])
