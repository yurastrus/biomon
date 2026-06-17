#!/usr/bin/env python3
"""
check_translation_consistency.py
Діагностика та синхронізація англійських перекладів по всіх доменах biomon.

Використання:
    python scripts/check_translation_consistency.py [--out FILEPATH]
        Генерує Markdown-звіт (до stdout або у файл).

    python scripts/check_translation_consistency.py --apply-level1 [--changes-out FILEPATH]
        Застосовує рекомендовані EN до всіх Level-1 груп у .po файлах
        і перекомпіловує відповідні .mo.
        --changes-out  шлях до .md файлу зі списком змін (за замовч. stdout)

    python scripts/check_translation_consistency.py --level2-xlsx PATH
        Експортує Level-2 групи в Excel для ручної чистки.
"""

import sys
import os
import re
import argparse
import difflib
from collections import defaultdict
from io import open as io_open

# Babel у venv
BIOMON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_SITE = os.path.join(BIOMON_ROOT, "venv", "lib")
for entry in os.listdir(VENV_SITE):
    sp = os.path.join(VENV_SITE, entry, "site-packages")
    if os.path.isdir(sp) and sp not in sys.path:
        sys.path.insert(0, sp)

from babel.messages.pofile import read_po  # noqa: E402

# ─── Конфігурація доменів ────────────────────────────────────────────────────

DOMAINS = {
    "messages": os.path.join(BIOMON_ROOT, "translations", "en", "LC_MESSAGES", "messages.po"),
    "camera_traps": os.path.join(BIOMON_ROOT, "app", "camera_traps", "translations", "en", "LC_MESSAGES", "camera_traps.po"),
    "pam": os.path.join(BIOMON_ROOT, "app", "pam", "translations", "en", "LC_MESSAGES", "pam.po"),
    "sdm": os.path.join(BIOMON_ROOT, "app", "sdm", "translations", "en", "LC_MESSAGES", "sdm.po"),
}

FUZZY_MATCH_THRESHOLD = 0.88

# ─── Допоміжні функції ───────────────────────────────────────────────────────

_TRAIL_PUNCT = re.compile(r"[.,:;!?……]+$")
_MULTI_SPACE = re.compile(r"\s+")

def normalize(text: str) -> str:
    """casefold + strip + прибрати кінцеву пунктуацію + згорнути пробіли."""
    t = text.casefold().strip()
    t = _TRAIL_PUNCT.sub("", t)
    t = _MULTI_SPACE.sub(" ", t)
    return t


def msgstr_value(msgstr) -> str:
    """Витягти рядок зі str або dict (plural-форми)."""
    if isinstance(msgstr, dict):
        return " | ".join(str(v) for v in msgstr.values())
    return str(msgstr) if msgstr else ""


def best_translation(translations: list[str]) -> tuple[str, str]:
    """
    Повернути (найкращий переклад, пояснення).
    Більшість → беремо той; нема більшості → найкоротший (зазвичай стисліший).
    """
    from collections import Counter
    c = Counter(translations)
    top_val, top_count = c.most_common(1)[0]
    total = len(translations)
    if top_count > total / 2:
        return top_val, "більшість"
    # Нема більшості — найкоротший непорожній
    non_empty = [t for t in translations if t.strip()]
    if not non_empty:
        return translations[0], "єдиний варіант"
    shortest = min(non_empty, key=len)
    return shortest, "нема більшості → найстисліший варіант"

# ─── Зчитування .po ──────────────────────────────────────────────────────────

