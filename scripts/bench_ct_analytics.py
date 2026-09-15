# SPDX-License-Identifier: AGPL-3.0-only
"""
Benchmark the camera-trap analytics queries against whatever ct_db is configured.

Run from the project root:
    venv/Scripts/python -m scripts.bench_ct_analytics                # Windows
    venv/bin/python -m scripts.bench_ct_analytics                    # Linux
    venv/Scripts/python -m scripts.bench_ct_analytics --explain      # + query plans
    venv/Scripts/python -m scripts.bench_ct_analytics --out logs/x.txt

Why this exists: the dashboard and the top-species chart are being optimised in
steps (indexes, then query rewrites), and every step has to be judged on the
same measurement rather than on a plan reading. Timings are wall-clock as the
app sees them, so a run through an SSH tunnel is inflated by the network — only
compare runs taken the same way, on the same machine, against the same database.

Read-only. Executes SELECTs and, with --explain, EXPLAIN ANALYZE (which also
runs the query). It never writes.

The queries mirror, as ORM expressions, what routes.dashboard() issues for an
anonymous/admin viewer with no location, biotope or QC filters, plus the raw
top-species SQL. Filter-heavy variants are covered by the narrow date windows.
"""

import argparse
import statistics
import sys
import time
from datetime import date, timedelta

from sqlalchemy import distinct, func, text

from app import create_app
from app.camera_traps.database import get_ct_session
from app.camera_traps.models import Identification, Location, Observation, Photo
from app.camera_traps.validity import valid_location_id_subquery

# Windows chosen to separate "index would help" (narrow) from "a scan is the
# right plan anyway" (all time). A rewrite that only helps the wide window is
# not the win we are after.
WINDOWS = [
    ('30d', date(2025, 6, 1), date(2025, 7, 1)),
    ('1y', date(2025, 1, 1), date(2026, 1, 1)),
    ('all', date(2020, 8, 1), date.today()),
]

REPEATS = 3

# Both shapes are kept so one run shows the difference and old log files stay
# comparable. TOP_SPECIES_OLD is the pre-2026-09-15 statement; TOP_SPECIES_SQL
# mirrors what routes.stats_top_species builds today (anonymous scope, no
# location or biotope filter). If the route changes, change this too.
TOP_SPECIES_OLD = """
WITH ObservationConsensus AS (
    SELECT p.observation_id, i.species_id,
           COUNT(DISTINCT i.user_id) AS vote_count,
           MAX(i.quantity) AS max_quantity
    FROM identifications i JOIN photos p ON i.photo_id = p.id
    GROUP BY p.observation_id, i.species_id
),
RankedConsensus AS (
    SELECT observation_id, species_id,
           ROW_NUMBER() OVER (PARTITION BY observation_id
                              ORDER BY vote_count DESC, max_quantity DESC) AS rn
    FROM ObservationConsensus
)
SELECT s.id, COUNT(o.id) AS observation_count
FROM observations o
JOIN RankedConsensus rc ON o.id = rc.observation_id AND rc.rn = 1
JOIN species s ON s.id = rc.species_id
JOIN locations l ON o.location_id = l.id
WHERE o.status IN ('completed', 'archived')
  AND s.id > 0
  AND DATE(o.series_start_time) BETWEEN :start_date AND :end_date
  AND l.is_valid IS NOT FALSE
GROUP BY s.id
ORDER BY observation_count DESC
LIMIT 15
"""

TOP_SPECIES_SQL = """
WITH EligibleObservations AS (
    SELECT o.id
    FROM observations o
    JOIN locations l ON o.location_id = l.id
    WHERE o.status IN ('completed', 'archived')
      AND o.series_start_time >= CAST(:start_date AS date)
      AND o.series_start_time < (CAST(:end_date AS date) + 1)
      AND l.is_valid IS NOT FALSE
),
ObservationConsensus AS (
    SELECT p.observation_id, i.species_id,
           COUNT(DISTINCT i.user_id) AS vote_count,
           MAX(i.quantity) AS max_quantity
    FROM identifications i
    JOIN photos p ON i.photo_id = p.id
    JOIN EligibleObservations eo ON eo.id = p.observation_id
    GROUP BY p.observation_id, i.species_id
),
RankedConsensus AS (
    SELECT observation_id, species_id,
           ROW_NUMBER() OVER (PARTITION BY observation_id
                              ORDER BY vote_count DESC, max_quantity DESC,
                                       species_id DESC) AS rn
    FROM ObservationConsensus
)
SELECT s.id, COUNT(rc.observation_id) AS observation_count
FROM RankedConsensus rc
JOIN species s ON s.id = rc.species_id
WHERE rc.rn = 1 AND s.id > 0
GROUP BY s.id
ORDER BY observation_count DESC
LIMIT 15
"""


