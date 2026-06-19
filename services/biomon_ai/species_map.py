# SPDX-License-Identifier: AGPL-3.0-only
"""Mapping of raw DeepFaune labels → biomon Species.id.

REFERENCE:
    The DeepFaune classifier returns one of ~38 English species names
    (`'roe deer'`, `'fox'`, `'wild boar'`, ...), or one of 8 bird
    sub-classes (`'bird corvid'`, `'bird raptor'`, ... — SPACE, not a
    hyphen; see predictTools.py:72), or one of the special classes
    `'empty'` / `'human'` / `'vehicle'` / `'undefined'`.
    When DeepFaune decides it is a bird but the sub-classifier is below
    threshold, it returns `'bird undefined'` (predictTools.py:73).

RULES:
    - If the species exists in your Species (id > 0) — map to it.
    - If the species is not found in the Carpathians / not yet in the DB
      — None (NULL in the DB). The raw label is still stored in
      `ai_predictions.prediction_label`, so once the species is added to
      Species you can back-fill via UPDATE.
    - `empty` / `human` / `vehicle` — map to special classes -1 / -5 / -3.
    - Bird sub-classes — map to special Species (-9, -12..-18).
    - `undefined` (DeepFaune returns it when score < threshold) — None.

UPDATING:
    If a new species is added to Species (e.g. otter/Lutra lutra) — add a
    row to DEEPFAUNE_TO_SPECIES_ID below and new predictions will start
    mapping. Old predictions can be back-filled with a one-off SQL:
        UPDATE ai_predictions
        SET prediction_species_id = <species_id>
        WHERE prediction_label = '<deepfaune_label>'
          AND prediction_species_id IS NULL;
"""

from typing import Optional


# DeepFaune label (English, as emitted by the classifier) → Species.id in ct_db.
# None means "store the raw label, but there is no mapping to a biomon species".
DEEPFAUNE_TO_SPECIES_ID: dict[str, Optional[int]] = {
    # ── Mammals present in Species ─────────────────────────────────
    'roe deer':       4,    # Capreolus capreolus — roe deer
    'red deer':       5,    # Cervus elaphus — red deer
    'wild boar':      3,    # Sus scrofa — wild boar
    'fox':            10,   # Vulpes vulpes — red fox
    'wolf':           9,    # Canis lupus — wolf
    'lynx':           37,   # Lynx lynx — lynx
    'bear':           36,   # Ursus arctos — brown bear
    'badger':         11,   # Meles meles — badger
    'moose':          6,    # Alces alces — moose
    'bison':          38,   # Bos bonasus — bison
    'squirrel':       2,    # Sciurus vulgaris — squirrel
    'raccoon dog':    26,   # Nyctereutes procyonoides — raccoon dog
    'dog':            8,    # Canis familiaris — domestic dog
    'cat':            7,    # Felis silvestris — wildcat
                            # (DeepFaune does not distinguish domestic/wild,
                            # in the Carpathians it is almost always wild)

    # ── Generalized DeepFaune classes → nearest species in Species ─
    'lagomorph':      1,    # Lepus europaeus (we only have the brown hare)
    'micromammal':   -8,    # → "small rodent" (special class in Species)

    # ── DeepFaune special classes ──────────────────────────────────
    'empty':         -1,    # empty
    'human':         -5,    # human
    'vehicle':       -3,    # vehicle

    # ── Species not found in the Carpathians / in Species ──────────
    # Left as NULL — the raw label is still stored in prediction_label
    'ibex':              None,  # alpine ibex, highlands
    'beaver':            None,  # beaver — present in Ukraine, but not in Species
    'golden jackal':     None,  # golden jackal — rare
    'chamois':           None,  # chamois, highlands
    'goat':              None,  # domestic goat
    'fallow deer':       None,  # fallow deer — not in the Carpathians
    'equid':             None,  # horse genus — rare
    'genet':             None,  # genet — not in the Carpathians
    'wolverine':         None,  # wolverine — not in the Carpathians
    'hedgehog':          None,  # hedgehog — present in Ukraine, but not in Species
    'otter':             None,  # otter — present in Ukraine, but not in Species
    'marmot':            None,  # marmot — highlands
    'mouflon':           None,  # mouflon — rare
    'sheep':             None,  # domestic sheep
    'mustelid':          None,  # generalized mustelid class (we have 2 species
                                # in this subcategory: marten, polecat — not resolvable)
    'porcupine':         None,
    'nutria':            None,
    'muskrat':           None,
    'raccoon':           None,
    'reindeer':          None,

    # ── Domestic, present in Species as special ids ────────────────
    'cow':              -10,    # cow
    'sheep':            -11,    # sheep

    # ── Birds: 8 DeepFaune sub-classes + fallback ──────────────────
    # Label format — "bird <subclass>" with a SPACE (see predictTools.py:72).
    'bird':              -18,   # legacy, in case birdclassification=False
    'bird anseriform':   -12,   # Anseriformes (waterfowl)
    'bird columbiform':  -13,   # Columbiformes (pigeons/doves)
    'bird corvid':       -14,   # corvids
    'bird galliform':    -15,   # Galliformes (gamebirds)
    'bird piciform':     -16,   # Piciformes (woodpeckers)
    'bird raptor':       -17,   # birds of prey
    'bird otherbird':    -18,   # other bird
    'bird passerine':    -9,    # small passerines
    'bird undefined':    -18,   # bird, sub-classifier < threshold → "other bird"

    # ── DeepFaune returns this when score < threshold ──────────────
    'undefined':         None,
}


def refresh_label_map(session) -> int:
    """Load the mapping from the ai_label_map table (the SINGLE source of
    truth) and merge it ON TOP of the built-in `DEEPFAUNE_TO_SPECIES_ID`
    defaults.

    The built-in dict stays as a fallback seed: if the table is missing /
    empty / the DB is unavailable — nothing changes and the worker runs as
    before. Call once at the start of a run (a live SQLAlchemy session is
    required).

    Returns the number of rows loaded (0 = only the built-in fallback used).
    """
    try:
        from sqlalchemy import text
        rows = session.execute(text("SELECT label, species_id FROM ai_label_map")).fetchall()
    except Exception:
        return 0
    n = 0
    for label, species_id in rows:
        if label:
            DEEPFAUNE_TO_SPECIES_ID[label.strip().lower()] = species_id
            n += 1
    return n


def map_deepfaune_label(label: Optional[str]) -> Optional[int]:
    """Return Species.id or None.

    None means one of two things:
      • for known labels — the species does not map (absent from Species).
      • for unknown/undefined labels — protection against new DeepFaune
        versions that may emit a label we did not anticipate.
    In both cases the raw label is still stored in the DB, so we lose
    nothing.
    """
    if not label:
        return None
    return DEEPFAUNE_TO_SPECIES_ID.get(label.strip().lower())
