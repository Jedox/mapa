#!/usr/bin/env python3
"""
Preuzima CSV sa RATEL registra (reg221) i generiše data/towers.js i data/meta.js
koji se koriste na BS Mapa sajtu.

Pokretanje: python3 scripts/fetch_data.py
"""

import sys
import os
import json
import hashlib
import urllib.request
import urllib.error
import ssl
import datetime
import io

CSV_URL = "https://registar.ratel.rs/sr/reg221?action=table&format=csv&nosilac_prava=&primenjena_tehnologija=&filter="
DATA_DIR       = os.path.join(os.path.dirname(__file__), "..", "data")
TOWERS_JS      = os.path.join(DATA_DIR, "towers.js")
META_JS        = os.path.join(DATA_DIR, "meta.js")
HASH_FILE      = os.path.join(DATA_DIR, ".last_hash")
CHANGELOG_FILE = os.path.join(DATA_DIR, "changelog.json")
TOWER_IDS_FILE = os.path.join(DATA_DIR, ".prev_tower_ids.json")

MAX_CHANGELOG = 50

os.makedirs(DATA_DIR, exist_ok=True)


def fetch_csv() -> bytes:
    print(f"Preuzimam CSV sa: {CSV_URL}")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        CSV_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; BS-Mapa/1.0)",
            "Accept": "text/csv,text/plain,*/*",
        }
    )
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        data = resp.read()
    print(f"Preuzeto {len(data):,} bajtova")
    return data


def csv_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_last_hash() -> str:
    if os.path.exists(HASH_FILE):
        return open(HASH_FILE).read().strip()
    return ""


def save_hash(h: str):
    open(HASH_FILE, "w").write(h)


# ── Changelog funkcije ────────────────────────────────────────────────────────

def load_changelog() -> list:
    if os.path.exists(CHANGELOG_FILE):
        with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_changelog(log: list):
    with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def load_prev_tower_ids() -> dict:
    if os.path.exists(TOWER_IDS_FILE):
        with open(TOWER_IDS_FILE, "r") as f:
            raw = json.load(f)
            return {int(k): set(v) for k, v in raw.items()}
    return {}


def save_tower_ids(ids: dict):
    with open(TOWER_IDS_FILE, "w") as f:
        json.dump({str(k): list(v) for k, v in ids.items()}, f)


def build_changelog_entry(records: list, prev_tower_ids: dict, now: str):
    """Napravi unos za changelog — koliko i kojih novih BS po operateru."""
    from collections import defaultdict
    current_ids = defaultdict(set)
    for loc in records:
        for op_idx, towers in loc[5]:
            for tower in towers:
                current_ids[op_idx].add(tower[0])

    op_names = ["MTS", "A1", "Yettel"]
    added = {}
    for op_idx in range(3):
        old_set = prev_tower_ids.get(op_idx, set())
        new_set = current_ids.get(op_idx, set())
        diff = new_set - old_set
        if diff:
            added[op_idx] = diff

    total_new = sum(len(v) for v in added.values())

    entry = {
        "date":   now,
        "total":  total_new,
        "mts":    len(added.get(0, set())),
        "a1":     len(added.get(1, set())),
        "yettel": len(added.get(2, set())),
    }

    details = []
    for op_idx, tower_set in sorted(added.items()):
        op_name = op_names[op_idx]
        for tid in list(tower_set)[:20]:
            for loc in records:
                for oi, towers in loc[5]:
                    if oi == op_idx:
                        for tower in towers:
                            if tower[0] == tid:
                                techs = sorted({s[2] for s in tower[2]})
                                details.append({
                                    "op":   op_name,
                                    "name": tower[1],
                                    "tech": techs,
                                    "addr": loc[2],
                                })
                                break

    if details:
        entry["details"] = details[:50]

    return entry, current_ids


# ── Originalno parsiranje (nepromenjeno) ──────────────────────────────────────

