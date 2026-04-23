"""
SDM routes — dashboard, map, job API.

Маршрути
--------
GET  /sdm/              — панель управління (кнопки + статус задач)
POST /sdm/enqueue       — поставити задачу у чергу (rebuild / reproject)
GET  /sdm/job/<job_id>  — JSON статус задачі (polling з UI)
GET  /sdm/map           — Leaflet-карта поширення
GET  /sdm/api/predictions — GeoJSON для Leaflet (фільтр: species, season)
"""
from __future__ import annotations

import json

from flask import jsonify, render_template, request

from app.sdm import sdm_bp


# ── Допоміжна функція — отримати список видів із camera_traps ────

def _species_list() -> list[str]:
    """Список видів, що зустрічаються у sdm_detection_history (вже є дані)."""
    try:
        from adapters.biomon import sdm_connection
        from sqlalchemy import text
        with sdm_connection() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT species_code FROM sdm_detection_history ORDER BY 1"
            )).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _recent_jobs(limit: int = 20) -> list[dict]:
    """Останні задачі з sdm_job_queue."""
    try:
        from adapters.biomon import sdm_connection
        from sqlalchemy import text
        with sdm_connection() as conn:
            rows = conn.execute(text("""
                SELECT job_id, task_name, status, progress_pct,
                       created_at, started_at, finished_at,
                       payload->>'species_code' AS species_code,
                       error
                FROM sdm_job_queue
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"lim": limit}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        return []


def _current_runs() -> list[dict]:
    """Поточні (is_current=true) model runs — для таблиці на дашборді."""
    try:
        from adapters.biomon import sdm_connection
        from sqlalchemy import text
        with sdm_connection() as conn:
            rows = conn.execute(text("""
                SELECT r.run_id, r.species_code, r.level_code,
                       r.temporal_axis, r.metrics, r.finished_at,
                       r.predictors_used
                FROM sdm_model_run r
                WHERE r.is_current = true
                ORDER BY r.species_code, r.level_code
            """)).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        return []


# ── Маршрути ──────────────────────────────────────────────────────

@sdm_bp.route("/")
def dashboard():
    """Панель управління SDM."""
    species_list = _species_list()
    recent_jobs  = _recent_jobs()
    current_runs = _current_runs()

    # Останній активний job (якщо є running/queued)
    active_job = next(
        (j for j in recent_jobs if j["status"] in ("queued", "running")),
        None
    )

    return render_template(
        "sdm/dashboard.html",
        species_list=species_list,
        recent_jobs=recent_jobs,
        current_runs=current_runs,
        active_job=active_job,
    )


@sdm_bp.route("/enqueue", methods=["POST"])
def enqueue():
    """
    Поставити задачу у чергу.

    Form fields:
        task_name   : "rebuild_model" | "reproject"
        species_code: напр. "Capreolus capreolus"
        level_code  : "EEA_1KM" (default)
        predictors  : "DEM,SLOPE,LC_TREE" (comma-separated)
        seasons     : "SPRING,SUMMER,AUTUMN,WINTER"
    """
    from adapters.worker import create_job

    task_name    = request.form.get("task_name", "rebuild_model")
    species_code = request.form.get("species_code", "").strip()
    level_code   = request.form.get("level_code", "EEA_1KM").strip()
    predictors   = request.form.get("predictors", "DEM,SLOPE,LC_TREE").strip()
    seasons_raw  = request.form.get("seasons", "SPRING,SUMMER,AUTUMN,WINTER").strip()

    if not species_code:
        return jsonify({"error": "species_code is required"}), 400

    payload = {
        "species_code":    species_code,
        "level_code":      level_code,
        "predictor_codes": [p.strip() for p in predictors.split(",") if p.strip()],
        "seasons":         [s.strip() for s in seasons_raw.split(",") if s.strip()],
    }

    job_id = create_job(task_name=task_name, payload=payload)
    return jsonify({"job_id": job_id, "status": "queued"})


@sdm_bp.route("/job/<job_id>")
def job_status(job_id: str):
    """JSON статус задачі для polling."""
    from adapters.worker import get_job_status
    data = get_job_status(job_id)
    if data is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@sdm_bp.route("/map")
def map_view():
    """Leaflet-карта поширення видів."""
    species_list = _species_list()
    seasons = ["SPRING", "SUMMER", "AUTUMN", "WINTER"]
    selected_species = request.args.get("species", species_list[0] if species_list else "")
    selected_season  = request.args.get("season", "SPRING")
    return render_template(
        "sdm/map.html",
        species_list=species_list,
        seasons=seasons,
        selected_species=selected_species,
        selected_season=selected_season,
    )


@sdm_bp.route("/api/predictions")
def api_predictions():
    """
    GeoJSON FeatureCollection для Leaflet choropleth.

    Query params:
        species : напр. "Capreolus capreolus"
        season  : "SPRING" | "SUMMER" | "AUTUMN" | "WINTER"
        level   : "EEA_1KM" (default)
    """
    from adapters.biomon import sdm_connection
    from sqlalchemy import text

    species = request.args.get("species", "")
    season  = request.args.get("season", "SPRING").upper()
    level   = request.args.get("level", "EEA_1KM")

    if not species:
        return jsonify({"error": "species required"}), 400

    temporal_key = json.dumps({"kind": "season", "value": season})

    try:
        with sdm_connection() as conn:
            rows = conn.execute(text("""
                SELECT
                    p.cell_id,
                    p.occupancy_prob,
                    p.occupancy_sd,
                    p.detection_prob,
                    ST_AsGeoJSON(c.geom_4326)::json AS geom
                FROM sdm_current_prediction p
                JOIN sdm_grid_cell c ON c.cell_id = p.cell_id
                WHERE p.species_code  = :sp
                  AND p.level_code    = :lv
                  AND p.temporal_key  = CAST(:tk AS jsonb)
                ORDER BY p.cell_id
            """), {"sp": species, "lv": level, "tk": temporal_key}).fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": r.geom,
            "properties": {
                "cell_id":        r.cell_id,
                "occupancy_prob": round(r.occupancy_prob, 4) if r.occupancy_prob is not None else None,
                "occupancy_sd":   round(r.occupancy_sd,   4) if r.occupancy_sd   is not None else None,
                "detection_prob": round(r.detection_prob, 4) if r.detection_prob is not None else None,
            }
        })

    return jsonify({"type": "FeatureCollection", "features": features})
