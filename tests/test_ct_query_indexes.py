"""
Idea 2 (#17): індекси photos.status та identifications.user_id.

На проді обидва індекси вже існували — тут гарантуємо, що вони
ЗАДЕКЛАРОВАНІ в моделі (shared-ct), тож create_all на нових/dev/тест
інсталяціях їх теж створює, і метадані відповідають реальній БД.

Запуск:
    venv/Scripts/python -m pytest tests/test_ct_query_indexes.py -v
"""
from sqlalchemy import create_engine, inspect


def test_index_declared_in_photo_model():
    from app.camera_traps.models import Photo
    names = {i.name for i in Photo.__table__.indexes}
    assert 'idx_photos_status' in names


def test_index_declared_in_identification_model():
    from app.camera_traps.models import Identification
    names = {i.name for i in Identification.__table__.indexes}
    assert 'idx_identifications_user_id' in names


def test_create_all_materializes_indexes_on_fresh_db():
    """create_all на чистій SQLite фізично створює обидва індекси
    (саме цей сценарій раніше їх не давав — вони були лише на проді)."""
    from app.camera_traps.models import Photo, Identification

    engine = create_engine('sqlite:///:memory:')
    # Лише дві потрібні таблиці (без PG-only типів у решті моделей)
    Photo.__table__.create(bind=engine, checkfirst=True)
    Identification.__table__.create(bind=engine, checkfirst=True)

    insp = inspect(engine)
    photo_idx = {ix['name'] for ix in insp.get_indexes('photos')}
    ident_idx = {ix['name'] for ix in insp.get_indexes('identifications')}
    engine.dispose()

    assert 'idx_photos_status' in photo_idx
    assert 'idx_identifications_user_id' in ident_idx


def test_index_columns_are_correct():
    """Індекси на правильних колонках (status / user_id)."""
    from app.camera_traps.models import Photo, Identification
    photo_ix = {i.name: [c.name for c in i.columns]
                for i in Photo.__table__.indexes}
    ident_ix = {i.name: [c.name for c in i.columns]
                for i in Identification.__table__.indexes}
    assert photo_ix['idx_photos_status'] == ['status']
    assert ident_ix['idx_identifications_user_id'] == ['user_id']