def dashboard_queries_new(session, start_date, end_date):
    """What routes.dashboard() runs today: the same seven counters, grouped by
    the base they share. Mirrors the route for an unfiltered admin scope."""
    valid = valid_location_id_subquery()
    photo_stats = (
        session.query(
            func.count(Photo.id),
            func.count(distinct(Observation.location_id)),
            func.count(func.distinct(func.date(Photo.captured_at))),
        ).join(Observation, Photo.observation_id == Observation.id)
        .join(Location, Observation.location_id == Location.id)
        .filter(Photo.captured_at.between(start_date, end_date))
        .filter(Location.id.in_(valid)))

    identified = (
        session.query(
            Observation.id.label('observation_id'),
            Identification.species_id.label('species_id'),
            Observation.status.in_(['completed', 'archived']).label('counts_for_species'),
        ).join(Photo, Photo.observation_id == Observation.id)
        .join(Identification, Identification.photo_id == Photo.id)
        .join(Location, Observation.location_id == Location.id)
        .filter(Photo.captured_at.between(start_date, end_date),
                Identification.species_id > 0)
        .filter(Location.id.in_(valid)).distinct().subquery())
    species_stats = session.query(
        func.count(distinct(identified.c.observation_id)),
        func.count(distinct(identified.c.species_id)).filter(
            identified.c.counts_for_species))

    pending = (
        session.query(func.count(Observation.id))
        .join(Location, Observation.location_id == Location.id)
        .filter(Observation.series_start_time.between(start_date,
                                                      end_date + timedelta(days=1)),
                ~Observation.photos.any(Photo.identifications.any()))
        .filter(Location.id.in_(valid)))

    contributors = (
        session.query(Identification.user_id,
                      func.count(distinct(Photo.observation_id)))
        .join(Photo, Identification.photo_id == Photo.id)
        .join(Observation, Photo.observation_id == Observation.id)
        .join(Location, Observation.location_id == Location.id)
        .filter(Photo.captured_at.between(start_date, end_date))
        .filter(Location.id.in_(valid))
        .group_by(Identification.user_id)
        .order_by(func.count(distinct(Photo.observation_id)).desc()).limit(10))

    return [
        ('A photos/loc/days', photo_stats),
        ('B obs/species', species_stats),
        ('5 pending', pending),
        ('7 contributors', contributors),
    ]


def dashboard_queries(session, start_date, end_date):
    """The seven separate aggregates the page ran before 2026-09-15."""
    valid = valid_location_id_subquery()
    return [
        ('1 photos',
         session.query(func.count(Photo.id)).join(Observation).join(Location)
         .filter(Photo.captured_at.between(start_date, end_date))
         .filter(Location.id.in_(valid))),

        ('2 locations',
         session.query(func.count(distinct(Observation.location_id)))
         .join(Photo).join(Location)
         .filter(Photo.captured_at.between(start_date, end_date))
         .filter(Location.id.in_(valid))),

        ('3 observations',
         session.query(func.count(func.distinct(Observation.id))).join(Photo)
         .join(Identification, Photo.id == Identification.photo_id).join(Location)
         .filter(Photo.captured_at.between(start_date, end_date),
                 Identification.species_id > 0)
         .filter(Location.id.in_(valid))),

        ('4 species count',
         session.query(func.count(distinct(Identification.species_id)))
         .join(Photo, Identification.photo_id == Photo.id)
         .join(Observation, Photo.observation_id == Observation.id).join(Location)
         .filter(Identification.species_id > 0,
                 Photo.captured_at.between(start_date, end_date),
                 Observation.status.in_(['completed', 'archived']))
         .filter(Location.id.in_(valid))),

        ('5 pending',
         session.query(func.count(Observation.id)).join(Location)
         .filter(Observation.series_start_time.between(start_date,
                                                       end_date + timedelta(days=1)),
                 ~Observation.photos.any(Photo.identifications.any()))
         .filter(Location.id.in_(valid))),

        ('6 capture days',
         session.query(func.count(func.distinct(func.date(Photo.captured_at))))
         .join(Observation).join(Location)
         .filter(Photo.captured_at.between(start_date, end_date))
         .filter(Location.id.in_(valid))),

        ('7 contributors',
         session.query(Identification.user_id,
                       func.count(distinct(Photo.observation_id)))
         .join(Photo, Identification.photo_id == Photo.id)
         .join(Observation).join(Location)
         .filter(Photo.captured_at.between(start_date, end_date))
         .filter(Location.id.in_(valid))
         .group_by(Identification.user_id)
         .order_by(func.count(distinct(Photo.observation_id)).desc()).limit(10)),
    ]


