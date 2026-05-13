# Analyticity Frontend

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Analyticity Frontend je klientská část aplikace Analyticity, funkcionalitami ponechaná pouze v rozsahu potřebném pro integrační testování platformy RIoT. Původní aplikace Analyticity slouží k analýze a vizualizaci dopravních dat města Brna nad daty Waze. Autorkou původní aplikace je **Magdaléna Ondrušková** a původní repozitář je dostupný jako [waze-data-analysis](https://github.com/MagdalenaOndruskova/waze-data-analysis).

Frontend samotný nebyl pro integraci RIoT zásadně upravován. Dále zobrazuje mapu, dashboard, výběr ulic, tras a časového období stejným způsobem jako původní aplikace. Komunikuje s backendem upraveným pro využití RIoT jako zdroje historických Waze dat a KPI výsledků.

## Spuštění

Samostatné spuštění frontendové aplikace:

```bash
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Frontend očekává backend Analyticity na adrese:

```text
http://localhost:8002
```

Výchozí cesta aplikace ve Vite konfiguraci je:

```text
http://localhost:5173/waze-data-analysis/
```

## Licence

Původní aplikace Analyticity je licencovaná pod MIT licencí. Licence je uvedena v souboru [LICENSE](LICENSE).
