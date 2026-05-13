# Analyticity Backend

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Analyticity Backend je backendová část aplikace Analyticity, funkcionalitami výrazně omezená pro potřeby integračního testování platformy RIoT. Původní aplikace Analyticity je webová analytická aplikace pro analýzu a vizualizaci dopravních dat města Brna nad daty Waze. Autorkou původní aplikace je **Magdaléna Ondrušková** a původní repozitář je dostupný v organizaci [analyticity](https://github.com/analyticity), respektive jako frontendová aplikace [waze-data-analysis](https://github.com/MagdalenaOndruskova/waze-data-analysis).

Tato upravená varianta zachovává HTTP rozhraní očekávané frontendovou částí Analyticity, ale datovou vrstvu Waze nahrazuje dotazy do RIoT. Slouží tedy hlavně k ověření, že RIoT dokáže fungovat jako obecná serverová část pro externí aplikaci s vlastním uživatelským rozhraním a vlastním modelem výstupů.

## Úloha RIoT

V původní aplikaci byla data o kongescích načítána z městského Waze výstupu a aplikačně agregována pro mapu, grafy a výpisy ulic. Tato varianta místo přímého čtení Waze zdroje používá WebSocket API RIoT. Při startu dohledá typ `WAZE_JAM_LOCATION`, ověří parametry `delay` a `jamCount`, případně založí aplikační KPI a nad historickými KPI i surovými body skládá odpovědi ve tvaru očekávaném původním frontendem.

Backend definuje čtyři KPI nad daty Waze:

- `Analyticity Waze Delay With Single Jam`: `delay != 0 AND jamCount == 1`.
- `Analyticity Waze Delay With Two Jams`: `delay != 0 AND jamCount == 2`.
- `Analyticity Waze Delay With Three Jams`: `delay != 0 AND jamCount == 3`.
- `Analyticity Waze Delay With More Than Three Jams`: `delay != 0 AND jamCount > 3`.

Barva ulice se neurčuje prostým počtem bodů, ale změnovou heuristikou nad časovými snímky. Jedna aktivní kongesce se započítá jen při přechodu z neaktivního stavu, u více současných kongescí se započítávají nově přidané identifikátory z `rawJams`, případně rozdíl proti předchozí hodnotě `jamCount`. Výsledek se vztáhne k délce období:

- `green`: počet výskytů je menší než `počet_dní * 3`.
- `orange`: počet výskytů je menší než `počet_dní * 7`.
- `red`: počet výskytů je větší nebo roven `počet_dní * 7`.

Backend tak přes RIoT určuje dostupný rozsah historických Waze dat, získává seznam ulic, obarvuje segmenty podle závažnosti kongescí a sestavuje časové řady pro detail ulice, aniž by frontend musel znát rozhraní RIoT.

Části původní aplikace pracující s Waze alerty nejsou v této testovací variantě napojené na nový zdroj a vrací prázdné datové sady, pokud není doplněn jiný lokální zdroj.

## Spuštění

Backend očekává běžící RIoT s historickými daty typu `WAZE_JAM_LOCATION` a API klíčem s oprávněními pro čtení typů, instancí, KPI definic a časových řad. Výchozí Docker konfigurace používá backend RIoT na hostitelském stroji:

```text
ws://host.docker.internal:9090/ws
```

Samostatné spuštění backendu:

```bash
docker compose up --build
```

## Konfigurace

Hlavní proměnné pro napojení na RIoT jsou v [docker-compose.yaml](docker-compose.yaml):

- `RIOT_API_KEY`: API klíč do RIoT.
- `RIOT_WS_URL`: WebSocket adresa RIoT.
- `RIOT_WS_ORIGIN`: origin používaný při WebSocket připojení.
- `RIOT_WAZE_SD_TYPE_UID`: volitelný UID typu Waze dat, výchozí je `WAZE_JAM_LOCATION`.
- `RIOT_QUERY_CHUNK_DAYS`: volitelná velikost časových bloků pro historické dotazy.

Před spuštěním je nutné do souboru [docker-compose.yaml](docker-compose.yaml) doplnit platný API klíč do proměnné `RIOT_API_KEY`. Bez platného klíče se backend k RIoT nepřipojí a dotazy na historická Waze data nebudou fungovat.

Lokálně je backend dostupný na adrese:

```text
http://localhost:8002
```

## Licence

Původní aplikace Analyticity je licencovaná pod MIT licencí. Licence je uvedena v souboru [LICENSE](LICENSE).
