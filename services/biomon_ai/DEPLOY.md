# Deploying biomon_ai on a server

Step-by-step instructions — SSH access and `sudo` are required.

> Target server: Ubuntu, `/var/www/biomon/` (biomon is already deployed).
> Everything AI-related goes into `/opt/biomon-ai/` — a separate directory with its own venv.

---

## 0. Prerequisites

- ~10 GB of free disk space (`df -h /opt`)
- Python 3.10+ (`python3 --version`)
- Internet access for `pip` and `wget`
- biomon is already running, `ct_db` is reachable, the `ai_models`/`ai_predictions`/`ai_run_queue` tables exist (see `scripts/init_ai_tables.py`)

---

## 1. Creating the directory structure

```bash
sudo mkdir -p /opt/biomon-ai
sudo chown $USER:$USER /opt/biomon-ai
cd /opt/biomon-ai
```

The final structure will look like this:

```
/opt/biomon-ai/
├── venv/                ← Python venv (torch, ultralytics ~5 GB)
├── deepfaune/           ← cloned DeepFaune + weights (.pt files ~1.5 GB)
├── biomon_ai → /var/www/biomon/services/biomon_ai   ← symlink to the code
├── logs/                ← logs of cron runs
└── run-batch.sh         ← wrapper for cron
```

---

## 2. Cloning DeepFaune

```bash
cd /opt/biomon-ai
git clone https://plmlab.math.cnrs.fr/deepfaune/software.git deepfaune
cd deepfaune
git checkout v1.4.1     # pinned version, the one we tested
cd ..
```

> If the `v1.4.1` tag does not exist, check `git tag -l` and pick the current one; update
> `AI_RUNNER_MODEL_VERSION` in the config accordingly.

---

## 3. Downloading the model weights

The DeepFaune git repo does NOT contain the weights (they are ~1.5 GB). We pull them from their CDN:

```bash
cd /opt/biomon-ai/deepfaune
BASE=https://pbil.univ-lyon1.fr/software/download/deepfaune/v1.4

# Classifier (1.2 GB — the largest one)
wget -c $BASE/deepfaune-vit_large_patch14_dinov2.lvd142m.v4.pt

# Bird classifier (8 MB) — in theory not needed, since birdclassification=False,
# but the DeepFaune code tries to load this file at startup. Safer to have it.
wget -c $BASE/deepfaune-vit_large_patch14_dinov2.lvd142m.v4-bird_head.pt

# Animal detector (22 MB)
wget -c $BASE/deepfaune-yolov8s_960.pt

# MegaDetector sorrel (19 MB) — fallback detector for empty photos
wget -c $BASE/md_v1000.0.0-sorrel.pt

# Optional — slower but more accurate detector (281 MB). Can be skipped.
# wget -c $BASE/md_v1000.0.0-redwood.pt
```

Verification:

```bash
ls -lh /opt/biomon-ai/deepfaune/*.pt
# Should be 4 files totaling ~1.3 GB
```

---

## 4. Creating a venv and installing dependencies

```bash
cd /opt/biomon-ai
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# DeepFaune dependencies + our worker
pip install torch torchvision \
            ultralytics yolov5 timm \
            pandas dill hachoir \
            setuptools==81 \
            sqlalchemy psycopg2-binary python-dotenv

# Verification
python -c "import torch; print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())"
# Expected output: torch: X.Y.Z cuda: False  (because CPU-only)
```

> On a server without a GPU, `cuda: False` is normal. Inference will run on the CPU.
> ~5-10 sec/series on an average server with 5 photos.

---

## 5. Symlink to the biomon code

The `biomon_ai/` code lives **in the biomon repository**, it is not duplicated. Only a symlink sits in `/opt/biomon-ai/`:

```bash
ln -s /var/www/biomon/services/biomon_ai /opt/biomon-ai/biomon_ai
ls -la /opt/biomon-ai/biomon_ai
# Should show the linked path
```

Now a `git pull` of biomon immediately updates the worker code too (nothing needs to be synced separately).

