# SPDX-License-Identifier: AGPL-3.0-only
"""biomon_ai — background AI classifier for camera-trap photos.

The module runs in a separate process with its own venv (torch + ultralytics)
and communicates with biomon exclusively through the ai_models / ai_predictions /
ai_run_queue tables in ct_db. The Flask app does not import anything from this module.

Structure:
    adapter.py      Abstract IClassifier + DeepFauneAdapter wrapper
    species_map.py  Mapping of DeepFaune label → biomon Species.id
    db.py           Separate SQLAlchemy engine to ct_db, access functions
    worker.py       Core logic: take a pending observation → run it → store the result
    cli.py          Entry point: `python -m biomon_ai.cli --batch=N`

See DEPLOY.md for server deployment instructions.
"""
