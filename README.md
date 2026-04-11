# BS Mapa — Bazne Stanice Srbije

Interaktivna mapa baznih stanica iz [RATEL reg221 registra](https://registar.ratel.rs/sr/reg221).
Podaci se automatski osvežavaju **svakog sata** putem GitHub Actions.

---

## Postavljanje na GitHub Pages

### Korak 1 — Napravi GitHub nalog

Idi na [github.com](https://github.com) i registruj se ako nemaš nalog.

---

### Korak 2 — Napravi novi repozitorijum

1. Klikni **"+"** u gornjem desnom uglu → **"New repository"**
2. Popuni:
   - **Repository name:** `bs-mapa`
   - **Visibility:** Public (obavezno za besplatan hosting)
3. Klikni **"Create repository"**

---

### Korak 3 — Postavi fajlove

Otvori terminal i pokreni:

```bash
cd bs-mapa
git init
git add .
git commit -m "Inicijalno postavljanje"
git branch -M main
git remote add origin https://github.com/TVOJE_IME/bs-mapa.git
git push -u origin main
```

---

### Korak 4 — Uključi GitHub Pages

1. GitHub repozitorijum → **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** / **(root)**
4. Klikni **Save**

Sajt je za ~2 minuta na: `https://TVOJE_IME.github.io/bs-mapa/`

---

### Korak 5 — Pokreni prvo preuzimanje

**Actions → "Ažuriranje podataka sa RATEL" → Run workflow**

---

### Korak 6 — Google AdSense (opciono)

U `index.html` zameni:
- `ca-pub-XXXXXXXXXXXXXXXX` → tvoj Publisher ID
- `XXXXXXXXXX` → Slot ID sidebar oglasa
- `YYYYYYYYYY` → Slot ID mobilnog banera

---

## Automatsko ažuriranje

GitHub Action radi **svakog sata**:
1. Preuzima CSV sa RATEL sajta
2. Proverava hash — ako nema promena, preskače
3. Ako ima novih podataka — commit-uje i sajt se ažurira

Besplatni GitHub nalog ima 2.000 min/mesec. Hourly job troši ~1.440 min/mesec — u okviru limita.
