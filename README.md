# Extracteur de métadonnées

Outil Python d'analyse de métadonnées pour l'OSINT. Il parcourt un dossier de photos et de documents (JPG, PDF, DOCX) et extrait ce que les fichiers révèlent de leurs auteurs : noms, logiciels, dates, appareil photo et coordonnées GPS. Il calcule l'empreinte SHA-256 de chaque fichier pour la traçabilité, puis produit un export JSON, un tableau CSV prêt pour Excel et une carte interactive des photos géolocalisées.

Une fonction complémentaire récupère les PDF qu'un domaine a publiés au fil des ans, y compris ceux retirés depuis, grâce aux archives de la Wayback Machine.

Ce projet d'apprentissage a été réalisé pas à pas, avec un accompagnement par l'IA : voir [Comment ce projet a été réalisé](#comment-ce-projet-a-été-réalisé).

## Fonctionnalités

- **Parcours récursif** d'un dossier : JPG/JPEG, PDF et DOCX, quelle que soit la casse de l'extension
- **Photos** : appareil, logiciel, auteur, dates de prise de vue et de modification, coordonnées GPS converties en degrés décimaux
- **PDF** : auteur, logiciel d'origine (`/Creator`), outil qui a produit le PDF (`/Producer`), dates
- **Documents Word** : auteur, dernier modificateur, dates, logiciel et version (lus dans `docProps/app.xml`)
- **Traçabilité** : empreinte SHA-256 de chaque fichier
- **Robustesse** : un fichier illisible est signalé, sans interrompre l'analyse
- **Exports** : JSON, et CSV au format attendu par Excel en français (séparateur « ; », UTF-8 avec BOM)
- **Carte** HTML interactive (folium / Leaflet), cadrée automatiquement sur les photos
- **Synthèse** : personnes, logiciels et appareils identifiés
- **Archives** : récupération des PDF d'un domaine via l'API CDX de la Wayback Machine
- **Sécurité** : métadonnées échappées dans la carte HTML (contre le XSS) et neutralisées dans le CSV (contre l'injection de formules)

## Installation

Python 3.10 ou plus récent.

Télécharge le projet avec le bouton vert « Code » de cette page (« Download ZIP » ou `git clone`), puis, dans le dossier du projet :

```bash
python -m venv .venv
source .venv/bin/activate        # Windows, Git Bash : source .venv/Scripts/activate
                                 # Windows, PowerShell : .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Utilisation

```bash
python extracteur.py echantillons
python extracteur.py "C:\\Users\chemin/nom du dossier1" --sortie "C:\\Users\chemin\nom du dossier2"
```
La première ligne analyse le dossier "echantillons" qui est situé dans le dosier "extracteur-metadonnees".
La seconde ligne est a utiliser si tu souhaites préciser le chemin où tes fichiers à extraire sont placés. 
Le programme écrit `<sortie>.json`, `<sortie>.csv` et `<sortie>_carte.html`. Par défaut, `<sortie>` vaut `rapport`.

Exemple sur les fichiers d'échantillon fournis :

```text
Analyse terminée. 7 fichiers analysés.
Fichiers produits : rapport.json, rapport.csv, rapport_carte.html (3 marqueurs).
{'personnes': ['Camille Martin', 'Jean Dupont', 'Marie Lefèvre', 'Thomas Bernard'], 'logiciels': ['17.5.1', 'Adobe Photoshop Lightroom Classic 13.1 (Windows)', 'ILCE-7M4 v2.00', 'Microsoft Office Word 16.0000', 'Microsoft Word', 'macOS Version 13.6.1 (Build 22G313) Quartz PDFContext'], 'appareils': ['Apple iPhone 15 Pro', 'FUJIFILM X-T5', 'SONY ILCE-7M4'], 'nb_geolocalises': 3, 'nb_erreurs': 1}
```

### Afficher la carte

Selon le navigateur, OpenStreetMap peut refuser le fond de carte d'une page ouverte directement depuis le disque (adresse en `file://`). Dans ce cas, sers la page avec le petit serveur web de Python:
```bash
python -m http.server 8000 --bind 127.0.0.1
```
puis ouvre `http://127.0.0.1:8000/rapport_carte.html` :


### Récupérer les PDF archivés d'un domaine

Remplace `example.com` par le domaine étudié :

```bash
python -c "from extracteur import telecharger_archives; telecharger_archives('example.com', 'archives_example', limite=20)"
python extracteur.py archives_example --sortie archives_example/rapport
```

Le programme marque une pause d'une seconde entre deux téléchargements, par respect pour ce service gratuit de l'Internet Archive.

## Pourquoi c'est utile en OSINT

La plupart des grands réseaux sociaux suppriment les métadonnées EXIF des photos au moment de la publication. Une image récupérée sur un réseau social ne contient donc généralement plus ni coordonnées GPS ni modèle d'appareil. Certaines plateformes de partage de photos, ou l'envoi d'une image « en tant que fichier » par une messagerie, conservent en revanche souvent l'original.

L'intérêt principal de l'outil porte sur les documents publiés directement par les organisations : rapports annuels, plaquettes, appels d'offres, comptes rendus. Leurs métadonnées révèlent souvent :

- des noms d'employés (auteur, dernier modificateur), utiles pour cartographier une organisation ou anticiper une campagne d'ingénierie sociale ;
- des identifiants de session Windows laissés dans le champ auteur, qui trahissent le format des comptes internes ;
- les logiciels utilisés et leur version exacte, qui indiquent si le parc informatique est à jour.

Les archives de la Wayback Machine ajoutent une dimension temporelle : un document retiré d'un site reste souvent consultable, métadonnées comprises.

## Limites et éthique

- Une métadonnée se falsifie en quelques lignes de code : les fichiers d'échantillon de ce projet en sont la preuve. Elle oriente une enquête, elle ne prouve rien à elle seule ; recoupe-la avec d'autres sources.
- L'absence de métadonnées ne prouve rien non plus : de nombreux outils les suppriment automatiquement.
- Noms, dates et positions sont des données personnelles. Leur traitement relève du RGPD : finalité définie, minimisation, durée de conservation limitée. Le `.gitignore` du projet écarte les résultats d'analyse pour qu'ils ne soient jamais publiés par erreur.
- N'analyse que des fichiers publics, ou des fichiers que tu es autorisé à examiner.
- Les métadonnées viennent de fichiers non fiables : elles sont échappées avant d'être affichées dans la carte HTML, et neutralisées dans le CSV pour qu'Excel n'exécute aucune formule.
- Discrétion : ouvrir la carte contacte les serveurs d'OpenStreetMap et plusieurs réseaux de diffusion de contenu (CDN), et interroger la Wayback Machine révèle le domaine étudié. Pour une enquête sensible, prends les précautions adaptées.

## Comment ce projet a été réalisé

Ce projet d'apprentissage a été guidé pas à pas. La structure du programme et le cahier des charges de chaque fonction m'ont été fournis sous forme d'exercices progressifs, avec des fichiers d'échantillon aux métadonnées connues et un vérificateur automatique (non inclus dans ce dépôt).

J'ai écrit le code des fonctions moi-même, exercice après exercice, en m'appuyant sur des explications, des indices et des relectures. Pour quelques parties (la création des marqueurs de la carte, la neutralisation des formules dans le CSV et la récupération des archives de la Wayback Machine), j'ai étudié une correction ou une solution commentée avant de l'intégrer.

## Ce que j'ai appris

- Manipuler fichiers et dossiers avec `pathlib`, et lire des formats binaires (EXIF, PDF, et DOCX, qui est en réalité une archive ZIP) avec des bibliothèques spécialisées
- Structurer des données en listes et en dictionnaires, puis les exporter en JSON et en CSV, en tenant compte des encodages et des habitudes d'Excel
- Gérer les erreurs avec `try` / `except` sans interrompre un traitement, et repérer quand un `except` trop large cache un bug
- Lire un message d'erreur Python et déboguer pas à pas, avec `print()` et le débogueur de VS Code
- Construire une interface en ligne de commande avec `argparse`
- Interroger une API web avec `requests`, en restant courtois envers le service (identification, délais, limites)
- Penser sécurité dès la conception : traiter toute donnée extraite comme non fiable, et la neutraliser selon sa destination (page HTML, tableur)

## Structure du projet

```text
extracteur-metadonnees/
├── extracteur.py      le programme
├── requirements.txt   les dépendances
├── echantillons/      des fichiers fictifs aux métadonnées connues
├── LICENSE
└── README.md
```

## Pistes d'amélioration

- Écrire une suite de tests automatisés avec `pytest`
- Créer automatiquement le dossier indiqué par `--sortie`
- Afficher la synthèse sous une forme plus lisible
- Ajouter une option de ligne de commande pour la Wayback Machine
- Lire les métadonnées XMP des PDF, ainsi que les versions précédentes conservées par les enregistrements incrémentaux
- Prendre en charge d'autres formats : HEIC, PNG, XLSX, PPTX
- Détecter les incohérences : dates contradictoires, logiciel de retouche, fuseau horaire inattendu

## Licence

Distribué sous licence MIT : voir le fichier [LICENSE](LICENSE).