def process(raw: bytes) -> list:
    """Parsira CSV i grupiše po fizičkoj lokaciji (lat, lon)."""
    import csv

    text = raw.decode("utf-16")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")

    op_map = {"Telekom Srbija": 0, "A1": 1, "Yettel": 2}
    tech_bit = {"2G": 1, "3G": 2, "4G": 4, "5G": 8}

    rows = []
    for r in reader:
        op = op_map.get(r["Nosilac prava (operator)"])
        if op is None:
            continue
        try:
            lat = round(float(r["Geografska širina"]), 5)
            lon = round(float(r["Geografska dužina"]), 5)
        except (ValueError, KeyError):
            continue

        ev = str(r["Evidencioni broj"]).strip()
        parts = ev.rsplit(".", 1)
        tower_id = parts[0]
        sector   = parts[1] if len(parts) > 1 else "0"
        freq_raw = r.get("Radiofrekvencijski opseg", "0").strip()
        try:
            freq = int(float(freq_raw))
        except ValueError:
            freq = 0
        tech = r.get("Primenjena tehnologija", "").strip()
        loc_name = r.get("Naziv mesta", "").strip()
        addr     = r.get("Adresa", "").strip()

        rows.append((lat, lon, addr, op, tower_id, sector, freq, tech, loc_name))

    print(f"Parsirano {len(rows):,} redova")

    from collections import defaultdict
    loc_map = defaultdict(lambda: {"addr": "", "ops": defaultdict(lambda: defaultdict(list))})

    for lat, lon, addr, op, tid, sec, freq, tech, loc_name in rows:
        key = (lat, lon)
        entry = loc_map[key]
        if not entry["addr"]:
            entry["addr"] = addr
        entry["ops"][op][tid].append((sec, freq, tech, loc_name))

    records = []
    for (lat, lon), entry in loc_map.items():
        tech_mask = 0
        op_mask   = 0
        ops_data  = []

        for op_idx in sorted(entry["ops"].keys()):
            op_mask |= (1 << op_idx)
            towers_data = []
            for tid, secs in entry["ops"][op_idx].items():
                seen = set()
                sec_list = []
                loc_name = ""
                for sec, freq, tech, lname in secs:
                    k = (sec, freq, tech)
                    if k not in seen:
                        seen.add(k)
                        sec_list.append([sec, freq, tech])
                        tech_mask |= tech_bit.get(tech, 0)
                    if not loc_name:
                        loc_name = lname
                sec_list.sort(key=lambda x: (len(x[0]), x[0]))
                towers_data.append([tid, loc_name, sec_list])
            ops_data.append([op_idx, towers_data])

        records.append([lon, lat, entry["addr"], tech_mask, op_mask, ops_data])

    print(f"Jedinstvenih lokacija: {len(records):,}")
    return records


def write_towers_js(records: list):
    js = "const LOCS=" + json.dumps(records, ensure_ascii=False, separators=(",", ":")) + ";"
    with open(TOWERS_JS, "w", encoding="utf-8") as f:
        f.write(js)
    size = os.path.getsize(TOWERS_JS)
    print(f"Zapisano {TOWERS_JS} ({size/1024/1024:.2f} MB)")


def write_meta_js(records: list, raw: bytes, changelog: list, now: str, locations_count: int = None):
    op_counts = [0, 0, 0]
    for loc in records:
        for op_idx, _ in loc[5]:
            op_counts[op_idx] += 1

    last_change = changelog[0]["date"] if changelog else None

    meta = {
        "updated":      now,
        "locations":    locations_count if locations_count is not None else len(records),
        "mts":          op_counts[0],
        "a1":           op_counts[1],
        "yettel":       op_counts[2],
        "source_bytes": len(raw),
    }
    if last_change:
        meta["lastChange"] = last_change

    meta["changelog"] = changelog[:MAX_CHANGELOG]

    js = "const META=" + json.dumps(meta, ensure_ascii=False) + ";"
    with open(META_JS, "w", encoding="utf-8") as f:
        f.write(js)
    print(f"Meta: locations={meta['locations']}, updated={meta['updated']}, lastChange={meta.get('lastChange', '—')}")


# ── Glavni tok ────────────────────────────────────────────────────────────────

def main():
    force = "--force" in sys.argv

    raw = fetch_csv()
    h   = csv_hash(raw)
    old = load_last_hash()

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    changelog = load_changelog()

    if h == old and not force:
        print("Podaci nisu promenjeni (hash identičan). Ažuriram samo 'updated' u meta.js.")
        # Sačuvaj trenutni broj lokacija iz postojećeg meta.js
        loc_count = None
        if os.path.exists(META_JS):
            try:
                txt = open(META_JS).read()
                import re
                m = re.search(r'"locations"\s*:\s*(\d+)', txt)
                if m:
                    loc_count = int(m.group(1))
            except Exception:
                pass
        write_meta_js([], raw, changelog, now, locations_count=loc_count)
        print("Koristite --force za prisilno ažuriranje.")
        return 0

    print("Novi podaci detektovani, obrađujem...")
    records = process(raw)

    prev_tower_ids = load_prev_tower_ids()
    entry, current_ids = build_changelog_entry(records, prev_tower_ids, now)

    if entry["total"] > 0 or not prev_tower_ids:
        changelog.insert(0, entry)
        save_changelog(changelog)
        print(f"Changelog: +{entry['total']} novih BS (MTS:{entry['mts']} A1:{entry['a1']} Yettel:{entry['yettel']})")

    save_tower_ids(current_ids)
    write_towers_js(records)
    write_meta_js(records, raw, changelog, now)
    save_hash(h)
    print("Gotovo!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
