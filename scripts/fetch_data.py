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
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TOWERS_JS = os.path.join(DATA_DIR, "towers.js")
META_JS   = os.path.join(DATA_DIR, "meta.js")
HASH_FILE = os.path.join(DATA_DIR, ".last_hash")

os.makedirs(DATA_DIR, exist_ok=True)


def fetch_csv() -> bytes:
    print(f"Preuzimam CSV sa: {CSV_URL}")
    # RATEL ima self-signed / nedostaje intermediate cert — ignorišemo SSL
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


def process(raw: bytes) -> list:
    """Parsira CSV i grupiše po fizičkoj lokaciji (lat, lon)."""
    import csv

    # UTF-16 BOM → decode
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

    # Group by (lat, lon)
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


def write_meta_js(records: list, raw: bytes):
    today = datetime.datetime.utcnow().strftime("%d.%m.%Y")
    op_counts = [0, 0, 0]
    for loc in records:
        for op_idx, _ in loc[5]:
            op_counts[op_idx] += 1

    meta = {
        "updated": today,
        "locations": len(records),
        "mts": op_counts[0],
        "a1":  op_counts[1],
        "yettel": op_counts[2],
        "source_bytes": len(raw),
    }
    js = "const META=" + json.dumps(meta, ensure_ascii=False) + ";"
    with open(META_JS, "w", encoding="utf-8") as f:
        f.write(js)
    print(f"Meta: {meta}")


def main():
    raw = fetch_csv()
    h   = csv_hash(raw)
    old = load_last_hash()

    if h == old and "--force" not in sys.argv:
        print("Podaci nisu promenjeni (hash identičan). Preskačem obradu.")
        print("Koristite --force za prisilno ažuriranje.")
        return 0

    print("Novi podaci detektovani, obrađujem...")
    records = process(raw)
    write_towers_js(records)
    write_meta_js(records, raw)
    save_hash(h)
    print("Gotovo!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
