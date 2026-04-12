#!/usr/bin/env python3
"""
fetch_bs.py — RATEL BS Mapa ažuriranje
--------------------------------------
Preuzima CSV sa RATEL sajta, generiše:
  - data/towers.js   (lokacije i sektori za mapu)
  - data/meta.js     (statistika + istorija promena)

Pokretanje: python3 scripts/fetch_bs.py
"""

import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

import urllib.request

# ── Konfiguracija ────────────────────────────────────────────────────────────

RATEL_URL = "https://registar.ratel.rs/reg221/csv"   # prilagodi ako se URL promeni
DATA_DIR   = os.path.join(os.path.dirname(__file__), "..", "data")
TOWERS_JS  = os.path.join(DATA_DIR, "towers.js")
META_JS    = os.path.join(DATA_DIR, "meta.js")
HASH_FILE      = os.path.join(DATA_DIR, ".last_hash")       # mora da odgovara workflow git add
CHANGELOG_FILE = os.path.join(DATA_DIR, "changelog.json")  # persists između runa
TOWER_IDS_FILE = os.path.join(DATA_DIR, ".prev_tower_ids.json")

# Operateri — mapiranje naziva iz CSV-a na indeks
OP_MAP = {
    "mts": 0, "telekom": 0, "srbija": 0,
    "a1":  1, "vip": 1, "telenor": 1,
    "yettel": 2, "open": 2,
}
OP_NAMES = ["MTS", "A1", "Yettel"]

# Tehnologije — mapiranje iz CSV-a na bit masku
TECH_MAP = {"2g": 1, "gsm": 1, "3g": 2, "umts": 2, "4g": 4, "lte": 4, "5g": 8, "nr": 8}

# Maksimalan broj unosa u changelog (čuva se u meta.js)
MAX_CHANGELOG = 50

# ── Pomoćne funkcije ─────────────────────────────────────────────────────────

