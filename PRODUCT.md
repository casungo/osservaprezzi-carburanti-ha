# PRD — Osservaprezzi Carburanti per Home Assistant

Data audit: 2026-08-12
Stato: integrazione HACS rilasciata; compatibilità corrente da riconfermare.

## Intento

- Utenti: utenti Home Assistant che vogliono prezzi e stato delle stazioni di carburante italiane nella propria installazione.
- Job principale: configurare una stazione per posizione, comune o ID e ottenere sensori di carburante, stazione, orari, servizi e contatti con cache/refresh controllati.
- Non-obiettivi: un portale pubblico, una fonte indipendente dai dati MIMIT/Osservaprezzi, o una garanzia di compatibilità con ogni versione futura di Home Assistant.

## Maturità attuale

### Capacità del repository

L’integrazione distribuisce la stabile `v2.3.0` e la pre-release `v2.4.0`, con config flow flessibile, sensori, servizi globali, diagnostica, cache, soglie stale e test pytest. Le guide inglese e italiana documentano HACS, setup e canali di rilascio.

### Evidenza d'uso reale

La distribuzione HACS/repository e i test locali sono verificabili; non è stata aperta una installazione Home Assistant reale in questo audit. La compatibilità con il core corrente e l’esperienza della pre-release restano da raccogliere dagli utenti.

## Stato del lavoro

- Completato: integrazione stabile, flussi di configurazione e servizi/caching documentati.
- Attivo: pre-release `v2.4.0` e manutenzione di compatibilità.
- Bloccato: nessun blocker di repository osservato; manca evidenza corrente da installazioni HA diverse.
- Congelato/indeciso: promuovere la pre-release solo dopo feedback e validator coerenti.

## Prossima azione / decisione owner

Raccogliere report di compatibilità su versioni Home Assistant e modalità di setup, separando chiaramente la stabile `v2.3.0` dalla pre-release `v2.4.0`.

## Audit anti-slop

### Testo

Nessun difetto confermato con confidenza almeno 75%; emoji e duplicazione inglese/italiana svolgono una funzione documentale/localizzativa e non bastano da sole come finding.

### Codice

Nessun difetto anti-slop confermato con la soglia. I test e i vincoli async/servizi sono proporzionati al confine Home Assistant.

### Design

Non applicabile: integrazione e configurazione Home Assistant, senza UI visuale proprietaria verificata.

## Fonti di evidenza

- [AGENTS.md](AGENTS.md)
- [README.md](README.md)
- [README.it.md](README.it.md)
- [CHANGELOG.md](CHANGELOG.md)
- `custom_components/`, `tests/`, `pyproject.toml`