def _timed(fn):
    """Median of REPEATS runs — the first one pays for a cold cache."""
    samples = []
    for _ in range(REPEATS):
        started = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples), min(samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--explain', action='store_true',
                        help='also print EXPLAIN ANALYZE for the 30-day window')
    parser.add_argument('--out', metavar='PATH',
                        help='write the report to this file as well as stdout')
    args = parser.parse_args()

    lines = []

    def emit(line=''):
        print(line)
        lines.append(line)

    app = create_app()
    with app.app_context():
        session = get_ct_session()
        session.execute(text("SET statement_timeout='300s'"))

        engine_url = session.get_bind().url
        emit(f"database: {engine_url.host}:{engine_url.port}/{engine_url.database}")
        emit(f"date:     {date.today().isoformat()}")
        emit(f"repeats:  {REPEATS} (median reported, min in brackets)")
        emit()

        for label, start_date, end_date in WINDOWS:
            emit(f"── window {label} ({start_date} → {end_date}) "
                 f"{'─' * 28}")
            total = 0.0
            for name, query in dashboard_queries(session, start_date, end_date):
                runner = query.all if name.startswith('7') else query.scalar
                median, best = _timed(runner)
                total += median
                emit(f"  {name:18}{median * 1000:8.0f} ms  [{best * 1000:.0f}]")
            emit(f"  {'dashboard (old)':18}{total * 1000:8.0f} ms")

            total = 0.0
            for name, query in dashboard_queries_new(session, start_date, end_date):
                runner = query.all if name.startswith('7') else query.one
                median, best = _timed(runner)
                total += median
                emit(f"  {name:18}{median * 1000:8.0f} ms  [{best * 1000:.0f}]")
            emit(f"  {'dashboard':18}{total * 1000:8.0f} ms")

            bind = {'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()}
            for label, sql in (('top-species (old)', TOP_SPECIES_OLD),
                               ('top-species', TOP_SPECIES_SQL)):
                median, best = _timed(
                    lambda sql=sql: session.execute(text(sql), bind).fetchall())
                emit(f"  {label:18}{median * 1000:8.0f} ms  [{best * 1000:.0f}]")
            emit()

        if args.explain:
            emit("── EXPLAIN ANALYZE, 30-day window " + "─" * 28)
            start_date, end_date = WINDOWS[0][1], WINDOWS[0][2]
            for name, query in dashboard_queries(session, start_date, end_date):
                sql = str(query.statement.compile(
                    compile_kwargs={'literal_binds': True}))
                emit(f"\n  ## {name}")
                for row in session.execute(text(
                        "EXPLAIN (ANALYZE, COSTS OFF) " + sql)):
                    emit("  " + row[0])
            emit("\n  ## top-species")
            for row in session.execute(
                    text("EXPLAIN (ANALYZE, COSTS OFF) " + TOP_SPECIES_SQL),
                    {'start_date': start_date.isoformat(),
                     'end_date': end_date.isoformat()}):
                emit("  " + row[0])

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        print(f"\nwritten to {args.out}")


if __name__ == '__main__':
    sys.exit(main())
