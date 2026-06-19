# biomon

A Flask-based biodiversity monitoring platform for wildlife data collection, review, and analysis. The platform supports three monitoring modules: **Camera Traps** (photo-based wildlife detection), **PAM** (Passive Acoustic Monitoring), and **SDM** (Species Distribution Modelling). It is bilingual (Ukrainian / English) and multi-institution.

## Architecture

The application is structured as a Flask app factory with three Git submodules that act as independent, reusable packages:

```
biomon/               ← this repo (main app)
├── app/
│   ├── camera_traps/ ← submodule: yurastrus/shared-ct   (branch: main)
│   ├── pam/          ← submodule: yurastrus/shared-pam
│   ├── sdm/          ← submodule: yurastrus/shared-sdm
│   ├── admin/
│   ├── models/       ← main-db ORM models (User, Role, Institution)
│   ├── routes/       ← main blueprint (login, home, static pages)
│   ├── static/
│   ├── templates/
│   ├── utils/
│   ├── commands.py   ← Flask CLI commands
│   ├── extensions.py ← all Flask extension singletons
│   └── __init__.py   ← create_app() factory
├── config.py         ← DevelopmentConfig / ProductionConfig / TestingConfig
├── wsgi.py           ← gunicorn entry point
├── migrations/       ← Alembic migrations for the main database only
├── translations/     ← Flask-Babel .po / .mo files
├── scripts/          ← one-time init and backfill scripts
└── tests/
```

The app factory (`create_app()`) loads one of three config classes (`DevelopmentConfig`, `ProductionConfig`, `TestingConfig`) based on the `FLASK_CONFIG` environment variable (default: `production`), then registers all five blueprints:

| Blueprint | URL prefix |
|---|---|
| `main` | `/` |
| `admin` | `/admin` |
| `pam_bp` | *(set inside pam submodule)* |
| `camera_traps_bp` | `/<lang>/camera-traps` |
| `sdm_bp` | `/<lang>/sdm` |

### Databases

The platform uses up to five PostgreSQL databases. Only the main database has a SQLite fallback (for local development without Postgres):

| Env var | Database | Managed by |
|---|---|---|
| `DATABASE_URL` | Main app (users, roles, institutions) | Flask-Migrate (`migrations/`) |
| `CT_DATABASE_URL` | Camera traps (`ct_db`) | shared-ct submodule |
| `PAM_DATABASE_URL` | Passive acoustic monitoring | shared-pam submodule |
| `GEODATA_DATABASE_URI` | GeoServer / spatial data | external |
| `SDM_DATABASE_URL` | Species distribution models | shared-sdm submodule |

### Main database models

| Table | Model | Purpose |
|---|---|---|
| `user` | `User` | Auth, roles, profile |
| `role` | `Role` | Named roles with hierarchy |
| `user_roles` | *(association)* | User ↔ Role many-to-many |
| `institutions` | `Institution` | Monitoring organisations (bilingual name, ecoregion) |
| `user_institutions` | `UserInstitution` | User ↔ Institution with `can_export` flag |
| `site_text_content` | `SiteTextContent` | CMS-style editable page content |

Role hierarchy (higher roles imply all listed lower ones):

```
admin
└── manager → pam_verifier, ct_verifier, analyst, viewer
    ├── pam_verifier → viewer
    ├── ct_verifier  → viewer
    └── analyst      → ct_verifier, viewer
```

## Installation

### Prerequisites

- Python 3.10+
- PostgreSQL 16 (or SQLite for main-db-only local dev)
- Git (with submodule support)

### 1. Clone with submodules

```bash
git clone --recurse-submodules https://github.com/yurastrus/biomon.git
cd biomon
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure environment variables

Copy [`.env.example`](.env.example) to `.env` in the project root and fill in values for your environment. The same variables are listed below for reference.

```dotenv
# ── Required ──────────────────────────────────────────────────────────────────
SECRET_KEY=<generate a strong random key>

# ── Databases ─────────────────────────────────────────────────────────────────
# Main app DB (falls back to SQLite if unset)
DATABASE_URL=postgresql://user:password@localhost/biomon_db

# Camera traps module DB
CT_DATABASE_URL=postgresql://user:password@localhost/ct_db

# Passive acoustic monitoring DB
PAM_DATABASE_URL=postgresql://user:password@localhost/pam_db

# Geodata (GeoServer / spatial layers)
GEODATA_DATABASE_URI=postgresql://user:password@localhost/geodata

# Species distribution models DB
SDM_DATABASE_URL=postgresql://user:password@localhost/sdm_db

# ── Flask ─────────────────────────────────────────────────────────────────────
# development | production | testing  (default: production)
FLASK_CONFIG=development

