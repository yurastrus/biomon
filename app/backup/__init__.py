# SPDX-License-Identifier: AGPL-3.0-only
"""Second-tier backup layer: human-readable CSV snapshots of module data.

The nightly `full_backup.sh` on the server already dumps every PostgreSQL
database and the GeoServer data dir. Those dumps only restore into a running
biomon; they are useless to a person who just needs the numbers. This package
adds a second, format-independent layer: per-institution CSV exports produced
by the very same queries the web UI serves on the data-export page.

Layout mirrors the dump layout, one directory per storage tier:

    <BACKUP_ROOT>/postgres/          ← full_backup.sh (existing)
    <BACKUP_ROOT>/geoserver/         ← full_backup.sh (existing)
    <BACKUP_ROOT>/phototraps_data/   ← this package
        Roztochya_Nature_Reserve/
            RSNR_ct_occurrence_2026-09-02.csv
            RSNR_ct_occurrence_2026-09-01.csv
            manifest.json

Modules:
    storage      — pluggable storage backends (local dir, rclone remote).
    ct_csv       — the camera-traps exporter itself.
"""
