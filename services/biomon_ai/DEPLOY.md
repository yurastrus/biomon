# Розгортання biomon_ai на сервері

Інструкція покрокова — потрібен SSH-доступ і `sudo`.

> Цільовий сервер: Ubuntu, `/var/www/biomon/` (біомон уже розгорнутий).
> Усе AI-related кладемо в `/opt/biomon-ai/` — окрема директорія, окремий venv.

---

## 0. Передумови

- ~10 GB вільного місця на диску (`df -h /opt`)
- Python 3.10+ (`python3 --version`)
- Доступ до інтернету для `pip` і `wget`
- Біомон уже працює, `ct_db` доступна, таблиці `ai_models`/`ai_predictions`/`ai_run_queue` створено (див. `scripts/init_ai_tables.py`)

---

## 1. Створення структури директорій

```bash
sudo mkdir -p /opt/biomon-ai
sudo chown $USER:$USER /opt/biomon-ai
cd /opt/biomon-ai
```

Фінальна структура буде така:

```
/opt/biomon-ai/
├── venv/                ← Python venv (torch, ultralytics ~5 GB)
├── deepfaune/           ← склонована DeepFaune + ваги (.pt-файли ~1.5 GB)
├── biomon_ai → /var/www/biomon/services/biomon_ai   ← symlink на код
├── logs/                ← логи cron-прогонів
└── run-batch.sh         ← обгортка для cron
```

---

## 2. Клонування DeepFaune

```bash
cd /opt/biomon-ai
git clone https://plmlab.math.cnrs.fr/deepfaune/software.git deepfaune
cd deepfaune
git checkout v1.4.1     # фіксована версія, та що тестували
cd ..
```

> Якщо `v1.4.1` тегу нема, заглянь у `git tag -l` і вибери актуальний; оновіть
> `AI_RUNNER_MODEL_VERSION` у конфігу відповідно.

---

## 3. Завантаження ваг моделей

DeepFaune git-репо НЕ містить ваг (вони ~1.5 GB). Тягнемо з їх CDN:

```bash
cd /opt/biomon-ai/deepfaune
BASE=https://pbil.univ-lyon1.fr/software/download/deepfaune/v1.4

# Класифікатор (1.2 GB — найбільший)
wget -c $BASE/deepfaune-vit_large_patch14_dinov2.lvd142m.v4.pt

# Класифікатор птахів (8 MB) — теоретично не потрібен, бо birdclassification=False,
# але DeepFaune-код намагається завантажити цей файл при старті. Безпечніше мати.
wget -c $BASE/deepfaune-vit_large_patch14_dinov2.lvd142m.v4-bird_head.pt

# Детектор тварин (22 MB)
wget -c $BASE/deepfaune-yolov8s_960.pt

# MegaDetector sorrel (19 MB) — fallback-детектор для empty-фото
wget -c $BASE/md_v1000.0.0-sorrel.pt

# Опційно — повільніший але точніший детектор (281 MB). Можна пропустити.
# wget -c $BASE/md_v1000.0.0-redwood.pt
```

Перевірка:

```bash
ls -lh /opt/biomon-ai/deepfaune/*.pt
# Має бути 4 файли загальною масою ~1.3 GB
```

---

## 4. Створення venv і встановлення залежностей

```bash
cd /opt/biomon-ai
python3 -m venv venv
source venv/bin/activate

# Оновлення pip
pip install --upgrade pip

# Залежності DeepFaune + наш worker
pip install torch torchvision \
            ultralytics yolov5 timm \
            pandas dill hachoir \
            setuptools==81 \
            sqlalchemy psycopg2-binary python-dotenv

# Перевірка
python -c "import torch; print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())"
# Очікуваний вивід: torch: X.Y.Z cuda: False  (бо CPU-only)
```

> На сервері без GPU `cuda: False` — це нормально. Інференс йтиме на CPU.
> ~5-10 сек/серія на середній сервер з 5 фото.

---

## 5. Symlink на код біомону

Код `biomon_ai/` живе **в репозиторії біомону**, не дублюється. На `/opt/biomon-ai/` лежить лише symlink:

```bash
ln -s /var/www/biomon/services/biomon_ai /opt/biomon-ai/biomon_ai
ls -la /opt/biomon-ai/biomon_ai
# Має показати linkнутий шлях
```

Тепер `git pull` біомону одразу оновлює і worker-код (нічого окремо синхронізувати не треба).

---

## 6. Environment file для cron і systemd

`/opt/biomon-ai/.env` — налаштування для worker'а. Більшість береться з біомонівського `.env`, можна симлінкнути, але DEEPFAUNE_PATH специфічний для AI-сервісу.

```bash
cat > /opt/biomon-ai/.env << 'EOF'
# DB (та сама що в біомону)
CT_DATABASE_URL=postgresql://ct_user:PASSWORD@localhost:5432/ct_db

# Де лежать фото (та сама директорія що в біомону для UPLOAD_PATH)
CAMERA_TRAP_UPLOAD_PATH=/var/biomon-data/camera_trap

# Шлях до DeepFaune
DEEPFAUNE_PATH=/opt/biomon-ai/deepfaune

# Параметри AI
AI_RUNNER_THRESHOLD=0.8
AI_RUNNER_MAX_PER_RUN=100
AI_RUNNER_MODEL_NAME=DeepFaune
AI_RUNNER_MODEL_VERSION=1.4.1
EOF

chmod 600 /opt/biomon-ai/.env   # бо містить пароль БД
```

> CT_DATABASE_URL і CAMERA_TRAP_UPLOAD_PATH мають збігатися з тим що
> у `/var/www/biomon/.env`. Можна навіть `source` його замість дублювання,
> але DEEPFAUNE_PATH біомон не знає.

---

## 7. Smoke-test (StubAdapter, без моделі)

Перевіряємо що worker бачить БД і знаходить pending observations:

```bash
cd /opt/biomon-ai
export $(grep -v '^#' .env | xargs)
venv/bin/python -m biomon_ai.cli --batch=2 --adapter=stub -v
```

Очікуваний вивід:
```
[INFO] Adapter: Stub 0.0.1 | upload_path=/var/biomon-data/camera_trap | threshold=0.8
[INFO] Picked 2 pending observation(s)
[INFO] Observation NN: classifying X photo(s)
[INFO] Observation NN: saved X prediction(s)
[INFO] Batch done. Processed: 2/2
```

Якщо `Picked 0` — значить нема pending observations або таблиці не створено.

Прибрати Stub-записи з БД:
```bash
psql -U ct_user -d ct_db -c "
DELETE FROM ai_predictions WHERE model_id IN (SELECT id FROM ai_models WHERE name='Stub');
DELETE FROM ai_models WHERE name='Stub';
"
```

---

## 8. Smoke-test (DeepFaune, реальна модель)

Перший запуск з DeepFaune — моделі завантажуються в RAM (~30 сек), потім працює:

```bash
cd /opt/biomon-ai
export $(grep -v '^#' .env | xargs)
venv/bin/python -m biomon_ai.cli --batch=2 --adapter=deepfaune -v
```

Перевір що в `ai_predictions` з'явилися рядки:
```bash
psql -U ct_user -d ct_db -c "
SELECT m.name, m.version, COUNT(*) AS predictions
FROM ai_predictions p JOIN ai_models m ON m.id = p.model_id
GROUP BY m.name, m.version;
"
```

---

## 9. Cron-скрипт для нічних прогонів

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

Cron-задача (запуск о 02:00 щоночі):

```bash
sudo tee /etc/cron.d/biomon-ai > /dev/null << 'EOF'
# AI-класифікація pending observations кожної ночі.
# Логи: /opt/biomon-ai/logs/run-YYYY-MM-DD.log
0 2 * * * yura /opt/biomon-ai/run-batch.sh >> /opt/biomon-ai/logs/run-$(date +\%Y-\%m-\%d).log 2>&1
EOF

mkdir -p /opt/biomon-ai/logs
chown yura:yura /opt/biomon-ai/logs
```