# ── File storage ──────────────────────────────────────────────────────────────
# Required in production; dev falls back to camera_trap_data/ in the project root
CAMERA_TRAP_UPLOAD_PATH=/var/www/biomon/camera_trap_data

# Required in production
PAM_UPLOAD_PATH=/var/www/biomon/pam_data_import/segments

# ── Email (Flask-Mail) ────────────────────────────────────────────────────────
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=noreply@example.com
MAIL_PASSWORD=<password>
MAIL_DEFAULT_SENDER=noreply@example.com

# Public URL used in email links
SITE_URL=https://biomon.app

# ── AI runner (DeepFaune classifier — optional) ───────────────────────────────
# Set to false on machines without the classifier worker
AI_RUNNER_ENABLED=false
AI_RUNNER_MAX_PER_RUN=200
AI_RUNNER_THRESHOLD=0.8
AI_RUNNER_WORKER_PYTHON=/opt/biomon-ai/venv/bin/python
AI_RUNNER_WORKER_PATH=/opt/biomon-ai
AI_RUNNER_MODEL_NAME=DeepFaune
AI_RUNNER_MODEL_VERSION=1.4.1

# ── Google Earth Engine (SDM module only) ────────────────────────────────────
GEE_SERVICE_ACCOUNT_KEY=/path/to/gee-key.json
GEE_PROJECT_ID=your-gcp-project-id
```

### 4. Set up the main database

```bash
# Apply all Alembic migrations (creates/updates the main-db schema)
venv/Scripts/flask db upgrade        # Windows
venv/bin/flask db upgrade            # Linux / macOS
```

This only covers the **main database**. Each submodule manages its own schema — see the submodule READMEs for their initialisation steps.

### 5. Compile translations

The application uses three independent Babel domains. Each submodule owns its own `babel.cfg`, `messages.pot`, and `translations/` directory; the main app's `babel.cfg` explicitly excludes submodule paths.

| Domain | Catalog location | Covers |
|---|---|---|
| `messages` | `translations/` | Main app (routes, admin, models, base templates) |
| `camera_traps` | `app/camera_traps/translations/` | Camera traps module (shared-ct) |
| `pam` | `app/pam/translations/` | PAM module (shared-pam) |

Run the commands for every domain you modified:

```bash
# ── Main app (messages domain) ─────────────────────────────────────────────
venv/Scripts/pybabel extract -F babel.cfg -k _l -k lazy_gettext -o messages.pot .
venv/Scripts/pybabel update -i messages.pot -d translations
venv/Scripts/pybabel compile -f -d translations

# ── Camera traps module (camera_traps domain) ──────────────────────────────
venv/Scripts/pybabel extract -F app/camera_traps/babel.cfg -k _l -k lazy_gettext -D camera_traps -o app/camera_traps/messages.pot .
venv/Scripts/pybabel update -i app/camera_traps/messages.pot -d app/camera_traps/translations -D camera_traps
venv/Scripts/pybabel compile -f -d app/camera_traps/translations -D camera_traps

# ── PAM module (pam domain) ────────────────────────────────────────────────
venv/Scripts/pybabel extract -F app/pam/babel.cfg -k _l -k lazy_gettext -D pam -o app/pam/messages.pot .
venv/Scripts/pybabel update -i app/pam/messages.pot -d app/pam/translations -D pam
venv/Scripts/pybabel compile -f -d app/pam/translations -D pam
```

On Linux replace `venv/Scripts/` with `venv/bin/`.

`-k _l -k lazy_gettext` are required on `extract` only (not `update`). `-f` (`--use-fuzzy`) is required on `compile` — fuzzy entries are otherwise silently dropped. After `update`, translate new `msgstr` values in the `en` catalog and remove `#, fuzzy` markers; the `uk` catalog needs no changes (msgids are already in Ukrainian).

### 6. Run one-time init scripts (if needed)

The `scripts/` directory contains idempotent initialisation scripts for optional features. Run only the ones relevant to your setup:

| Script | Purpose |
|---|---|
| `init_fast_upload.py` | Creates the `idx_photos_batch_captured` index for fast batch upload |
| `init_ai_tables.py` | Creates AI prediction tables |
| `init_analytics_status.py` | Backfills analytics status column |
| `init_query_indexes.py` | Creates query-performance indexes |
| `init_deployments.py` | Seeds deployment table structure |

```bash
venv/Scripts/python -m scripts.init_fast_upload   # Windows
venv/bin/python -m scripts.init_fast_upload       # Linux
```

## Running the application

### Development

```bash
# With Flask dev server (debug mode, auto-reload)
venv/Scripts/flask run                  # Windows
venv/bin/flask run                      # Linux
```

