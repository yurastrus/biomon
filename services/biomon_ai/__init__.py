"""biomon_ai — фоновий AI-класифікатор фотографій з фотопасток.

Модуль живе в окремому процесі з власним venv (torch + ultralytics) і
спілкується з біомоном виключно через таблиці ai_models / ai_predictions /
ai_run_queue у ct_db. Flask-додаток нічого з цього модуля не імпортує.

Структура:
    adapter.py      Абстрактний IClassifier + DeepFauneAdapter обгортка
    species_map.py  Мапінг DeepFaune-label → biomon Species.id
    db.py           Окремий SQLAlchemy engine до ct_db, функції доступу
    worker.py       Основна логіка: bерути pending observation → прогнати → зберегти
    cli.py          Точка входу: `python -m biomon_ai.cli --batch=N`

Дивись DEPLOY.md для інструкцій з розгортання на сервері.
"""