---

## 6. Environment file for cron and systemd

`/opt/biomon-ai/.env` — settings for the worker. Most of it comes from biomon's `.env` and can be symlinked, but DEEPFAUNE_PATH is specific to the AI service.

```bash
cat > /opt/biomon-ai/.env << 'EOF'
# DB (the same one as in biomon)
CT_DATABASE_URL=postgresql://ct_user:PASSWORD@localhost:5432/ct_db

# Where the photos live (the same directory as biomon's UPLOAD_PATH)
CAMERA_TRAP_UPLOAD_PATH=/var/biomon-data/camera_trap

# Path to DeepFaune
DEEPFAUNE_PATH=/opt/biomon-ai/deepfaune

# AI parameters
AI_RUNNER_THRESHOLD=0.8
AI_RUNNER_MAX_PER_RUN=100
AI_RUNNER_MODEL_NAME=DeepFaune
AI_RUNNER_MODEL_VERSION=1.4.1
EOF

chmod 600 /opt/biomon-ai/.env   # because it contains the DB password
```

> CT_DATABASE_URL and CAMERA_TRAP_UPLOAD_PATH must match those
> in `/var/www/biomon/.env`. You can even `source` it instead of duplicating,
> but biomon does not know about DEEPFAUNE_PATH.

---

## 7. Smoke test (StubAdapter, no model)

We verify that the worker sees the DB and finds pending observations:

```bash
cd /opt/biomon-ai
export $(grep -v '^#' .env | xargs)
venv/bin/python -m biomon_ai.cli --batch=2 --adapter=stub -v
```

Expected output:
```
[INFO] Adapter: Stub 0.0.1 | upload_path=/var/biomon-data/camera_trap | threshold=0.8
[INFO] Picked 2 pending observation(s)
[INFO] Observation NN: classifying X photo(s)
[INFO] Observation NN: saved X prediction(s)
[INFO] Batch done. Processed: 2/2
```

If `Picked 0` — it means there are no pending observations or the tables were not created.

Remove the Stub records from the DB:
```bash
psql -U ct_user -d ct_db -c "
DELETE FROM ai_predictions WHERE model_id IN (SELECT id FROM ai_models WHERE name='Stub');
DELETE FROM ai_models WHERE name='Stub';
"
```

---

## 8. Smoke test (DeepFaune, real model)

The first run with DeepFaune loads the models into RAM (~30 sec), then it works:

```bash
cd /opt/biomon-ai
export $(grep -v '^#' .env | xargs)
venv/bin/python -m biomon_ai.cli --batch=2 --adapter=deepfaune -v
```

Verify that rows appeared in `ai_predictions`:
```bash
psql -U ct_user -d ct_db -c "
SELECT m.name, m.version, COUNT(*) AS predictions
FROM ai_predictions p JOIN ai_models m ON m.id = p.model_id
GROUP BY m.name, m.version;
"
```

---

## 9. Cron script for nightly runs

`/opt/biomon-ai/run-batch.sh`:

```bash
cat > /opt/biomon-ai/run-batch.sh << 'EOF'
#!/bin/bash
set -e
cd /opt/biomon-ai
export $(grep -v '^#' .env | xargs)
exec venv/bin/python -m biomon_ai.cli --batch=$AI_RUNNER_MAX_PER_RUN
EOF
chmod +x /opt/biomon-ai/run-batch.sh
```

Cron job (runs at 02:00 every night):

```bash
sudo tee /etc/cron.d/biomon-ai > /dev/null << 'EOF'
# AI classification of pending observations every night.
# Logs: /opt/biomon-ai/logs/run-YYYY-MM-DD.log
0 2 * * * yura /opt/biomon-ai/run-batch.sh >> /opt/biomon-ai/logs/run-$(date +\%Y-\%m-\%d).log 2>&1
EOF

mkdir -p /opt/biomon-ai/logs
chown yura:yura /opt/biomon-ai/logs
```

