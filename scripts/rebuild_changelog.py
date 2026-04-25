#!/usr/bin/env python3
"""
rebuild_changelog.py — Rekonstrukcija istorije promena iz Git historije
-----------------------------------------------------------------------
Prolazi kroz sve commitove koji su menjali data/towers.js,
poredi ih i gradi changelog.json sa svim promenama iz prošlosti.

Pokretanje (iz root foldera repoa):
    python3 scripts/rebuild_changelog.py

Zahtevi:
    - Git mora biti instaliran i dostupan u PATH-u
    - Mora se pokrenuti iz root foldera repoa (gde je .git folder)
"""

import subprocess
import json
import os
import sys
import re
from datetime import datetime

DATA_DIR       = os.path.join(os.path.dirname(__file__), "..", "data")
CHANGELOG_FILE = os.path.join(DATA_DIR, "changelog.json")
TOWER_IDS_FILE = os.path.join(DATA_DIR, ".prev_tower_ids.json")

OP_NAMES = ["MTS", "A1", "Yettel"]


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip()


def get_commits_for_file(filepath):
    """Vrati listu (hash, datum) svih commitova koji su dirali filepath, od najstarijeg."""
    out = run(["git", "log", "--follow", "--format=%H %ai", "--", filepath])
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        if len(parts) >= 2:
            commit_hash = parts[0]
            # Parse ISO date: 2026-04-12 20:15:33 +0200
            date_str = parts[1] + " " + parts[2] if len(parts) > 2 else parts[1]
            try:
                dt = datetime.fromisoformat(date_str.strip().rsplit(" ", 1)[0])
                date_fmt = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_fmt = parts[1]
            commits.append((commit_hash, date_fmt))
    commits.reverse()  # od najstarijeg
    return commits


def get_towers_js_at(commit_hash, filepath):
    """Preuzmi sadržaj fajla na određenom commitu."""
    return run(["git", "show", f"{commit_hash}:{filepath}"])


def parse_towers_js(content):
    """
    Parsira towers.js i vraća {op_idx: set(tower_ids)}.
    Format: const LOCS=[...];
    Svaki element: [lon, lat, addr, tech_mask, op_mask, [[op_idx, [[tid, name, secs], ...]], ...]]
    """
    if not content or "const LOCS=" not in content:
        return {}

    try:
        m = re.search(r'const LOCS=(\[.*\]);', content, re.DOTALL)
        if not m:
            return {}
        locs = json.loads(m.group(1))
    except Exception as e:
        print(f"    Greška pri parsiranju: {e}")
        return {}

    from collections import defaultdict
    ids = defaultdict(set)
    for loc in locs:
        if len(loc) < 6:
            continue
        ops_data = loc[5]
        for op_entry in ops_data:
            op_idx = op_entry[0]
            towers = op_entry[1]
            for tower in towers:
                tid = tower[0]
                ids[op_idx].add(tid)

    return ids


def diff_ids(old_ids, new_ids):
    """Vrati {op_idx: set_novih} — tower IDs koji su NOVI u new_ids."""
    added = {}
    for op_idx in range(3):
        old_set = old_ids.get(op_idx, set())
        new_set = new_ids.get(op_idx, set())
        diff = new_set - old_set
        if diff:
            added[op_idx] = diff
    return added


def build_details(added, locs_content):
    """Izvuci detalje (naziv, adresa, tehno) za nove tower ID-jeve."""
    if not locs_content:
        return []

    try:
        m = re.search(r'const LOCS=(\[.*\]);', locs_content, re.DOTALL)
        if not m:
            return []
        locs = json.loads(m.group(1))
    except Exception:
        return []

    details = []
    for op_idx, tower_set in sorted(added.items()):
        op_name = OP_NAMES[op_idx]
        for tid in list(tower_set)[:20]:
            for loc in locs:
                if len(loc) < 6:
                    continue
                for op_entry in loc[5]:
                    if op_entry[0] != op_idx:
                        continue
                    for tower in op_entry[1]:
                        if tower[0] != tid:
                            continue
                        techs = sorted({s[2] for s in tower[2]})
                        details.append({
                            "op":   op_name,
                            "name": str(tower[1])[:100],
                            "tech": techs,
                            "addr": str(loc[2])[:150],
                        })
                        break
        if len(details) >= 50:
            break

    return details[:50]


def main():
    # Proveri da smo u root folderu repoa
    if not os.path.exists(".git"):
        print("GREŠKA: Pokreni skriptu iz root foldera repoa (gde se nalazi .git folder)!")
        print("  cd mapa && python3 scripts/rebuild_changelog.py")
        sys.exit(1)

    towers_path = "data/towers.js"

    print("Tražim commitove koji su menjali data/towers.js...")
    commits = get_commits_for_file(towers_path)

    if not commits:
        print("Nema commitova za data/towers.js!")
        sys.exit(1)

    print(f"Pronađeno {len(commits)} commitova.\n")

    changelog = []
    prev_ids = {}
    final_ids = {}

    for i, (commit_hash, date_fmt) in enumerate(commits):
        short = commit_hash[:8]
        print(f"[{i+1}/{len(commits)}] {short} — {date_fmt}", end=" ... ")

        content = get_towers_js_at(commit_hash, towers_path)
        if not content:
            print("prazan fajl, preskačem")
            continue

        curr_ids = parse_towers_js(content)
        if not curr_ids:
            print("0 lokacija, preskačem")
            continue

        total_curr = sum(len(v) for v in curr_ids.values())

        if not prev_ids:
            # Prvi commit — sve je "novo"
            print(f"{total_curr:,} lokacija (inicijalno stanje)")
            prev_ids = curr_ids
            final_ids = curr_ids
            continue

        added = diff_ids(prev_ids, curr_ids)
        total_new = sum(len(v) for v in added.values())

        if total_new == 0:
            print(f"nema novih BS ({total_curr:,} ukupno)")
        else:
            mts    = len(added.get(0, set()))
            a1     = len(added.get(1, set()))
            yettel = len(added.get(2, set()))
            print(f"+{total_new} novih BS (MTS:{mts} A1:{a1} Yettel:{yettel})")

            details = build_details(added, content)

            entry = {
                "date":   date_fmt,
                "total":  total_new,
                "mts":    mts,
                "a1":     a1,
                "yettel": yettel,
            }
            if details:
                entry["details"] = details

            changelog.append(entry)

        prev_ids = curr_ids
        final_ids = curr_ids

    # Sortiraj od najnovijeg
    changelog.sort(key=lambda e: e["date"], reverse=True)

    print(f"\nUkupno {len(changelog)} unosa sa promenama.")

    # Sačuvaj changelog
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
        json.dump(changelog, f, ensure_ascii=False, indent=2)
    print(f"Sačuvano: {CHANGELOG_FILE}")

    # Sačuvaj poslednje tower IDs
    if final_ids:
        with open(TOWER_IDS_FILE, "w") as f:
            json.dump({str(k): list(v) for k, v in final_ids.items()}, f)
        print(f"Sačuvano: {TOWER_IDS_FILE}")

    print("\nGotovo! Sada pokreni:")
    print("  git add data/changelog.json data/.prev_tower_ids.json")
    print("  git commit -m \"Rekonstruisana istorija promena\"")
    print("  git push")


if __name__ == "__main__":
    main()
