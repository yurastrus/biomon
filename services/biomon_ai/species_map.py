"""Мапінг сирих labels від DeepFaune → biomon Species.id.

ДОВІДКА:
    DeepFaune класифікатор повертає одне з ~38 ім'ям виду англійською
    (`'roe deer'`, `'fox'`, `'wild boar'`, ...) або одне з 8 під-класів
    птахів (`'bird-corvid'`, `'bird-raptor'`, ...) або одне зі спецкласів
    `'empty'` / `'human'` / `'vehicle'` / `'undefined'`.

ПРАВИЛА:
    - Якщо вид є у вашому Species (id > 0) — мапаємо на нього.
    - Якщо вид не водиться в Карпатах / поки нема в БД — None (NULL у БД).
      Сирий label все одно зберігається в `ai_predictions.prediction_label`,
      тому при додаванні виду в Species потім можна back-fill через UPDATE.
    - `empty` / `human` / `vehicle` — мапаємо на спецкласи -1 / -5 / -3.
    - Підкласи птахів — None (як домовились: птахи не пріоритет).
    - `undefined` (DeepFaune видає коли score < threshold) — None.

ОНОВЛЕННЯ:
    Якщо в Species додасться новий вид (наприклад, видра/Lutra lutra) —
    допишіть рядок у DEEPFAUNE_TO_SPECIES_ID нижче, і нові прогнози
    почнуть мапитись. Старі прогнози можна back-fill одноразовим SQL:
        UPDATE ai_predictions
        SET prediction_species_id = <species_id>
        WHERE prediction_label = '<deepfaune_label>'
          AND prediction_species_id IS NULL;
"""

from typing import Optional


# DeepFaune label (англійський, як видає класифікатор) → Species.id в ct_db.
# None означає "сирий label зберігаємо, але мапінга на біомон-вид нема".
DEEPFAUNE_TO_SPECIES_ID: dict[str, Optional[int]] = {
    # ── Ссавці які є в Species ─────────────────────────────────────
    'roe deer':       4,    # Capreolus capreolus — Козуля
    'red deer':       5,    # Cervus elaphus — Олень благородний
    'wild boar':      3,    # Sus scrofa — Кабан
    'fox':            10,   # Vulpes vulpes — Лисиця
    'wolf':           9,    # Canis lupus — Вовк
    'lynx':           37,   # Lynx lynx — Рись
    'bear':           36,   # Ursus arctos — Ведмідь бурий
    'badger':         11,   # Meles meles — Борсук
    'moose':          6,    # Alces alces — Лось
    'bison':          38,   # Bos bonasus — Зубр
    'squirrel':       2,    # Sciurus vulgaris — Білка
    'raccoon dog':    26,   # Nyctereutes procyonoides — Єнотовидна собака
    'dog':            8,    # Canis familiaris — Собака свійський
    'cat':            7,    # Felis silvestris — Кіт лісовий
                            # (DeepFaune не розрізняє домашнього/лісового,
                            # для Карпат майже завжди це лісовий)

    # ── Узагальнені класи DeepFaune → найближчий вид у Species ─────
    'lagomorph':      1,    # Lepus europaeus (у нас лише заєць сірий)
    'micromammal':   -8,    # → "Дрібний гризун" (спецклас в Species)

    # ── Спецкласи DeepFaune ────────────────────────────────────────
    'empty':         -1,    # Пусто
    'human':         -5,    # Людина
    'vehicle':       -3,    # Автомобіль

    # ── Види яких немає в Carpathians / в Species ──────────────────
    # Лишаємо як NULL — сирий label все одно зберігається в prediction_label
    'ibex':              None,  # альпійський козел, високогір'я
    'beaver':            None,  # бобер — є в Україні, але не в Species
    'golden jackal':     None,  # шакал золотистий — рідкісний
    'chamois':           None,  # сарна, високогір'я
    'goat':              None,  # домашня коза
    'fallow deer':       None,  # лань — не в Карпатах
    'equid':             None,  # рід коней — рідко
    'genet':             None,  # генета — не в Карпатах
    'wolverine':         None,  # росомаха — не в Карпатах
    'hedgehog':          None,  # їжак — є в Україні, але не в Species
    'otter':             None,  # видра — є в Україні, але не в Species
    'marmot':            None,  # бабак — високогір'я
    'mouflon':           None,  # муфлон — рідко
    'sheep':             None,  # домашня вівця
    'mustelid':          None,  # узагальнений клас куньих (у нас 2 види
                                # підкатегорії: куниця, тхір, не визначимо)
    'porcupine':         None,
    'nutria':            None,
    'muskrat':           None,
    'raccoon':           None,
    'reindeer':          None,
    'cow':               None,

    # ── Птахи: усі підкласи DeepFaune ігноруємо (за домовленістю) ──
    'bird':              None,
    'bird-anseriform':   None,
    'bird-otherbird':    None,
    'bird-columbiform':  None,
    'bird-corvid':       None,
    'bird-galliform':    None,
    'bird-passerine':    None,
    'bird-piciform':     None,
    'bird-raptor':       None,

    # ── DeepFaune видає коли score < threshold ─────────────────────
    'undefined':         None,
}


def map_deepfaune_label(label: Optional[str]) -> Optional[int]:
    """Повертає Species.id або None.

    None означає одне з двох:
      • для відомих labels — вид не мапається (відсутній у Species).
      • для невідомих/невизначених labels — захист від нових версій
        DeepFaune, які можуть видати label, якого ми не передбачили.
    У обох випадках сирий label все одно зберігається в БД, тож
    нічого не втрачаємо.
    """
    if not label:
        return None
    return DEEPFAUNE_TO_SPECIES_ID.get(label.strip().lower())
