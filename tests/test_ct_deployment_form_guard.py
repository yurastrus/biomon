"""
Deployment detail panel must collapse when switching to another location,
and guard unsaved edits before collapsing.

Template-contract regression guard on manage_deployments.html.
"""
import pathlib

CT_TPL = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'camera_traps' / 'templates'
DEP = (CT_TPL / 'manage_deployments.html').read_text(encoding='utf-8')


def test_dirty_flag_and_helpers_exist():
    assert 'formDirty' in DEP, 'немає прапорця незбережених змін'
    assert 'function confirmLeaveForm(' in DEP, 'немає guard-функції'
    assert 'function closeEditPanel(' in DEP, 'немає функції згортання панелі'


def test_selectlocation_guards_and_collapses():
    # selectLocation must call the guard and collapse the panel on a real switch.
    idx = DEP.index('function selectLocation(')
    body = DEP[idx:idx + 700]
    assert 'confirmLeaveForm()' in body, 'selectLocation не перевіряє незбережені зміни'
    assert 'closeEditPanel()' in body, 'selectLocation не згортає деталі при зміні локації'


def test_edits_mark_form_dirty():
    assert "on('input change'" in DEP, 'зміни в полях не позначають форму брудною'
    # QC tri-state clicks also count as edits.
    assert DEP.count('formDirty = true') >= 2, 'не всі види редагування позначають форму брудною'


def test_save_resets_dirty_flag():
    assert 'formDirty = false' in DEP, 'прапорець не скидається після збереження/відкриття'


def test_row_and_new_button_also_guarded():
    # Switching deployment rows and starting a new one guard unsaved edits too.
    assert DEP.count('confirmLeaveForm()') >= 3, \
        'guard не покриває перемикання рядка/створення нового деплойменту'
