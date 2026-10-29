"""Ukrainian catalogs must not carry fuzzy guesses.

msgids are already Ukrainian, so a uk msgstr is only ever empty (fall back to
the msgid) or deliberate. `pybabel update` fills new msgids with a fuzzy guess
from a similar old string, and `compile -f` (our standard) ships it: "Додати
вид" rendered as "стан невідомий" on the CT species list (2026-10-29).
"""
import glob

import pytest
from babel.messages.pofile import read_po

UK_CATALOGS = sorted(glob.glob('translations/uk/LC_MESSAGES/*.po')
                     + glob.glob('app/*/translations/uk/LC_MESSAGES/*.po'))


@pytest.mark.parametrize('path', UK_CATALOGS)
def test_no_fuzzy_guess_in_uk_catalog(path):
    with open(path, 'rb') as f:
        cat = read_po(f)
    guesses = [(m.id, m.string) for m in cat if m.id and m.fuzzy and m.string]
    assert not guesses, f'{path}: clear these fuzzy msgstr: {guesses}'