def now_str():
    """Trenutno vreme u UTC, formatiran kao string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_prev_hash() -> str:
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE, "r") as f:
            return f.read().strip()
    return ""

def save_hash(h: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HASH_FILE, "w") as f:
        f.write(h)

def load_changelog() -> list:
    if os.path.exists(CHANGELOG_FILE):
        with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_changelog(log: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def detect_operator(name: str) -> int:
    """Prepoznaj operatera iz naziva kolone/vrednosti. Vrati indeks 0/1/2 ili -1."""
    n = name.lower()
    for key, idx in OP_MAP.items():
        if key in n:
            return idx
    return -1

def detect_tech(s: str) -> int:
    """Vrati bit masku tehnologije."""
    s = s.lower()
    for key, bit in TECH_MAP.items():
        if key in s:
            return bit
    return 0

# ── Parsiranje CSV-a ──────────────────────────────────────────────────────────

def parse_csv(raw: bytes):
    """
    Parsira RATEL CSV i vraća:
      locs_data  — dict klučevan po (lat, lon) → agregisani podaci lokacije
      op_counts  — dict {op_idx: set of tower_ids}  za statistiku
    """
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")

    # Lokacije: (lat, lon) → { addr, tech_mask, op_mask, ops: {op_idx: {tower_id: [sektori]}} }
    locs = {}
    op_tower_ids = defaultdict(set)   # op_idx → set(tower_id)

    for row in reader:
        # Pokušaj da pronađeš relevantne kolone (RATEL CSV format može da varira)
        lat_raw = row.get("Latitude") or row.get("LAT") or row.get("lat") or ""
        lon_raw = row.get("Longitude") or row.get("LON") or row.get("lon") or ""
        addr    = row.get("Address") or row.get("Adresa") or row.get("Lokacija") or ""
        op_raw  = row.get("Operator") or row.get("operator") or ""
        tech_raw= row.get("Technology") or row.get("Tehnologija") or row.get("tech") or ""
        tower_id= row.get("BS_ID") or row.get("TowerID") or row.get("ID") or ""
        sector  = row.get("Sector") or row.get("Sektor") or row.get("SEC") or "1"
        freq    = row.get("Frequency") or row.get("Frekvencija") or row.get("FREQ") or "0"
        loc_name= row.get("LocationName") or row.get("Naziv") or row.get("Name") or addr

        try:
            lat = float(lat_raw.replace(",", ".").strip())
            lon = float(lon_raw.replace(",", ".").strip())
        except (ValueError, AttributeError):
            continue

        op_idx  = detect_operator(op_raw)
        if op_idx < 0:
            continue
        tech_bit = detect_tech(tech_raw)
        tech_name = {1:"2G", 2:"3G", 4:"4G", 8:"5G"}.get(tech_bit, "4G")

        key = (round(lon, 5), round(lat, 5))
        if key not in locs:
            locs[key] = {
                "addr": addr.strip(),
                "tech_mask": 0,
                "op_mask": 0,
                "ops": defaultdict(lambda: defaultdict(list)),
                "loc_name": loc_name.strip(),
            }

        loc = locs[key]
        loc["tech_mask"] |= tech_bit
        loc["op_mask"]   |= (1 << op_idx)
        loc["ops"][op_idx][tower_id].append((sector, freq, tech_name))
        op_tower_ids[op_idx].add(tower_id)

    return locs, op_tower_ids

# ── Generisanje towers.js ─────────────────────────────────────────────────────

def build_towers_js(locs: dict) -> str:
    """Generiši kompaktan JS array za Leaflet mapu."""
    lines = ["/* Auto-generisano — ne menjati ručno */", "const LOCS=["]

    for (lon, lat), d in locs.items():
        ops_data = []
        for op_idx, towers in sorted(d["ops"].items()):
            tower_list = []
            for tid, sectors in towers.items():
                tower_list.append([tid, d["loc_name"], sectors])
            ops_data.append([op_idx, tower_list])

        addr_esc = d["addr"].replace('"', '\\"')
        name_esc = d["loc_name"].replace('"', '\\"')
        entry = json.dumps(
            [lon, lat, addr_esc, d["tech_mask"], d["op_mask"], ops_data],
            ensure_ascii=False, separators=(",", ":")
        )
        lines.append(entry + ",")

    lines.append("];")
    return "\n".join(lines)

# ── Poređenje i detekcija promena ─────────────────────────────────────────────

def diff_tower_ids(old_ids: dict, new_ids: dict) -> dict:
    """
    Poredi setove tower_id-jeva po operateru.
    Vraća {op_idx: set_novih_id} — samo novi tornjevi.
    """
    result = {}
    for op_idx in range(3):
        old_set = old_ids.get(op_idx, set())
        new_set = new_ids.get(op_idx, set())
        added = new_set - old_set
        if added:
            result[op_idx] = added
    return result

def load_prev_tower_ids() -> dict:
    """Učitaj prethodni skup tower ID-jeva iz fajla."""
    if os.path.exists(TOWER_IDS_FILE):
        with open(TOWER_IDS_FILE, "r") as f:
            raw = json.load(f)
            return {int(k): set(v) for k, v in raw.items()}
    return {}

def save_tower_ids(ids: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOWER_IDS_FILE, "w") as f:
        json.dump({str(k): list(v) for k, v in ids.items()}, f)

# ── Generisanje meta.js ───────────────────────────────────────────────────────

def build_meta_js(updated: str, changelog: list) -> str:
    """Generiši data/meta.js sa svim meta-podacima."""
    last_change = changelog[0]["date"] if changelog else None

    lines = [
        "/* Auto-generisano — ne menjati ručno */",
        "const META = {",
        f'  updated: "{updated}",',
    ]
    if last_change:
        lines.append(f'  lastChange: "{last_change}",')

    # Changelog array (do MAX_CHANGELOG unosa)
    cl_json = json.dumps(changelog[:MAX_CHANGELOG], ensure_ascii=False, indent=2)
    # Uvuci 2 razmaka
    cl_indented = "\n".join("  " + l for l in cl_json.splitlines())
    lines.append(f"  changelog: {cl_indented},")
    lines.append("};")

    return "\n".join(lines)

# ── Glavni tok ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Prisilno ažuriranje čak i ako nema promena")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"[{now_str()}] Preuzimanje CSV sa RATEL-a...")
    try:
        req = urllib.request.Request(RATEL_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"  GREŠKA: {e}", file=sys.stderr)
        sys.exit(1)

    current_hash = sha256_of(raw)
    prev_hash    = load_prev_hash()

    updated_str  = now_str()
    changelog    = load_changelog()

    if current_hash == prev_hash and not args.force:
        print("  Nema promena u CSV-u. Ažuriramo samo 'updated' vreme u meta.js.")
        # Samo osvežimo updated vreme, changelog ostaje isti
        meta_js = build_meta_js(updated_str, changelog)
        with open(META_JS, "w", encoding="utf-8") as f:
            f.write(meta_js)
        print("  data/meta.js ažuriran.")
        return  # towers.js ne treba ponovo generisati

    print("  ✓ Detektovane promene! Parsiranje CSV-a...")
    locs, new_tower_ids = parse_csv(raw)
    prev_tower_ids = load_prev_tower_ids()

    # Detekcija novih tornjeva po operateru
    added = diff_tower_ids(prev_tower_ids, new_tower_ids)

    total_new = sum(len(v) for v in added.values())
    print(f"  Novi tornjevi: ukupno {total_new} "
          f"(MTS: {len(added.get(0,set()))}, "
          f"A1: {len(added.get(1,set()))}, "
          f"Yettel: {len(added.get(2,set()))})")

    # Generiši towers.js
    towers_js = build_towers_js(locs)
    with open(TOWERS_JS, "w", encoding="utf-8") as f:
        f.write(towers_js)
    print(f"  data/towers.js generisan ({len(locs):,} lokacija).")

    # Dodaj unos u changelog samo ako ima novih tornjeva
    if total_new > 0 or not prev_tower_ids:
        entry = {
            "date":   updated_str,
            "total":  total_new,
            "mts":    len(added.get(0, set())),
            "a1":     len(added.get(1, set())),
            "yettel": len(added.get(2, set())),
        }

        # Detalji: do 50 reprezentativnih novih stanica
        details = []
        for op_idx, tower_set in sorted(added.items()):
            op_name = OP_NAMES[op_idx]
            for tid in list(tower_set)[:20]:  # max 20 po operateru
                # Pronađi lokaciju i tehnologije za ovaj tower_id
                for (lon, lat), d in locs.items():
                    if op_idx in d["ops"] and tid in d["ops"][op_idx]:
                        sectors = d["ops"][op_idx][tid]
                        techs = list({s[2] for s in sectors})
                        details.append({
                            "op":   op_name,
                            "name": d["loc_name"],
                            "tech": sorted(techs),
                            "addr": d["addr"]
                        })
                        break

        if details:
            entry["details"] = details[:50]

        changelog.insert(0, entry)  # najnovije prvo
        save_changelog(changelog)
        print(f"  Changelog ažuriran ({len(changelog)} unosa ukupno).")

    # Sačuvaj novi hash i tower IDs
    save_hash(current_hash)
    save_tower_ids(new_tower_ids)

    # Generiši meta.js
    meta_js = build_meta_js(updated_str, changelog)
    with open(META_JS, "w", encoding="utf-8") as f:
        f.write(meta_js)
    print("  data/meta.js generisan.")

    print(f"[{now_str()}] Gotovo! ✓")

if __name__ == "__main__":
    main()