> Замінити `yura` на той користувач, від якого запускати worker.
> Для перевірки cron не чекати ночі — запусти руками:
> ```bash
> sudo -u yura /opt/biomon-ai/run-batch.sh
> ```

---

## 10. Адмін-кнопка → polling воркера через cron

Кнопка на `/camera-traps/admin` додає рядок у `ai_run_queue`. Worker його
підхоплює коли наступним разом запускається. Найпростіше — окремий cron
кожні 2-5 хвилин:

```bash
# Додаємо в /etc/cron.d/biomon-ai
*/3 * * * * yura cd /opt/biomon-ai && export $(grep -v '^#' .env | xargs) && \
            venv/bin/python -m biomon_ai.cli --from-queue --adapter=deepfaune \
            >> /opt/biomon-ai/logs/queue-$(date +\%Y-\%m-\%d).log 2>&1
```

> Запит ставить адмін → за 0-3 хв worker його взяв і обробив 100 серій.
> Можна також зробити окремий systemd-таймер з 1-хвилинним інтервалом —
> але простіше через cron.

---

## 11. Logrotate

Логи можуть набирати об'єм. Налаштовуємо ротацію:

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

## 12. Перевірка готовності

Чек-лист:

- [ ] `/opt/biomon-ai/venv/bin/python -c "import torch"` працює
- [ ] `/opt/biomon-ai/deepfaune/predictTools.py` існує
- [ ] `/opt/biomon-ai/deepfaune/*.pt` — 4 файли загалом ~1.3 GB
- [ ] `/opt/biomon-ai/biomon_ai` — symlink на біомон-репо
- [ ] `cat /opt/biomon-ai/.env` — заповнений, права 600
- [ ] `psql -d ct_db -c "\d ai_models"` — таблиця існує
- [ ] StubAdapter smoke-test проходить
- [ ] DeepFauneAdapter smoke-test проходить, рядки з'являються в `ai_predictions`
- [ ] `crontab -l` показує задачі (або `cat /etc/cron.d/biomon-ai`)
- [ ] Cron-task запускається руками без помилок

---

## Troubleshooting

### `ImportError: cannot import name 'PredictorImage'`

DeepFaune не в `sys.path` або зіпсована копія.
```bash
ls /opt/biomon-ai/deepfaune/predictTools.py    # має існувати
echo $DEEPFAUNE_PATH                           # має == /opt/biomon-ai/deepfaune
```

### `psycopg2.OperationalError: connection refused`

PostgreSQL не на 5432 або в `CT_DATABASE_URL` хибний host/port.

### `RuntimeError: PytorchStreamReader failed`

Ваги (.pt) пошкоджені — частково завантажились. Стерти і знову `wget -c`:
```bash
rm /opt/biomon-ai/deepfaune/deepfaune-vit_large_*.pt
wget -c <URL>
```

### Memory error (Killed) під час прогону

ViT-моделі споживають ~2 GB RAM. Якщо OOM:
1. Зменшити batch у DeepFaune — у `predictTools.py` параметр `BATCH_SIZE=8` змінити на 4.
2. Перевірити що GeoServer не їсть 4 GB паралельно з worker'ом — нічний batch має йти **після** того як GeoServer стих, або до нього.

### Cron не запускає

```bash
grep CRON /var/log/syslog | tail
```

Зазвичай — права на `/opt/biomon-ai/run-batch.sh` або забутий `chmod +x`.

---

## Оновлення коду

```bash
cd /var/www/biomon
git pull
# Worker автоматично підхоче зміни на наступному cron-запуску (symlink).
```

## Оновлення DeepFaune

```bash
cd /opt/biomon-ai/deepfaune
git fetch && git checkout vX.Y.Z
# Якщо ваги мали змінитись — перезавантажити з URL'ом нової версії.

# Оновити версію в /opt/biomon-ai/.env:
sed -i 's/^AI_RUNNER_MODEL_VERSION=.*/AI_RUNNER_MODEL_VERSION=X.Y.Z/' /opt/biomon-ai/.env

# Перший прогін зареєструє нову модель в ai_models; старі прогнози
# лишаються під попереднім model_id.
```
