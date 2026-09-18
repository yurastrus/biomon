"""Create the camera timestamp profile table used by the video upload page.

`create_all` does not add tables to an existing database on its own here, so the
one table this feature needs is created explicitly. Idempotent: running it twice
is a no-op.

    venv/Scripts/python -m scripts.init_video_upload    # Windows / dev
    venv/bin/python -m scripts.init_video_upload        # Linux / production
"""

from sqlalchemy import inspect

from app import create_app
from app.camera_traps.database import CTBase, get_ct_engine
from app.camera_traps.models import CameraTimestampProfile


def main():
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        table = CameraTimestampProfile.__table__

        if inspect(engine).has_table(table.name):
            print(f'{table.name}: already present, nothing to do')
            return

        CTBase.metadata.create_all(engine, tables=[table])
        print(f'{table.name}: created')


if __name__ == '__main__':
    main()
