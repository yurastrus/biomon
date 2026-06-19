#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""
check_translation_consistency.py
Diagnose and synchronize English translations across all biomon domains.

Usage:
    python scripts/check_translation_consistency.py [--out FILEPATH]
        Generate a Markdown report (to stdout or a file).

    python scripts/check_translation_consistency.py --apply-level1 [--changes-out FILEPATH]
        Apply the recommended EN to all Level-1 groups in the .po files
        and recompile the matching .mo files.
        --changes-out  path to a .md file with the list of changes (default: stdout)

    python scripts/check_translation_consistency.py --level2-xlsx PATH
        Export Level-2 groups to Excel for manual cleanup.
"""

import sys
import os
import re
import argparse
import difflib
from collections import defaultdict
from io import open as io_open

# Babel from venv
BIOMON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_SITE = os.path.join(BIOMON_ROOT, "venv", "lib")
for entry in os.listdir(VENV_SITE):
    sp = os.path.join(VENV_SITE, entry, "site-packages")
    if os.path.isdir(sp) and sp not in sys.path:
        sys.path.insert(0, sp)

from babel.messages.pofile import read_po  # noqa: E402

# ─── Domain configuration ────────────────────────────────────────────────────

DOMAINS = {
    "messages": os.path.join(BIOMON_ROOT, "translations", "en", "LC_MESSAGES", "messages.po"),
    "camera_traps": os.path.join(BIOMON_ROOT, "app", "camera_traps", "translations", "en", "LC_MESSAGES", "camera_traps.po"),
    "pam": os.path.join(BIOMON_ROOT, "app", "pam", "translations", "en", "LC_MESSAGES", "pam.po"),
    "sdm": os.path.join(BIOMON_ROOT, "app", "sdm", "translations", "en", "LC_MESSAGES", "sdm.po"),
}

FUZZY_MATCH_THRESHOLD = 0.88

# ─── Helper functions ───────────────────────────────────────────────────

_TRAIL_PUNCT = re.compile(r"[.,:;!?……]+$")
_MULTI_SPACE = re.compile(r"\s+")

def normalize(text: str) -> str:
    """casefold + strip + remove trailing punctuation + collapse whitespace."""
    t = text.casefold().strip()
    t = _TRAIL_PUNCT.sub("", t)
    t = _MULTI_SPACE.sub(" ", t)
    return t


def msgstr_value(msgstr) -> str:
    """Extract a string from a str or dict (plural forms)."""
    if isinstance(msgstr, dict):
        return " | ".join(str(v) for v in msgstr.values())
    return str(msgstr) if msgstr else ""


def best_translation(translations: list[str]) -> tuple[str, str]:
    """
    Return (best translation, explanation).
    Majority → use it; no majority → shortest (usually the most concise).
    """
    from collections import Counter
    c = Counter(translations)
    top_val, top_count = c.most_common(1)[0]
    total = len(translations)
    if top_count > total / 2:
        return top_val, "majority"
    # No majority — shortest non-empty
    non_empty = [t for t in translations if t.strip()]
    if not non_empty:
        return translations[0], "only variant"
    shortest = min(non_empty, key=len)
    return shortest, "no majority → most concise variant"

# ─── Reading .po ──────────────────────────────────────────────────────────

def _count_obsolete_raw(path: str) -> int:
    """Count obsolete lines (#~ msgid ...) directly in the file."""
    count = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("#~ msgid ") and line.strip() != '#~ msgid ""':
                    count += 1
    except Exception:
        pass
    return count


def load_domain(domain: str, path: str):
    """
    Return a dict with keys:
      entries       : list[dict]  — {msgid, msgstr, domain, fuzzy, obsolete}
      missing       : bool        — file not found
      error         : str | None  — parse error
      obsolete_count: int         — number of obsolete (#~) entries in the file
    """
    if not os.path.exists(path):
        return {"entries": [], "missing": True, "error": None, "obsolete_count": 0}
    try:
        with io_open(path, "rb") as f:
            catalog = read_po(f)
    except Exception as exc:
        return {"entries": [], "missing": False, "error": str(exc), "obsolete_count": 0}

    entries = []
    for msg in catalog:
        if not msg.id:  # catalog header
            continue
        msgid = str(msg.id) if not isinstance(msg.id, tuple) else str(msg.id[0])
        msgstr = msgstr_value(msg.string)
        fuzzy = msg.fuzzy  # babel Message has its own .fuzzy bool
        entries.append({
            "msgid": msgid,
            "msgstr": msgstr,
            "domain": domain,
            "fuzzy": fuzzy,
            "obsolete": False,  # babel does not expose obsolete via the read_po catalog
        })

    obsolete_count = _count_obsolete_raw(path)
    return {"entries": entries, "missing": False, "error": None, "obsolete_count": obsolete_count}

# ─── Analysis ──────────────────────────────────────────────────────────────

def analyze(all_entries: list[dict]):
    """
    Return:
      level1  : list[dict]  — exact msgid duplicates with different msgstr
      level2  : list[dict]  — similar (by normalization or SequenceMatcher)
      fuzzy   : list[dict]
      empty   : list[dict]
    """
    fuzzy_entries = [e for e in all_entries if e["fuzzy"]]
    # Active (non-fuzzy for the main analysis; obsolete do not come through babel)
    active = [e for e in all_entries if not e["fuzzy"]]
    empty_entries = [e for e in active if not e["msgstr"].strip()]

    # ── Level 1: exact msgid ──
    by_msgid: dict[str, list[dict]] = defaultdict(list)
    for e in active:
        by_msgid[e["msgid"]].append(e)

    level1 = []
    for msgid, entries in by_msgid.items():
        if len(entries) < 2:
            continue
        unique_msgstrs = set(e["msgstr"] for e in entries)
        if len(unique_msgstrs) > 1:
            level1.append({
                "msgids": [msgid],
                "entries": entries,
                "unique_msgstrs": unique_msgstrs,
                "type": "exact",
            })

    # ── Level 2: normalized or SequenceMatcher ──
    # First collect unique msgids (those already in level1 — we do not duplicate them)
    level1_msgids = set(g["msgids"][0] for g in level1)
    unique_msgids = list(by_msgid.keys())

    # Normalized match
    norm_to_msgids: dict[str, list[str]] = defaultdict(list)
    for mid in unique_msgids:
        norm_to_msgids[normalize(mid)].append(mid)

    level2 = []
    processed_norms = set()
    for norm_key, mids in norm_to_msgids.items():
        if len(mids) < 2:
            continue
        all_entries_group = []
        for mid in mids:
            all_entries_group.extend(by_msgid[mid])
        unique_msgstrs = set(e["msgstr"] for e in all_entries_group)
        if len(unique_msgstrs) <= 1 and all(m not in level1_msgids for m in mids):
            # Same translation — not a discrepancy
            pass
        group = {
            "msgids": mids,
            "entries": all_entries_group,
            "unique_msgstrs": unique_msgstrs,
            "type": "norm",
            "norm_key": norm_key,
        }
        level2.append(group)
        processed_norms.add(norm_key)

    # SequenceMatcher for the rest
    remaining = [mid for mid in unique_msgids if normalize(mid) not in processed_norms or
                 len(norm_to_msgids[normalize(mid)]) == 1]
    # Group via SM
    sm_groups = []
    used = set()
    for i, mid_a in enumerate(remaining):
        if mid_a in used:
            continue
        group_mids = [mid_a]
        for mid_b in remaining[i + 1:]:
            if mid_b in used:
                continue
            ratio = difflib.SequenceMatcher(None, normalize(mid_a), normalize(mid_b)).ratio()
            if ratio >= FUZZY_MATCH_THRESHOLD:
                group_mids.append(mid_b)
        if len(group_mids) > 1:
            all_entries_group = []
            for mid in group_mids:
                all_entries_group.extend(by_msgid[mid])
            unique_msgstrs = set(e["msgstr"] for e in all_entries_group)
            sm_groups.append({
                "msgids": group_mids,
                "entries": all_entries_group,
                "unique_msgstrs": unique_msgstrs,
                "type": "sm",
            })
            for mid in group_mids:
                used.add(mid)

    level2.extend(sm_groups)

    # Filter level2: keep only groups with real msgstr discrepancies
    # or with different msgids that have similar text (same translation is also of interest)
    level2_filtered = []
    for g in level2:
        # Do not duplicate level1
        if all(m in level1_msgids for m in g["msgids"]):
            continue
        # Only if there are at least 2 domains or different msgstrs
        domains_in_group = set(e["domain"] for e in g["entries"])
        unique_msgstrs = set(e["msgstr"] for e in g["entries"] if e["msgstr"].strip())
        if len(unique_msgstrs) > 1 or len(g["msgids"]) > 1:
            level2_filtered.append(g)

    # Sorting: more discrepancies on top
    level1.sort(key=lambda g: len(g["unique_msgstrs"]), reverse=True)
    level2_filtered.sort(key=lambda g: (len(g["unique_msgstrs"]), len(g["msgids"])), reverse=True)

    return level1, level2_filtered, fuzzy_entries, empty_entries

# ─── Report generation ─────────────────────────────────────────────────────

def format_report(
    domain_meta: dict,
    level1: list,
    level2: list,
    fuzzy_entries: list,
    empty_entries: list,
    all_entries: list,
) -> str:
    lines = []
    a = lines.append

    covered_domains = sorted(set(e["domain"] for e in all_entries))
    total_unique_phrases = len(set(e["msgid"] for e in all_entries))
    total_obsolete = sum(v.get("obsolete_count", 0) for v in domain_meta.values())

    a("# English translation consistency report — biomon")
    a("")
    a(f"Date: 2026-06-16")
    a("")
    a("## Summary")
    a("")
    a("| Metric | Value |")
    a("|---|---|")
    a(f"| Inconsistent Level-1 groups (exact msgid duplicates with different EN) | **{len(level1)}** |")
    a(f"| Inconsistent/similar Level-2 groups (normalized/SM matches) | **{len(level2)}** |")
    a(f"| Unique Ukrainian phrases (active) | {total_unique_phrases} |")
    a(f"| Covered domains | {', '.join(covered_domains)} |")
    a(f"| Fuzzy entries (need review) | {len(fuzzy_entries)} |")
    a(f"| Empty translations (active, non-fuzzy) | {len(empty_entries)} |")
    a(f"| Obsolete entries (#~) | {total_obsolete} |")
    a("")

    # Domain status
    a("### Domain status")
    a("")
    a("| Domain | File | Status | Active | Obsolete (#~) |")
    a("|---|---|---|---|---|")
    for domain, path in DOMAINS.items():
        meta = domain_meta[domain]
        if meta["missing"]:
            status = "MISSING"
            count = "—"
            obs = "—"
        elif meta["error"]:
            status = f"ERROR: {meta['error'][:60]}"
            count = "—"
            obs = "—"
        else:
            status = "OK"
            count = str(len(meta["entries"]))
            obs = str(meta.get("obsolete_count", 0))
        rel_path = os.path.relpath(path, BIOMON_ROOT)
        a(f"| {domain} | `{rel_path}` | {status} | {count} | {obs} |")
    a("")

    # ─── Level 1 ───
    a("---")
    a("")
    a("## Level 1 — Exact msgid duplicates with different English translations")
    a("")
    if not level1:
        a("*No discrepancies found.*")
    else:
        a("| Ukrainian phrase | EN variants (domain) | Variant count | Suggested single EN | Note |")
        a("|---|---|---|---|---|")
        for g in level1:
            msgid = g["msgids"][0]
            variants = []
            seen_strs = {}
            for e in g["entries"]:
                key = e["msgstr"] or "*(empty)*"
                if key not in seen_strs:
                    seen_strs[key] = []
                seen_strs[key].append(e["domain"])
            for msgstr_val, domains in seen_strs.items():
                variants.append(f'`{msgstr_val}` ({", ".join(domains)})')
            variants_str = "<br>".join(variants)

            non_empty = [e["msgstr"] for e in g["entries"] if e["msgstr"].strip()]
            if non_empty:
                suggested, reason = best_translation(non_empty)
            else:
                suggested, reason = "*(no translation)*", "all empty"

            # Escape pipe in the table
            msgid_safe = msgid.replace("|", "\\|")
            suggested_safe = suggested.replace("|", "\\|")
            a(f"| {msgid_safe} | {variants_str} | {len(seen_strs)} | {suggested_safe} | {reason} |")
    a("")

    # ─── Level 2 ───
    a("---")
    a("")
    a("## Level 2 — Similar msgids (normalized matches / SequenceMatcher ≥ 0.88)")
    a("")
    if not level2:
        a("*No similar groups found.*")
    else:
        a("| Ukrainian phrases | EN variants (domain) | Variant count | Suggested single EN | Note |")
        a("|---|---|---|---|---|")
        for g in level2:
            msgids_str = "<br>".join(m.replace("|", "\\|") for m in g["msgids"])
            seen_strs = {}
            for e in g["entries"]:
                key = e["msgstr"] or "*(empty)*"
                if key not in seen_strs:
                    seen_strs[key] = []
                seen_strs[key].append(e["domain"])
            variants = []
            for msgstr_val, domains in seen_strs.items():
                variants.append(f'`{msgstr_val}` ({", ".join(sorted(set(domains)))})')
            variants_str = "<br>".join(variants)

            non_empty = [e["msgstr"] for e in g["entries"] if e["msgstr"].strip()]
            if non_empty:
                suggested, reason = best_translation(non_empty)
            else:
                suggested, reason = "*(no translation)*", "all empty"

            gtype = {"norm": "normalization", "sm": "SequenceMatcher"}.get(g.get("type", ""), g.get("type", ""))
            suggested_safe = suggested.replace("|", "\\|")
            a(f"| {msgids_str} | {variants_str} | {len(seen_strs)} | {suggested_safe} | {gtype} |")
    a("")

    # ─── Fuzzy ───
    a("---")
    a("")
    a("## Fuzzy entries (need translator review)")
    a("")
    if not fuzzy_entries:
        a("*No fuzzy entries found.*")
    else:
        a("| Domain | Ukrainian phrase | Current EN (fuzzy) |")
        a("|---|---|---|")
        for e in fuzzy_entries:
            mid = e["msgid"].replace("|", "\\|").replace("\n", " ")[:120]
            mstr = e["msgstr"].replace("|", "\\|").replace("\n", " ")[:120]
            a(f"| {e['domain']} | {mid} | {mstr} |")
    a("")

    # ─── Empty ───
    a("---")
    a("")
    a("## Empty translations (active, non-fuzzy)")
    a("")
    if not empty_entries:
        a("*No empty translations found.*")
    else:
        a("| Domain | Ukrainian phrase |")
        a("|---|---|")
        for e in empty_entries:
            mid = e["msgid"].replace("|", "\\|").replace("\n", " ")[:150]
            a(f"| {e['domain']} | {mid} |")
    a("")

    # ─── Obsolete ───
    a("---")
    a("")
    a("## Obsolete entries (#~)")
    a("")
    a(f"*Babel read_po does not return obsolete entries via the catalog — they are counted directly from the files.*")
    a("")
    a("| Domain | Obsolete count |")
    a("|---|---|")
    for domain in DOMAINS:
        meta = domain_meta[domain]
        obs = meta.get("obsolete_count", "—")
        a(f"| {domain} | {obs} |")
    a("")

    return "\n".join(lines)

# ─── Main ─────────────────────────────────────────────────────────────────────

# ─── Mode A: apply Level-1 ────────────────────────────────────────────

def apply_level1(level1: list, dry_run: bool = False) -> list[dict]:
    """
    For each Level-1 group, write the recommended EN into every domain
    whose current msgstr differs.

    Returns the list of changes: [{domain, msgid, old_msgstr, new_msgstr}].
    If dry_run=True — nothing is written to files.
    """
    # Babel write_po / read_po
    from babel.messages.pofile import read_po, write_po  # noqa: E402
    from io import open as io_open, BytesIO

    changes = []

    # Collect which domains need rewriting and exactly what to change
    # domain -> {msgid -> new_msgstr}
    domain_patches: dict[str, dict[str, str]] = defaultdict(dict)

    for g in level1:
        msgid = g["msgids"][0]
        non_empty = [e["msgstr"] for e in g["entries"] if e["msgstr"].strip()]
        if not non_empty:
            continue
        suggested, _reason = best_translation(non_empty)

        for e in g["entries"]:
            if e["msgstr"] != suggested:
                domain_patches[e["domain"]][msgid] = suggested
                changes.append({
                    "domain": e["domain"],
                    "msgid": msgid,
                    "old_msgstr": e["msgstr"],
                    "new_msgstr": suggested,
                })

    if dry_run or not changes:
        return changes

    # Apply patches domain by domain
    for domain, patches in domain_patches.items():
        po_path = DOMAINS[domain]
        if not os.path.exists(po_path):
            print(f"[SKIP] {domain}: file not found: {po_path}", file=sys.stderr)
            continue

        # Read the catalog
        with io_open(po_path, "rb") as f:
            catalog = read_po(f)

        # Apply changes
        patched_count = 0
        for msg in catalog:
            if not msg.id:
                continue
            msgid_str = str(msg.id) if not isinstance(msg.id, tuple) else str(msg.id[0])
            if msgid_str in patches:
                new_val = patches[msgid_str]
                if isinstance(msg.string, dict):
                    # plural — update all forms identically (atypical for EN, but safe)
                    msg.string = {k: new_val for k in msg.string}
                else:
                    msg.string = new_val
                patched_count += 1

        print(f"[PATCH] {domain}: {patched_count} entries updated → {po_path}", file=sys.stderr)

        # Write .po (sort_output=False — keep the original order)
        with open(po_path, "wb") as f:
            write_po(f, catalog, sort_output=False)

        # Recompile .mo via pybabel
        import subprocess
        translations_dir = os.path.dirname(os.path.dirname(os.path.dirname(po_path)))
        cmd = [
            os.path.join(BIOMON_ROOT, "venv", "bin", "pybabel"),
            "compile",
            "-d", translations_dir,
            "-l", "en",
            "-D", domain,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[COMPILE ERROR] {domain}: {result.stderr}", file=sys.stderr)
        else:
            print(f"[OK] {domain}: .mo compiled", file=sys.stderr)

    return changes


def format_changes_report(changes: list[dict], level1_count: int) -> str:
    """Build a Markdown report of the applied Level-1 changes."""
    lines = []
    a = lines.append

    a("# Applied Level-1 changes — biomon EN translations")
    a("")
    a(f"Date: 2026-06-16")
    a("")

    domains_touched = sorted(set(c["domain"] for c in changes))
    a("## Summary")
    a("")
    a(f"- Level-1 groups processed: **{level1_count}**")
    a(f"- Entries actually changed: **{len(changes)}**")
    a(f"- Domains touched: {', '.join(domains_touched) if domains_touched else 'none'}")
    a("")

    if not changes:
        a("*No changes made (already consistent or no Level-1 groups).*")
    else:
        a("## Change details")
        a("")
        a("| Domain | Ukrainian phrase (msgid) | Old EN | New EN |")
        a("|---|---|---|---|")
        for c in sorted(changes, key=lambda x: (x["domain"], x["msgid"])):
            msgid_safe = c["msgid"].replace("|", "\\|").replace("\n", " ")[:120]
            old_safe = c["old_msgstr"].replace("|", "\\|").replace("\n", " ")[:120]
            new_safe = c["new_msgstr"].replace("|", "\\|").replace("\n", " ")[:120]
            a(f"| {c['domain']} | {msgid_safe} | {old_safe} | {new_safe} |")
    a("")

    return "\n".join(lines)


# ─── Mode B: Level-2 → Excel ─────────────────────────────────────────────────

def export_level2_xlsx(level2: list, output_path: str):
    """Export Level-2 groups to .xlsx for manual cleanup."""
    import pandas as pd

    rows = []
    for g in level2:
        msgids_str = "\n".join(g["msgids"])

        seen_strs: dict[str, list[str]] = {}
        for e in g["entries"]:
            key = e["msgstr"] or "*(empty)*"
            if key not in seen_strs:
                seen_strs[key] = []
            seen_strs[key].append(e["domain"])
        variants_parts = []
        for msgstr_val, domains in seen_strs.items():
            variants_parts.append(f'{msgstr_val} ({", ".join(sorted(set(domains)))})')
        variants_str = "\n".join(variants_parts)

        non_empty = [e["msgstr"] for e in g["entries"] if e["msgstr"].strip()]
        if non_empty:
            suggested, reason = best_translation(non_empty)
        else:
            suggested, reason = "*(no translation)*", "all empty"

        gtype = {"norm": "normalization", "sm": "SequenceMatcher"}.get(g.get("type", ""), g.get("type", ""))

        rows.append({
            "Ukrainian phrases (normalized group)": msgids_str,
            "EN variants (with domains)": variants_str,
            "Variant count": len(seen_strs),
            "Suggested single EN": suggested,
            "Note": gtype,
        })

    df = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Level2")

        workbook = writer.book
        worksheet = writer.sheets["Level2"]

        # Formats
        header_fmt = workbook.add_format({
            "bold": True,
            "bg_color": "#D7E4BC",
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
        })
        cell_fmt = workbook.add_format({
            "text_wrap": True,
            "valign": "top",
            "border": 1,
        })
        num_fmt = workbook.add_format({
            "border": 1,
            "valign": "vcenter",
            "align": "center",
        })

        # Headers with format
        for col_num, col_name in enumerate(df.columns):
            worksheet.write(0, col_num, col_name, header_fmt)

        # Freeze the first row
        worksheet.freeze_panes(1, 0)

        # Column widths (hand-tuned)
        col_widths = [55, 55, 12, 45, 18]
        for i, width in enumerate(col_widths):
            worksheet.set_column(i, i, width)

        # Row height and cell formatting
        for row_idx in range(len(df)):
            # Slightly taller rows for readability
            worksheet.set_row(row_idx + 1, 60)
            for col_idx, col_name in enumerate(df.columns):
                val = df.iloc[row_idx][col_name]
                if col_name == "Variant count":
                    worksheet.write(row_idx + 1, col_idx, val, num_fmt)
                else:
                    worksheet.write(row_idx + 1, col_idx, str(val) if val is not None else "", cell_fmt)

        # Autofilter
        worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)

    print(f"[OK] Level-2 Excel saved: {output_path} ({len(df)} rows)", file=sys.stderr)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Check and synchronize biomon English translations")
    parser.add_argument("--out", help="Path to the output .md file (default: stdout)")
    parser.add_argument(
        "--apply-level1",
        action="store_true",
        dest="apply_level1",
        help="Apply the recommended EN Level-1 to .po and recompile .mo",
    )
    parser.add_argument(
        "--changes-out",
        dest="changes_out",
        help="Where to save the .md with the Level-1 change list (default: stdout)",
    )
    parser.add_argument(
        "--level2-xlsx",
        dest="level2_xlsx",
        help="Path to save the Level-2 Excel (.xlsx)",
    )
    args = parser.parse_args()

    domain_meta = {}
    all_entries = []

    for domain, path in DOMAINS.items():
        result = load_domain(domain, path)
        domain_meta[domain] = result
        if result["missing"]:
            print(f"[WARNING] Domain '{domain}': file not found: {path}", file=sys.stderr)
        elif result["error"]:
            print(f"[ERROR] Domain '{domain}': {result['error']}", file=sys.stderr)
        else:
            all_entries.extend(result["entries"])
            print(f"[OK] {domain}: {len(result['entries'])} active entries, "
                  f"{result['obsolete_count']} obsolete", file=sys.stderr)

    level1, level2, fuzzy_entries, empty_entries = analyze(all_entries)
    total_obsolete = sum(v.get("obsolete_count", 0) for v in domain_meta.values())

    print(f"\n[Analysis] Level 1: {len(level1)} groups | Level 2: {len(level2)} groups | "
          f"Fuzzy: {len(fuzzy_entries)} | Empty: {len(empty_entries)} | "
          f"Obsolete: {total_obsolete}", file=sys.stderr)

    # ── Mode A: apply Level-1 ──
    if args.apply_level1:
        print("\n[Mode] --apply-level1: applying recommended EN...", file=sys.stderr)
        changes = apply_level1(level1)
        report = format_changes_report(changes, len(level1))
        if args.changes_out:
            with open(args.changes_out, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"[Done] Change report saved: {args.changes_out}", file=sys.stderr)
        else:
            print(report)
        return

    # ── Mode B: Level-2 → xlsx ──
    if args.level2_xlsx:
        export_level2_xlsx(level2, args.level2_xlsx)
        return

    # ── Standard mode: report ──
    report = format_report(domain_meta, level1, level2, fuzzy_entries, empty_entries, all_entries)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[Done] Report saved: {args.out}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
