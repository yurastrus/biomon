# SPDX-License-Identifier: AGPL-3.0-only
"""
Backfill segments.recording_id / detection_id for legacy segments (uploaded via
the old ZIP path, before migration 0002).

MUST be run once before the automated sample-upload page is used, otherwise its
dedup (NOT EXISTS on segments.detection_id) can't skip already-uploaded
detections.

Run from the project root:
    venv/Scripts/python -m scripts.backfill_pam_segment_links --report   # dry-run
    venv/Scripts/python -m scripts.backfill_pam_segment_links --apply    # write

Two-tier matching (see app/pam/pam_segment_sampling.backfill_segment_links):
    tier 1 — new-format filename (has _secN) → exact (recording, species, start_s)
    tier 2 — legacy filename (no _secN) → heuristic (recording, species,
             round(confidence, 3)); collisions are reported and left untouched.

Idempotent: only touches rows with detection_id IS NULL. Safe to re-run.
Inspect the collision count from --report before deciding whether to add a
UNIQUE(detection_id) index.
"""
import argparse

from app import create_app
from app.pam.pam_segment_sampling import backfill_segment_links


def main():
    ap = argparse.ArgumentParser(description="Backfill segment→detection links.")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument('--report', action='store_true',
                     help="Dry-run: compute + print stats, write nothing.")
    grp.add_argument('--apply', action='store_true',
                     help="Write recording_id/detection_id to matched segments.")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        stats = backfill_segment_links(report_only=args.report)

    print()
    print("=== Segment link backfill ===")
    print(f"  mode:                {'REPORT (dry-run)' if args.report else 'APPLY'}")
    print(f"  scanned:             {stats['scanned']}")
    print(f"  tier-1 linked (exact):     {stats['tier1_linked']}")
    print(f"  tier-2 linked (heuristic): {stats['tier2_linked']}")
    print(f"  total linked:        {stats['total_linked']}")
    print(f"  tier-2 collisions:   {stats['tier2_collisions']}  (left NULL)")
    print(f"  no matching detection: {stats['no_detection']}")
    print(f"  recording unmatched: {stats['recording_unmatched']}")
    print(f"  unparseable name:    {stats['unparseable']}")
    if stats['collision_samples']:
        print("  collision samples (up to 25):")
        for c in stats['collision_samples']:
            print(f"    - seg {c['segment_id']}: {c['filename']} → {c['candidates']} detections")
    if args.report:
        print()
        print("Dry-run only. Re-run with --apply to write.")


if __name__ == '__main__':
    main()
