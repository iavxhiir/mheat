# -*- coding: utf-8 -*-
"""Generate French and Italian translations of the MHEAT tutorial.

This script reads ``mhw_mediterranean.ipynb`` and produces two parallel
notebooks with markdown cells translated, while keeping every code cell
identical (Python is the universal language).

Run once: ``python tutorials/_generate_translations.py``.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

SRC = Path(__file__).with_name("mhw_mediterranean.ipynb")

FR = {
    "md-intro": [
        "# Vagues de chaleur marines en Méditerranée — tutoriel MHEAT\n",
        "\n",
        "## Contexte scientifique\n",
        "\n",
        "Une **Vague de Chaleur Marine (VCM)**, ou *Marine Heatwave* (MHW), est un événement océanique discret, prolongé et anormalement chaud. Formellement, Hobday *et al.* (2016) définissent une VCM comme une période d'au moins **cinq jours consécutifs** pendant laquelle la température de surface de la mer (SST) dépasse le 90ᵉ centile variable selon la saison, calculé sur une climatologie de référence de 30 ans (typiquement 1991–2020). Une fois détecté, l'événement est classé en cinq catégories de sévérité (I Modérée → V Super-Extrême) à partir du rapport entre l'anomalie maximale et l'anomalie seuil. Parce que la définition est relative à la climatologie locale, la même température absolue peut constituer une VCM dans une région et rester parfaitement normale dans une autre.\n",
        "\n",
        "La **mer Méditerranée** est l'un des bassins océaniques qui se réchauffent le plus vite : la température de surface a augmenté d'environ 0,4 °C par décennie depuis les années 1980, soit à peu près trois fois la moyenne mondiale, et le bassin a connu des VCM à l'échelle du bassin entier en 2003, 2015, 2017, 2018, 2022 et 2023. Ces événements coïncident avec des mortalités massives d'invertébrés benthiques (gorgones, éponges, bivalves), des pertes en aquaculture de poissons, et un blanchissement à grande échelle des herbiers de *Posidonia oceanica* — un habitat-clé méditerranéen. Quantifier la co-localisation des événements extrêmes avec les secteurs vulnérables est un besoin politique direct au titre de la Directive-cadre Stratégie pour le milieu marin et de la Stratégie Biodiversité 2030 de l'UE.\n",
        "\n",
        "Ce notebook parcourt le flux de travail MHEAT sur un **cube SST synthétique fourni** afin que vous puissiez exécuter chaque cellule **sans compte Copernicus** ni accès Internet. Le même code, inchangé, fonctionne sur les données réelles du Copernicus Marine Service une fois les identifiants fournis — voir la dernière section.\n",
        "\n",
        "---\n",
        "\n",
        "## Dépendances\n",
        "\n",
        "```bash\n",
        "pip install xarray numpy pandas matplotlib marineHeatWaves netCDF4\n",
        "# Optionnel, pour la récupération en direct depuis CMS :\n",
        "pip install copernicusmarine\n",
        "```",
    ],
    "md-step1": [
        "## Étape 1 — Charger un cube SST méditerranéen\n",
        "\n",
        "Nous utilisons le cube synthétique livré avec MHEAT. C'est un champ SST quotidien de 3 ans (2020–2022) à 0,25° couvrant la plus grande partie de la Méditerranée occidentale et centrale, avec un cycle saisonnier réaliste et une forte anomalie chaude injectée en juillet–août 2022 pour imiter la vague de chaleur historique de cet été-là.\n",
        "\n",
        "Le cube a la forme ``(1096 jours × 41 lat × 73 lon)``.",
    ],
    "md-step2": [
        "## Étape 2 — Climatologie saisonnière + seuil au 90ᵉ centile\n",
        "\n",
        "Nous choisissons le pixel le plus chaud pour l'illustration, exécutons ``marineHeatWaves.detect()`` et traçons les trois courbes de référence : la SST quotidienne, la moyenne lissée par jour de l'année (climatologie) et le seuil au 90ᵉ centile. Les zones rouges ombrées sont les VCM identifiées par le détecteur.",
    ],
    "md-step3": [
        "## Étape 3 — Détection pixel par pixel + regroupement avec les utilitaires MHEAT\n",
        "\n",
        "Le backend encapsule ``marineHeatWaves.detect`` dans ``detect_cube`` (pixel par pixel) et ``cluster_events`` (fusionne les voisins spatio-temporels). Ensemble ils transforment des milliers de minuscules événements par pixel en quelques polygones de région contigus — l'unité d'analyse pertinente pour les études d'impact.",
    ],
    "md-map": [
        "## Étape 4 — Carte spatiale de la densité d'événements + histogramme d'intensité"
    ],
    "md-impact": [
        "## Étape 5 — Jointure avec les couches sectorielles\n",
        "\n",
        "Le backend embarque un petit jeu de sites aquacoles méditerranéens, d'aires marines protégées Natura 2000 et de polygones d'herbiers sous ``backend/app/fixtures/overlays/``. ``attach_impact_properties`` signale, pour chaque événement, combien de sites aquacoles sont touchés et combien de km² d'habitat AMP / herbier le polygone d'événement couvre.",
    ],
    "md-edito": [
        "## Comment exécuter ce notebook sur EDITO Datalab\n",
        "\n",
        "1. Dans l'EDITO Datalab, lancez le service **JupyterLab** (n'importe quelle image Python 3.11).\n",
        "2. Ouvrez un terminal et `git clone https://github.com/<your-org>/mheat.git`, puis `cd mheat/tutorials` et ouvrez ce notebook.\n",
        "3. `pip install -r ../backend/requirements.txt` depuis l'environnement du notebook (ou intégrez les dépendances dans votre image).\n",
        "4. Pour exécuter avec les **vraies** données Copernicus Marine, définissez `COPERNICUSMARINE_SERVICE_USERNAME` et `COPERNICUSMARINE_SERVICE_PASSWORD` dans l'environnement du notebook et remplacez le chargement synthétique par :\n",
        "\n",
        "```python\n",
        "import copernicusmarine\n",
        "out = copernicusmarine.subset(\n",
        "    dataset_id='SST_MED_SST_L4_NRT_OBSERVATIONS_010_004',\n",
        "    minimum_longitude=-6, maximum_longitude=36.5,\n",
        "    minimum_latitude=30, maximum_latitude=46,\n",
        "    start_datetime='2022-05-01T00:00:00', end_datetime='2022-09-30T23:59:59',\n",
        "    variables=['analysed_sst'],\n",
        ")\n",
        "ds = xr.open_dataset(out)\n",
        "```\n",
        "\n",
        "5. Le reste du notebook s'exécute sans modification. Le service MHEAT lui-même (``docker compose up --build``) est également redéployable sur une instance EDITO **Onyxia** — pointez `FRONTEND_DIR` vers le bundle Vite compilé et exposez le port 8000.\n",
        "\n",
        "## Citations\n",
        "\n",
        "* Hobday, A. J., Alexander, L. V., Perkins, S. E., Smale, D. A., *et al.* (2016). *A hierarchical approach to defining marine heatwaves.* **Progress in Oceanography**, 141, 227–238.\n",
        "* Hobday, A. J., Oliver, E. C. J., *et al.* (2018). *Categorizing and naming marine heatwaves.* **Oceanography**, 31(2), 162–173.\n",
        "* Oliver, E. C. J. (2019). `marineHeatWaves` — paquet Python pour l'identification des vagues de chaleur marines. https://github.com/ecjoliver/marineHeatWaves\n",
        "* Copernicus Marine Service — https://marine.copernicus.eu",
    ],
}

IT = {
    "md-intro": [
        "# Ondate di calore marine nel Mediterraneo — tutorial MHEAT\n",
        "\n",
        "## Contesto scientifico\n",
        "\n",
        "Un'**Ondata di Calore Marina (MHW)** è un evento oceanico discreto, prolungato e anomalamente caldo. Formalmente, Hobday *et al.* (2016) definiscono una MHW come un periodo di almeno **cinque giorni consecutivi** durante il quale la temperatura superficiale del mare (SST) supera il 90° percentile variabile stagionalmente, calcolato su una climatologia di riferimento di 30 anni (tipicamente 1991–2020). Una volta rilevato, l'evento è classificato in cinque categorie di severità (I Moderata → V Super-Estrema) in base al rapporto tra anomalia di picco e anomalia di soglia. Poiché la definizione è relativa alla climatologia locale, la stessa temperatura assoluta può essere una MHW in una regione e perfettamente normale in un'altra.\n",
        "\n",
        "Il **Mar Mediterraneo** è uno dei bacini oceanici che si riscaldano più velocemente: le temperature superficiali sono aumentate di circa 0,4 °C per decennio dagli anni '80, cioè circa tre volte la media globale, e il bacino ha sperimentato MHW estese a tutto il bacino nel 2003, 2015, 2017, 2018, 2022 e 2023. Questi eventi coincidono con mortalità massive di invertebrati bentonici (gorgonie, spugne, bivalvi), perdite nell'acquacoltura dei pesci e sbiancamenti su larga scala delle praterie di *Posidonia oceanica* — un habitat chiave del Mediterraneo. Quantificare la co-localizzazione degli eventi estremi con i settori vulnerabili è un'esigenza politica diretta ai sensi della Direttiva Quadro sulla Strategia per l'Ambiente Marino e della Strategia UE sulla Biodiversità 2030.\n",
        "\n",
        "Questo notebook percorre il flusso di lavoro di MHEAT su un **cubo SST sintetico incluso** in modo da poter eseguire ogni cella **senza un account Copernicus** o accesso a Internet. Lo stesso codice, invariato, funziona su dati reali del Copernicus Marine Service una volta forniti i credenziali — vedere la sezione finale.\n",
        "\n",
        "---\n",
        "\n",
        "## Dipendenze\n",
        "\n",
        "```bash\n",
        "pip install xarray numpy pandas matplotlib marineHeatWaves netCDF4\n",
        "# Opzionale, per recuperi in diretta dal CMS:\n",
        "pip install copernicusmarine\n",
        "```",
    ],
    "md-step1": [
        "## Passo 1 — Caricare un cubo SST del Mediterraneo\n",
        "\n",
        "Usiamo il cubo sintetico fornito con MHEAT. È un campo SST giornaliero di 3 anni (2020–2022) a 0,25° che copre la maggior parte del Mediterraneo occidentale e centrale, con un ciclo stagionale realistico e una forte anomalia calda iniettata in luglio–agosto 2022 per imitare l'ondata di calore storica di quell'estate.\n",
        "\n",
        "Il cubo ha forma ``(1096 giorni × 41 lat × 73 lon)``.",
    ],
    "md-step2": [
        "## Passo 2 — Climatologia stagionale + soglia al 90° percentile\n",
        "\n",
        "Selezioniamo il pixel più caldo per l'illustrazione, eseguiamo ``marineHeatWaves.detect()`` e tracciamo le tre curve di riferimento: la SST giornaliera, la media lisciata per giorno dell'anno (climatologia) e la soglia al 90° percentile. Le aree rosse ombreggiate sono le MHW che il rilevatore ha segnalato.",
    ],
    "md-step3": [
        "## Passo 3 — Rilevamento pixel per pixel + clustering con gli helper MHEAT\n",
        "\n",
        "Il backend incapsula ``marineHeatWaves.detect`` in ``detect_cube`` (pixel per pixel) e ``cluster_events`` (unisce i vicini spazio-temporali). Insieme trasformano migliaia di piccoli eventi per pixel in una manciata di poligoni di regione contigui — l'unità di analisi giusta per gli studi di impatto.",
    ],
    "md-map": [
        "## Passo 4 — Mappa spaziale della densità di eventi + istogramma di intensità"
    ],
    "md-impact": [
        "## Passo 5 — Giunzione con gli strati settoriali\n",
        "\n",
        "Il backend spedisce un piccolo pacchetto di siti di acquacoltura mediterranei, AMP marine Natura 2000 e poligoni di prateria di posidonia sotto ``backend/app/fixtures/overlays/``. ``attach_impact_properties`` riporta, per evento, quanti siti di acquacoltura sono colpiti e quanti km² di habitat AMP / posidonia il poligono dell'evento copre.",
    ],
    "md-edito": [
        "## Come eseguire questo notebook su EDITO Datalab\n",
        "\n",
        "1. Nel EDITO Datalab, avviare il servizio **JupyterLab** (qualsiasi immagine Python 3.11).\n",
        "2. Aprire un terminale ed eseguire `git clone https://github.com/<your-org>/mheat.git`, poi `cd mheat/tutorials` e aprire questo notebook.\n",
        "3. `pip install -r ../backend/requirements.txt` dall'ambiente del notebook (o integrare le dipendenze nella propria immagine).\n",
        "4. Per eseguire contro i **veri** dati Copernicus Marine, impostare `COPERNICUSMARINE_SERVICE_USERNAME` e `COPERNICUSMARINE_SERVICE_PASSWORD` nell'ambiente del notebook e sostituire il caricamento sintetico con:\n",
        "\n",
        "```python\n",
        "import copernicusmarine\n",
        "out = copernicusmarine.subset(\n",
        "    dataset_id='SST_MED_SST_L4_NRT_OBSERVATIONS_010_004',\n",
        "    minimum_longitude=-6, maximum_longitude=36.5,\n",
        "    minimum_latitude=30, maximum_latitude=46,\n",
        "    start_datetime='2022-05-01T00:00:00', end_datetime='2022-09-30T23:59:59',\n",
        "    variables=['analysed_sst'],\n",
        ")\n",
        "ds = xr.open_dataset(out)\n",
        "```\n",
        "\n",
        "5. Il resto del notebook viene eseguito senza modifiche. Il servizio MHEAT stesso (``docker compose up --build``) è anche ridistribuibile su un'istanza EDITO **Onyxia** — puntare `FRONTEND_DIR` al bundle Vite compilato ed esporre la porta 8000.\n",
        "\n",
        "## Citazioni\n",
        "\n",
        "* Hobday, A. J., Alexander, L. V., Perkins, S. E., Smale, D. A., *et al.* (2016). *A hierarchical approach to defining marine heatwaves.* **Progress in Oceanography**, 141, 227–238.\n",
        "* Hobday, A. J., Oliver, E. C. J., *et al.* (2018). *Categorizing and naming marine heatwaves.* **Oceanography**, 31(2), 162–173.\n",
        "* Oliver, E. C. J. (2019). `marineHeatWaves` — pacchetto Python per l'identificazione delle ondate di calore marine. https://github.com/ecjoliver/marineHeatWaves\n",
        "* Copernicus Marine Service — https://marine.copernicus.eu",
    ],
}


def translate(nb: dict, mapping: dict) -> dict:
    out = copy.deepcopy(nb)
    for c in out["cells"]:
        if c.get("cell_type") == "markdown" and c.get("id") in mapping:
            c["source"] = mapping[c["id"]]
    return out


def main() -> None:
    nb = json.loads(SRC.read_text(encoding="utf-8"))
    for lang, mapping in (("fr", FR), ("it", IT)):
        dst = SRC.with_name(f"mhw_mediterranean_{lang}.ipynb")
        translated = translate(nb, mapping)
        translated.setdefault("metadata", {})["mheat_locale"] = lang
        dst.write_text(
            json.dumps(translated, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"wrote {dst}")


if __name__ == "__main__":
    main()
