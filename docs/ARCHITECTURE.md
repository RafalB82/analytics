# Architektura: rdzeń `analytics` + fetcher `mcp_fetchers`

Ten dokument opisuje, jak **dwa pakiety współistnieją w jednym repo** i dlaczego
podział jest taki, a nie inny. To stała referencja dla każdego, kto rozwija
projekt — najpierw przeczytaj to, potem kod.

---

## 1. Dwie warstwy, jedna odpowiedzialność każdej

```
┌─────────────────────────────────────────────────────────────────────┐
│  MCP SERWERY (źródła danych, z sieci)                                 │
│  Apple Health / Apple Watch · Hevy · MyFitnessPal                    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  wołania MCP (transport)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  mcp_fetchers/   POBIERANIE + KONWERSJA FORMATU (offline, stdin→stdout)│
│  · fetch_mcp.py      — programistyczny odczyt MCP (cron-ready)        │
│  · *_normalize.py    — surowe MCP → format rdzenia (deterministyczne)  │
│  · build_input.py    — składa payload, odpala analizę                 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  tmp/*.json (bufory pośrednie)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  analytics/   RDZEŃ ANALITYCZNY (offline, deterministyczny, liczy)   │
│  · acwr.py, apple_cardio.py  — obciążenie (tonaż, TRIMP), ACWR        │
│  · energy_balance.py         — wydatek vs zjedzone kcal (ryzyko urazu)│
│  · nutrition_adaptive.py     — TDEE, cel kcal, białko                │
│  · readiness_integration.py  — finalny scoring gotowości             │
│  · run_analysis.py           — orchestrator (wejście→wyjście JSON)   │
└─────────────────────────────────────────────────────────────────────┘
```

### Zasada nadrzędna (nienaruszalna)

> **`mcp_fetchers/` NIE liczy — tylko pobiera z MCP i przepisuje format.**
> **`analytics/` NIE woła MCP — tylko liczy na dostarczonych danych.**

Konsekwencje:
- Każda logika obliczeniowa (tonaż, TRIMP/ACWR, TDEE, bilans kaloryczny,
  scoring gotowości) żyje **wyłącznie w `analytics/`** — testowalna offline.
- Każde wołanie MCP (transport, sesja, sieć) żyje **wyłącznie w `mcp_fetchers/`**
  (albo u agenta/cronu) — rdzeń jest deterministyczny i wersjonowany.
- `mcp_fetchers` dostarcza dane w dokładnie takim kształcie, jakiego oczekuje
  rdzeń; konwersja formatu jest **ścisła, deterministyczna, idempotentna**.

To rozdzielenie (wzorzec "adapter na brzegu") było celową decyzją — patrz
`mcp_fetchers/README.md` §1 i §2.3, gdzie opisano, dlaczego nie refaktorujemy
rdzenia do czytania surowych formatów MCP.

---

## 2. Mapa odpowiedzialności (kto co robi)

| Dane | Pobieranie (mcp_fetchers / agent) | Konwersja formatu (czysta) | Analiza / liczenie (rdzeń analytics) |
|---|---|---|---|
| Workouty siłowe | `fetch_mcp.py` → Hevy (stdio) | `hevy_normalize.py` (weight_kg→weight, start_time→startTime, kasuje warmupy) | tonaż/objętość `_volume` → `fetch_hevy.py`; ACWR siła → `acwr.py` |
| Cardio (Apple) | `fetch_mcp.py` → Apple (HTTP) | `apple_normalize.py` (filtruje cardio, dedup po id, sortuje) | TRIMP z tętna → `apple_cardio.py`; ACWR cardio + guard próbki → `acwr.py` |
| Fizjologia dzienna | `fetch_mcp.py` → Apple (HTTP) | `apple_normalize.py` (selekcja pól, temp, waga) | HRV/RHR/sen/trendy → `baseline.py`; readiness → `readiness_integration.py` |
| Zjedzone kalorie | `fetch_mcp.py` → MFP (HTTP, `mfp_get_diary`) | `mfp_normalize.py` (wyciąga daily_totals.calories) | bilans wydatek vs kcal, ryzyko niedoboru → `energy_balance.py` |
| Wydatek energetyczny | — (z Apple, powyżej) | — | TDEE + cel kcal + białko → `nutrition_adaptive.py` |

---

## 3. Przepływ danych (pełna ścieżka)

Dwa wejścia kończą się w tym samym rdzeniu:

**A. Uruchomienie ręczne (agent/cron):**
```
agent woła MCP (Apple/Hevy/MFP) ──surowy JSON──► *_normalize.py (stdin)
                                                       │
                                                       ▼
                                                tmp/*.json  (bufory)
                                                       │
                                               build_input.py (składa payload)
                                                       │
                                                       ▼
                                            analytics.run_analysis → JSON
```

**B. Uruchomienie programistyczne (cron-ready, bez agenta):**
```
python3 -m mcp_fetchers.fetch_mcp --target YYYY-MM-DD
  → łączy się z Hevy + Apple + MFP (MCP transport)
  → normalizuje (hevy/apple/mfp_normalize)
  → składa payload (build_input)
  → odpala analytics.run_analysis → JSON
```

`tmp/*.json` to **bufory pośrednie** (punkt kontrolny między pobraniem a analizą):
można je odtworzyć i debugować bez ponownego wołania MCP. Są w `.gitignore`.

---

## 4. Dlaczego dwa pakiety, a nie jeden

- **Rdzeń `analytics`** jest czysty względem źródeł: nie wie, skąd przyszły dane
  (Apple/Hevy/MFP), nie wie o MCP, jest w pełni testowalny i wersjonowany osobno.
- **Fetcher `mcp_fetchers`** absorbuje specyfikę MCP (nazwy narzędzi, formaty,
  transport, sesje) **na brzegu** — zmiany w API MCP nie dotykają logiki rdzenia.
- Oba instalują się przez ten sam `pip install -e .` (pyproject obejmuje
  `analytics*` i `mcp_fetchers*`) i są wersjonowane razem — spójny release.

### Limity / uwaga

`mcp_fetchers` zależy od rdzenia (`from analytics.run_analysis import run`),
ale ta zależność nie jest formalnie zadeklarowana jako wymaganie pakietu —
wynika z tego, że oba są w jednym repo i instalowane razem. Jeśli kiedyś
mcp_fetchers miałby być wydawany osobno, należy dodać `analytics` jako zależność.

---

## 5. Pakiety a uruchomienie

```bash
pip install -e .                    # instaluje analytics + mcp_fetchers
python -m analytics.run_analysis '<json>'               # rdzeń, ręcznie
python -m mcp_fetchers.fetch_mcp --target 2026-08-09    # pełny pipeline (MCP)
cat diary.json | python -m mcp_fetchers.mfp_normalize   # pojedyncza konwersja
```

Szczegóły uruchomienia każdego z osobna — w `README.md` (root, rdzeń)
oraz `mcp_fetchers/README.md` (fetcher).