### Production (gunicorn)

```bash
gunicorn --workers 3 --timeout 120 --bind 0.0.0.0:8003 wsgi:app
```

The `--timeout 120` is required: the upload pipeline can take up to 60 s per batch on single-photo processing.

The `wsgi.py` entry point calls `create_app()` and exposes it as `app` — this name is required by the service configuration.

### SDM worker (optional)

The SDM module uses a background job queue. A systemd service unit is provided:

```bash
sudo cp deploy/sdm-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sdm-worker
```

## Testing

The test suite uses **pytest**. Shared fixtures are in `tests/conftest.py`.

```bash
# Run all tests
venv/Scripts/python -m pytest

# With coverage (camera_traps + pam)
venv/Scripts/python -m pytest --cov=app/camera_traps --cov=app/pam --cov-report=term-missing

# Coverage gate (exit 1 if below 30%)
venv/Scripts/python -m pytest --cov=app/camera_traps --cov=app/pam --cov-fail-under=30 --cov-report=term

# Specific file
venv/Scripts/python -m pytest tests/test_camera_traps_models.py -v
```

Available markers (configured in `pytest.ini`): `slow`, `integration`, `smoke`.

## Directory structure

```
biomon/
├── app/
│   ├── admin/            ← admin panel blueprint
│   ├── camera_traps/     ← camera traps module (git submodule → shared-ct)
│   ├── models/           ← main-db SQLAlchemy models
│   ├── pam/              ← PAM module (git submodule → shared-pam)
│   ├── routes/           ← main blueprint (login, homepage, static pages)
│   ├── sdm/              ← SDM module (git submodule → shared-sdm)
│   ├── static/           ← CSS, JS, images
│   ├── templates/        ← Jinja2 base templates
│   ├── utils/            ← shared utilities (i18n helpers, etc.)
│   ├── commands.py       ← flask CLI commands
│   ├── extensions.py     ← extension singletons (db, login_manager, …)
│   └── __init__.py       ← create_app() factory
├── config.py             ← config classes (Dev / Prod / Test)
├── wsgi.py               ← gunicorn entry point
├── migrations/           ← Alembic migrations (main DB only)
├── scripts/              ← one-time init / backfill scripts
├── tests/                ← pytest test suite
│   └── conftest.py       ← shared fixtures
├── translations/         ← Flask-Babel catalogues (uk + en)
│   ├── en/LC_MESSAGES/
│   └── uk/LC_MESSAGES/
├── babel.cfg             ← pybabel extraction config
├── pytest.ini            ← test configuration
├── requirements.txt      ← pinned Python dependencies
└── deploy/
    └── sdm-worker.service ← systemd unit for the SDM job worker
```

## Submodules

Three directories inside `app/` are Git submodules — independent repositories shared across multiple deployments:

| Path | Repository | Branch | Content |
|---|---|---|---|
| `app/camera_traps` | `yurastrus/shared-ct` | `main` | Camera trap models, routes, upload pipeline, analytics, activity heatmap, AI integration |
| `app/pam` | `yurastrus/shared-pam` | *(default)* | PAM models, routes, audio import, evaluation |
| `app/sdm` | `yurastrus/shared-sdm` | *(default)* | SDM routes, occupancy modelling, GEE integration |

### Updating a submodule

```bash
# Pull latest commits for one submodule
cd app/camera_traps
git pull origin main
cd ../..
git add app/camera_traps
git commit -m "chore: update shared-ct submodule"
```

`app/camera_traps` is also used by a second deployment (`/var/www/myproject` on the server). After committing changes to `shared-ct`, update the submodule pointer in **both** projects and deploy each separately.

### Making changes to submodule code

Work inside the submodule directory as a normal Git repo (commit there first), then update the parent pointer:

```bash
cd app/camera_traps
# … make changes, commit …
git push origin main

cd ../..
git add app/camera_traps
git commit -m "chore(biomon): update shared-ct submodule"
```

For more detail on each module's own models, routes, and database schema, see the README inside the respective submodule directory.

## License: GNU AGPL-3.0

Copyright (C) 2025–2026 Iurii Strus.

This program is free software: you can redistribute it and/or modify it under
the terms of the **GNU Affero General Public License, version 3** as published
by the Free Software Foundation. See the [LICENSE](LICENSE) file for the full
text (`SPDX-License-Identifier: AGPL-3.0-only`).

biomon is a network application: if you run a modified version on a server and
let users interact with it over a network, you must also make the complete
corresponding source code of your modified version available to those users
(AGPL §13). The bundled modules `app/camera_traps` (shared-ct) and `app/pam`
(shared-pam) are separate AGPL-3.0 repositories included as Git submodules.