def _count_obsolete_raw(path: str) -> int:
    """Рахує obsolete-рядки (#~ msgid ...) безпосередньо у файлі."""
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
    Повертає dict з ключами:
      entries       : list[dict]  — {msgid, msgstr, domain, fuzzy, obsolete}
      missing       : bool        — файл не знайдено
      error         : str | None  — помилка парсингу
      obsolete_count: int         — кількість obsolete (#~) записів у файлі
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
        if not msg.id:  # заголовок каталогу
            continue
        msgid = str(msg.id) if not isinstance(msg.id, tuple) else str(msg.id[0])
        msgstr = msgstr_value(msg.string)
        fuzzy = msg.fuzzy  # babel Message має власний .fuzzy bool
        entries.append({
            "msgid": msgid,
            "msgstr": msgstr,
            "domain": domain,
            "fuzzy": fuzzy,
            "obsolete": False,  # babel не передає obsolete через read_po catalog
        })

    obsolete_count = _count_obsolete_raw(path)
    return {"entries": entries, "missing": False, "error": None, "obsolete_count": obsolete_count}

# ─── Аналіз ──────────────────────────────────────────────────────────────────

def analyze(all_entries: list[dict]):
    """
    Повертає:
      level1  : list[dict]  — точні дублі msgid з різними msgstr
      level2  : list[dict]  — подібні (за нормалізацією або SequenceMatcher)
      fuzzy   : list[dict]
      empty   : list[dict]
    """
    fuzzy_entries = [e for e in all_entries if e["fuzzy"]]
    # Активні (не fuzzy для основного аналізу; obsolete не приходять через babel)
    active = [e for e in all_entries if not e["fuzzy"]]
    empty_entries = [e for e in active if not e["msgstr"].strip()]

    # ── Рівень 1: точний msgid ──
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

    # ── Рівень 2: нормалізований або SequenceMatcher ──
    # Спочатку збираємо унікальні msgid (ті, що вже є в level1 — їх не дублюємо)
    level1_msgids = set(g["msgids"][0] for g in level1)
    unique_msgids = list(by_msgid.keys())

    # Нормалізований збіг
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
            # Однаковий переклад — не розбіжність
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

    # SequenceMatcher для решти
    remaining = [mid for mid in unique_msgids if normalize(mid) not in processed_norms or
                 len(norm_to_msgids[normalize(mid)]) == 1]
    # Групуємо через SM
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

    # Фільтруємо level2: залишаємо лише ті, де є реальні розбіжності msgstr
    # або де різні msgid зі схожим текстом (може бути однаковий переклад — теж цікаво)
    level2_filtered = []
    for g in level2:
        # Не дублюємо level1
        if all(m in level1_msgids for m in g["msgids"]):
            continue
        # Тільки якщо є хоча б 2 домени або різні msgstr
        domains_in_group = set(e["domain"] for e in g["entries"])
        unique_msgstrs = set(e["msgstr"] for e in g["entries"] if e["msgstr"].strip())
        if len(unique_msgstrs) > 1 or len(g["msgids"]) > 1:
            level2_filtered.append(g)

    # Сортування: більше розбіжностей вгорі
    level1.sort(key=lambda g: len(g["unique_msgstrs"]), reverse=True)
    level2_filtered.sort(key=lambda g: (len(g["unique_msgstrs"]), len(g["msgids"])), reverse=True)

    return level1, level2_filtered, fuzzy_entries, empty_entries

# ─── Генерація звіту ─────────────────────────────────────────────────────────

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

    a("# Звіт про узгодженість англійських перекладів — biomon")
    a("")
    a(f"Дата: 2026-06-16")
    a("")
    a("## Підсумок")
    a("")
    a("| Метрика | Значення |")
    a("|---|---|")
    a(f"| Неузгоджених груп Рівня 1 (точні дублі msgid з різними EN) | **{len(level1)}** |")
    a(f"| Неузгоджених/схожих груп Рівня 2 (нормалізовані/SM збіги) | **{len(level2)}** |")
    a(f"| Унікальних українських фраз (активних) | {total_unique_phrases} |")
    a(f"| Охоплені домени | {', '.join(covered_domains)} |")
    a(f"| Fuzzy записів (потребують перевірки) | {len(fuzzy_entries)} |")
    a(f"| Порожніх перекладів (активних, не fuzzy) | {len(empty_entries)} |")
    a(f"| Obsolete записів (#~) | {total_obsolete} |")
    a("")

    # Статус доменів
    a("### Статус доменів")
    a("")
    a("| Домен | Файл | Статус | Активних | Obsolete (#~) |")
    a("|---|---|---|---|---|")
    for domain, path in DOMAINS.items():
        meta = domain_meta[domain]
        if meta["missing"]:
            status = "ВІДСУТНІЙ"
            count = "—"
            obs = "—"
        elif meta["error"]:
            status = f"ПОМИЛКА: {meta['error'][:60]}"
            count = "—"
            obs = "—"
        else:
            status = "OK"
            count = str(len(meta["entries"]))
            obs = str(meta.get("obsolete_count", 0))
        rel_path = os.path.relpath(path, BIOMON_ROOT)
        a(f"| {domain} | `{rel_path}` | {status} | {count} | {obs} |")
    a("")

    # ─── Рівень 1 ───
    a("---")
    a("")
    a("## Рівень 1 — Точні дублі msgid з різними англійськими перекладами")
    a("")
    if not level1:
        a("*Розбіжностей не знайдено.*")
    else:
        a("| Українська фраза | Англ. варіанти (домен) | К-сть варіантів | Запропонований єдиний EN | Примітка |")
        a("|---|---|---|---|---|")
        for g in level1:
            msgid = g["msgids"][0]
            variants = []
            seen_strs = {}
            for e in g["entries"]:
                key = e["msgstr"] or "*(порожньо)*"
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
                suggested, reason = "*(нема перекладу)*", "всі порожні"

            # Екранування pipe у таблиці
            msgid_safe = msgid.replace("|", "\\|")
            suggested_safe = suggested.replace("|", "\\|")
            a(f"| {msgid_safe} | {variants_str} | {len(seen_strs)} | {suggested_safe} | {reason} |")
    a("")

    # ─── Рівень 2 ───
    a("---")
    a("")
    a("## Рівень 2 — Схожі msgid (нормалізовані збіги / SequenceMatcher ≥ 0.88)")
    a("")
    if not level2:
        a("*Схожих груп не знайдено.*")
    else:
        a("| Українські фрази | Англ. варіанти (домен) | К-сть варіантів | Запропонований єдиний EN | Примітка |")
        a("|---|---|---|---|---|")
        for g in level2:
            msgids_str = "<br>".join(m.replace("|", "\\|") for m in g["msgids"])
            seen_strs = {}
            for e in g["entries"]:
                key = e["msgstr"] or "*(порожньо)*"
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
                suggested, reason = "*(нема перекладу)*", "всі порожні"

            gtype = {"norm": "нормалізація", "sm": "SequenceMatcher"}.get(g.get("type", ""), g.get("type", ""))
            suggested_safe = suggested.replace("|", "\\|")
            a(f"| {msgids_str} | {variants_str} | {len(seen_strs)} | {suggested_safe} | {gtype} |")
    a("")

    # ─── Fuzzy ───
    a("---")
    a("")
    a("## Fuzzy записи (потребують перевірки перекладача)")
    a("")
    if not fuzzy_entries:
        a("*Fuzzy записів не знайдено.*")
    else:
        a("| Домен | Українська фраза | Поточний EN (fuzzy) |")
        a("|---|---|---|")
        for e in fuzzy_entries:
            mid = e["msgid"].replace("|", "\\|").replace("\n", " ")[:120]
            mstr = e["msgstr"].replace("|", "\\|").replace("\n", " ")[:120]
            a(f"| {e['domain']} | {mid} | {mstr} |")
    a("")

    # ─── Порожні ───
    a("---")
    a("")
    a("## Порожні переклади (активні, не fuzzy)")
    a("")
    if not empty_entries:
        a("*Порожніх перекладів не знайдено.*")
    else:
        a("| Домен | Українська фраза |")
        a("|---|---|")
        for e in empty_entries:
            mid = e["msgid"].replace("|", "\\|").replace("\n", " ")[:150]
            a(f"| {e['domain']} | {mid} |")
    a("")

    # ─── Obsolete ───
    a("---")
    a("")
    a("## Obsolete записи (#~)")
    a("")
    a(f"*Babel read_po не повертає obsolete записи через catalog — вони підраховані безпосередньо з файлів.*")
    a("")
    a("| Домен | Кількість obsolete |")
    a("|---|---|")
    for domain in DOMAINS:
        meta = domain_meta[domain]
        obs = meta.get("obsolete_count", "—")
        a(f"| {domain} | {obs} |")
    a("")

    return "\n".join(lines)

# ─── Main ─────────────────────────────────────────────────────────────────────

# ─── Режим A: застосувати Level-1 ────────────────────────────────────────────

def apply_level1(level1: list, dry_run: bool = False) -> list[dict]:
    """
    Для кожної Level-1 групи записати рекомендований EN у всі домени,
    де поточний msgstr відрізняється.

    Повертає список змін: [{domain, msgid, old_msgstr, new_msgstr}].
    Якщо dry_run=True — нічого не пише у файли.
    """
    # Babel write_po / read_po
    from babel.messages.pofile import read_po, write_po  # noqa: E402
    from io import open as io_open, BytesIO

    changes = []

    # Збираємо які домени потрібно переписати і що саме міняємо
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

    # Застосовуємо патчі домен за доменом
    for domain, patches in domain_patches.items():
        po_path = DOMAINS[domain]
        if not os.path.exists(po_path):
            print(f"[ПРОПУСК] {domain}: файл не знайдено: {po_path}", file=sys.stderr)
            continue

        # Зчитуємо каталог
        with io_open(po_path, "rb") as f:
            catalog = read_po(f)

        # Застосовуємо зміни
        patched_count = 0
        for msg in catalog:
            if not msg.id:
                continue
            msgid_str = str(msg.id) if not isinstance(msg.id, tuple) else str(msg.id[0])
            if msgid_str in patches:
                new_val = patches[msgid_str]
                if isinstance(msg.string, dict):
                    # plural — оновлюємо всі форми однаково (нетипово для EN, але безпечно)
                    msg.string = {k: new_val for k in msg.string}
                else:
                    msg.string = new_val
                patched_count += 1

        print(f"[ПАТЧ] {domain}: {patched_count} записів оновлено → {po_path}", file=sys.stderr)

        # Записуємо .po (sort_output=False — зберігаємо оригінальний порядок)
        with open(po_path, "wb") as f:
            write_po(f, catalog, sort_output=False)

        # Перекомпілюємо .mo через pybabel
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
            print(f"[ПОМИЛКА компіляції] {domain}: {result.stderr}", file=sys.stderr)
        else:
            print(f"[OK] {domain}: .mo скомпільовано", file=sys.stderr)

    return changes


def format_changes_report(changes: list[dict], level1_count: int) -> str:
    """Формує Markdown-звіт про застосовані зміни Level-1."""
    lines = []
    a = lines.append

    a("# Застосовані зміни Level-1 — biomon EN translations")
    a("")
    a(f"Дата: 2026-06-16")
    a("")

    domains_touched = sorted(set(c["domain"] for c in changes))
    a("## Підсумок")
    a("")
    a(f"- Груп Level-1 оброблено: **{level1_count}**")
    a(f"- Фактично змінено записів: **{len(changes)}**")
    a(f"- Торкнуті домени: {', '.join(domains_touched) if domains_touched else 'жодного'}")
    a("")

    if not changes:
        a("*Змін не внесено (вже узгоджено або нема Level-1 груп).*")
    else:
        a("## Деталі змін")
        a("")
        a("| Домен | Українська фраза (msgid) | Старий EN | Новий EN |")
        a("|---|---|---|---|")
        for c in sorted(changes, key=lambda x: (x["domain"], x["msgid"])):
            msgid_safe = c["msgid"].replace("|", "\\|").replace("\n", " ")[:120]
            old_safe = c["old_msgstr"].replace("|", "\\|").replace("\n", " ")[:120]
            new_safe = c["new_msgstr"].replace("|", "\\|").replace("\n", " ")[:120]
            a(f"| {c['domain']} | {msgid_safe} | {old_safe} | {new_safe} |")
    a("")

    return "\n".join(lines)


# ─── Режим B: Level-2 → Excel ─────────────────────────────────────────────────

def export_level2_xlsx(level2: list, output_path: str):
    """Експортує Level-2 групи у .xlsx для ручної чистки."""
    import pandas as pd

    rows = []
    for g in level2:
        msgids_str = "\n".join(g["msgids"])

        seen_strs: dict[str, list[str]] = {}
        for e in g["entries"]:
            key = e["msgstr"] or "*(порожньо)*"
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
            suggested, reason = "*(нема перекладу)*", "всі порожні"

        gtype = {"norm": "нормалізація", "sm": "SequenceMatcher"}.get(g.get("type", ""), g.get("type", ""))

        rows.append({
            "Українські фрази (нормалізована група)": msgids_str,
            "Англ. варіанти (з доменами)": variants_str,
            "К-сть варіантів": len(seen_strs),
            "Запропонований єдиний EN": suggested,
            "Примітка": gtype,
        })

    df = pd.DataFrame(rows)

    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Level2")

        workbook = writer.book
        worksheet = writer.sheets["Level2"]

        # Формати
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

        # Заголовки з форматом
        for col_num, col_name in enumerate(df.columns):
            worksheet.write(0, col_num, col_name, header_fmt)

        # Заморозити перший рядок
        worksheet.freeze_panes(1, 0)

        # Ширини колонок (підбір вручну)
        col_widths = [55, 55, 12, 45, 18]
        for i, width in enumerate(col_widths):
            worksheet.set_column(i, i, width)

        # Висота рядків і форматування клітинок
        for row_idx in range(len(df)):
            # Рядок трохи вищий для читабельності
            worksheet.set_row(row_idx + 1, 60)
            for col_idx, col_name in enumerate(df.columns):
                val = df.iloc[row_idx][col_name]
                if col_name == "К-сть варіантів":
                    worksheet.write(row_idx + 1, col_idx, val, num_fmt)
                else:
                    worksheet.write(row_idx + 1, col_idx, str(val) if val is not None else "", cell_fmt)

        # Автофільтр
        worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)

    print(f"[OK] Level-2 Excel збережено: {output_path} ({len(df)} рядків)", file=sys.stderr)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Перевірка та синхронізація англійських перекладів biomon")
    parser.add_argument("--out", help="Шлях до вихідного .md файлу (за замовчуванням — stdout)")
    parser.add_argument(
        "--apply-level1",
        action="store_true",
        dest="apply_level1",
        help="Застосувати рекомендовані EN Level-1 до .po і перекомпілювати .mo",
    )
    parser.add_argument(
        "--changes-out",
        dest="changes_out",
        help="Куди зберегти .md зі списком змін Level-1 (за замовч. stdout)",
    )
    parser.add_argument(
        "--level2-xlsx",
        dest="level2_xlsx",
        help="Шлях для збереження Level-2 Excel (.xlsx)",
    )
    args = parser.parse_args()

    domain_meta = {}
    all_entries = []

    for domain, path in DOMAINS.items():
        result = load_domain(domain, path)
        domain_meta[domain] = result
        if result["missing"]:
            print(f"[ПОПЕРЕДЖЕННЯ] Домен '{domain}': файл не знайдено: {path}", file=sys.stderr)
        elif result["error"]:
            print(f"[ПОМИЛКА] Домен '{domain}': {result['error']}", file=sys.stderr)
        else:
            all_entries.extend(result["entries"])
            print(f"[OK] {domain}: {len(result['entries'])} активних записів, "
                  f"{result['obsolete_count']} obsolete", file=sys.stderr)

    level1, level2, fuzzy_entries, empty_entries = analyze(all_entries)
    total_obsolete = sum(v.get("obsolete_count", 0) for v in domain_meta.values())

    print(f"\n[Аналіз] Рівень 1: {len(level1)} груп | Рівень 2: {len(level2)} груп | "
          f"Fuzzy: {len(fuzzy_entries)} | Порожніх: {len(empty_entries)} | "
          f"Obsolete: {total_obsolete}", file=sys.stderr)

    # ── Режим A: застосувати Level-1 ──
    if args.apply_level1:
        print("\n[Режим] --apply-level1: застосовую рекомендовані EN...", file=sys.stderr)
        changes = apply_level1(level1)
        report = format_changes_report(changes, len(level1))
        if args.changes_out:
            with open(args.changes_out, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"[Готово] Звіт змін збережено: {args.changes_out}", file=sys.stderr)
        else:
            print(report)
        return

    # ── Режим B: Level-2 → xlsx ──
    if args.level2_xlsx:
        export_level2_xlsx(level2, args.level2_xlsx)
        return

    # ── Стандартний режим: звіт ──
    report = format_report(domain_meta, level1, level2, fuzzy_entries, empty_entries, all_entries)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[Готово] Звіт збережено: {args.out}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