> Replace `yura` with the user the worker should run as.
> To test cron without waiting for the night — run it by hand:
> ```bash
> sudo -u yura /opt/biomon-ai/run-batch.sh
> ```

---

## 10. Admin button → worker polling via cron

The button on `/camera-traps/admin` adds a row to `ai_run_queue`. The worker
picks it up the next time it runs. The simplest approach — a separate cron
every 2-5 minutes:

```bash
# Add to /etc/cron.d/biomon-ai
*/3 * * * * yura cd /opt/biomon-ai && export $(grep -v '^#' .env | xargs) && \
            venv/bin/python -m biomon_ai.cli --from-queue --adapter=deepfaune \
            >> /opt/biomon-ai/logs/queue-$(date +\%Y-\%m-\%d).log 2>&1
```

> The admin submits a request → within 0-3 min the worker picks it up and processes 100 series.
> You could also set up a separate systemd timer with a 1-minute interval —
> but cron is simpler.

---

## 11. Logrotate

Logs can accumulate in size. We set up rotation:

```bash
sudo tee /etc/logrotate.d/biomon-ai > /dev/null << 'EOF'
/opt/biomon-ai/logs/*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 0644 yura yura
}
EOF
```

---

## 12. Readiness check

Checklist:

- [ ] `/opt/biomon-ai/venv/bin/python -c "import torch"` works
- [ ] `/opt/biomon-ai/deepfaune/predictTools.py` exists
- [ ] `/opt/biomon-ai/deepfaune/*.pt` — 4 files totaling ~1.3 GB
- [ ] `/opt/biomon-ai/biomon_ai` — symlink to the biomon repo
- [ ] `cat /opt/biomon-ai/.env` — populated, permissions 600
- [ ] `psql -d ct_db -c "\d ai_models"` — the table exists
- [ ] StubAdapter smoke test passes
- [ ] DeepFauneAdapter smoke test passes, rows appear in `ai_predictions`
- [ ] `crontab -l` shows the jobs (or `cat /etc/cron.d/biomon-ai`)
- [ ] The cron task runs by hand without errors

---

## Troubleshooting

### `ImportError: cannot import name 'PredictorImage'`

DeepFaune is not in `sys.path` or the copy is corrupted.
```bash
ls /opt/biomon-ai/deepfaune/predictTools.py    # must exist
echo $DEEPFAUNE_PATH                           # must == /opt/biomon-ai/deepfaune
```

### `psycopg2.OperationalError: connection refused`

PostgreSQL is not on 5432, or the host/port in `CT_DATABASE_URL` is wrong.

### `RuntimeError: PytorchStreamReader failed`

The weights (.pt) are corrupted — they downloaded only partially. Delete and `wget -c` again:
```bash
rm /opt/biomon-ai/deepfaune/deepfaune-vit_large_*.pt
wget -c <URL>
```

### Memory error (Killed) during a run

The ViT models consume ~2 GB of RAM. If OOM:
1. Reduce the batch in DeepFaune — change the `BATCH_SIZE=8` parameter in `predictTools.py` to 4.
2. Make sure GeoServer is not eating 4 GB in parallel with the worker — the nightly batch should run **after** GeoServer has quieted down, or before it.

### Cron does not run

```bash
grep CRON /var/log/syslog | tail
```

Usually — permissions on `/opt/biomon-ai/run-batch.sh` or a forgotten `chmod +x`.

---

## Updating the code

```bash
cd /var/www/biomon
git pull
# The worker automatically picks up the changes on the next cron run (symlink).
```

## Updating DeepFaune

```bash
cd /opt/biomon-ai/deepfaune
git fetch && git checkout vX.Y.Z
# If the weights were supposed to change — re-download from the URL of the new version.

# Update the version in /opt/biomon-ai/.env:
sed -i 's/^AI_RUNNER_MODEL_VERSION=.*/AI_RUNNER_MODEL_VERSION=X.Y.Z/' /opt/biomon-ai/.env

# The first run registers the new model in ai_models; old predictions
# stay under the previous model_id.
```
