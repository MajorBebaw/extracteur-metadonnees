"""
Extracteur de métadonnées — outil OSINT
=======================================

Parcourt un dossier de photos et de documents (JPG, PDF, DOCX), en extrait
les métadonnées (auteur, logiciel, dates, appareil photo, GPS), calcule
l'empreinte SHA-256 de chaque fichier, exporte le tout en JSON et en CSV,
place les photos géolocalisées sur une carte HTML et résume ce que les
métadonnées révèlent. Récupère aussi, à la demande, les PDF qu'un domaine
a publiés, grâce aux archives de la Wayback Machine.

Utilisation :
    python extracteur.py <dossier>
    python extracteur.py <dossier> --sortie <préfixe>
"""

# Bibliothèque standard de Python
import argparse
import csv
import hashlib
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

# Bibliothèques externes (voir requirements.txt)
import docx  # le paquet s'installe sous le nom « python-docx »
import exifread
import folium
import requests
from pypdf import PdfReader

# pypdf affiche un avertissement pour chaque PDF abîmé. Le programme signale
# déjà ces fichiers dans le champ « erreur » : seules les erreurs graves restent.
logging.getLogger("pypdf").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Réglages : les constantes du programme
# ---------------------------------------------------------------------------

# Les extensions que l'on sait analyser (écrites en minuscules).
EXTENSIONS_SUPPORTEES = {".jpg", ".jpeg", ".pdf", ".docx"}

# Les métadonnées recherchées, quel que soit le type de fichier.
CHAMPS_METADONNEES = [
    "auteur",                # qui a créé le fichier
    "dernier_modificateur",  # DOCX : la dernière personne à l'avoir enregistré
    "logiciel",              # le logiciel d'origine (Word, Lightroom, iOS…)
    "producteur",            # PDF : l'outil qui a fabriqué le PDF
    "date_creation",         # au format ISO, par exemple "2024-07-14T22:58:10"
    "date_modification",     # au format ISO
    "appareil",              # JPG : marque et modèle de l'appareil photo
    "latitude",              # JPG : en degrés décimaux, négative au sud
    "longitude",             # JPG : en degrés décimaux, négative à l'ouest
]

# Les colonnes du fichier CSV, dans l'ordre : ce sont aussi les clés de
# chaque résultat produit par analyser_fichier().
COLONNES_CSV = ["fichier", "type", "taille_octets", "sha256"] + CHAMPS_METADONNEES + ["erreur", "chemin"]

# Débuts de texte qu'un tableur prend pour le début d'une formule (recommandation OWASP).
DEBUTS_DE_FORMULE = ("=", "+", "-", "@", "\t", "\r", "\n")

# Adresse de l'API CDX de la Wayback Machine.
URL_API_CDX = "https://web.archive.org/cdx/search/cdx"


# ---------------------------------------------------------------------------
# Fonctions utilitaires communes aux fonctions d'extraction
# ---------------------------------------------------------------------------

def metadonnees_vides():
    """Renvoie un dictionnaire où chaque champ de CHAMPS_METADONNEES vaut None.

    None signifie « pas trouvé ». Chaque fonction extraire_xxx() part de ce
    dictionnaire et remplit ce qu'elle trouve.
    """
    return {champ: None for champ in CHAMPS_METADONNEES}


def texte_ou_none(valeur):
    """Nettoie une valeur lue dans un fichier : renvoie un texte, ou None.

    - None reste None ;
    - le reste est converti en texte, débarrassé des caractères nuls et des
      espaces en début et en fin ;
    - un texte vide devient None.

    Exemples :
        texte_ou_none("  Canon  ")  ->  "Canon"
        texte_ou_none("")           ->  None
    """
    if valeur is None:
        return None
    texte = str(valeur).replace("\x00", "").strip()
    if texte == "":
        return None
    return texte


# ===========================================================================
# Recherche des fichiers à analyser
# ===========================================================================

def lister_fichiers(dossier):
    """Renvoie la liste triée des fichiers à analyser dans `dossier`.

    - `dossier` peut être un texte ou un objet Path ; ses sous-dossiers sont
      parcourus aussi.
    - Seuls les fichiers dont l'extension figure dans EXTENSIONS_SUPPORTEES
      sont retenus, majuscules comprises (« photo.JPG » est retenu).
    - Le résultat est une liste d'objets Path, triée par ordre alphabétique.
    """
    dossier = Path(dossier)
    fichiers = []
    for chemin in dossier.rglob("*"):
        if chemin.is_file() and chemin.suffix.lower() in EXTENSIONS_SUPPORTEES:
            fichiers.append(chemin)
    return sorted(fichiers)


# ===========================================================================
# Empreinte SHA-256
# ===========================================================================

def calculer_sha256(chemin):
    """Renvoie l'empreinte SHA-256 du fichier `chemin`, en hexadécimal.

    Le résultat est un texte de 64 caractères. Le fichier est lu en mode
    binaire, par blocs de 64 Ko, pour ne jamais charger un gros fichier en
    mémoire d'un seul coup.
    """
    empreinte = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        bloc = fichier.read(65536)
        while bloc:
            empreinte.update(bloc)  # Ajouter le bloc à l'empreinte
            bloc = fichier.read(65536)  # Lire le bloc suivant
    return empreinte.hexdigest()


# ===========================================================================
# Conversion des coordonnées GPS et des dates EXIF
# ===========================================================================

def dms_vers_decimal(degres, minutes, secondes, reference):
    """Convertit une coordonnée degrés-minutes-secondes en degrés décimaux.

    - `degres`, `minutes` et `secondes` sont des nombres.
    - `reference` est la lettre de l'hémisphère : "N", "S", "E" ou "W".
    - Au sud ("S") et à l'ouest ("W"), le résultat est négatif.
    - Le résultat est arrondi à 6 décimales (environ 10 cm sur le terrain).

    Exemple :
        dms_vers_decimal(48, 51, 30.24, "N")  ->  48.8584
    """
    decimal = degres + minutes / 60 + secondes / 3600
    if reference in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def date_exif_vers_iso(texte):
    """Convertit une date au format EXIF en date au format ISO 8601.

    - Format EXIF :  "2024:07:14 22:58:10"
    - Format ISO :   "2024-07-14T22:58:10"
    - Renvoie None si `texte` est None, vide, ou n'est pas une date valide
      (par exemple "0000:00:00 00:00:00").
    """
    if not texte:
        return None
    try:
        return datetime.strptime(texte, "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


# ===========================================================================
# Photos : métadonnées EXIF
# ===========================================================================

def extraire_jpg(chemin):
    """Lit les métadonnées EXIF d'une photo JPG et renvoie un dictionnaire.

    Le dictionnaire part de metadonnees_vides(). Champs remplis :
      - auteur            <- tag "Image Artist"
      - logiciel          <- tag "Image Software"
      - date_creation     <- tag "EXIF DateTimeOriginal", au format ISO
      - date_modification <- tag "Image DateTime", au format ISO
      - appareil          <- tags "Image Make" et "Image Model" réunis
      - latitude          <- tags "GPS GPSLatitude" et "GPS GPSLatitudeRef"
      - longitude         <- tags "GPS GPSLongitude" et "GPS GPSLongitudeRef"

    Une photo sans EXIF renvoie un dictionnaire dont tous les champs valent None.
    """
    meta = metadonnees_vides()
    with open(chemin, "rb") as f:
        tags = exifread.process_file(f, details=False)
        meta["auteur"] = texte_ou_none(tags.get("Image Artist"))
        meta["logiciel"] = texte_ou_none(tags.get("Image Software"))
        meta["date_creation"] = date_exif_vers_iso(texte_ou_none(tags.get("EXIF DateTimeOriginal")))
        meta["date_modification"] = date_exif_vers_iso(texte_ou_none(tags.get("Image DateTime")))

        make = texte_ou_none(tags.get("Image Make"))
        model = texte_ou_none(tags.get("Image Model"))
        if make and model:
            meta["appareil"] = f"{make} {model}"
        elif make:
            meta["appareil"] = make
        elif model:
            meta["appareil"] = model

        lat = tags.get("GPS GPSLatitude")
        lat_ref = texte_ou_none(tags.get("GPS GPSLatitudeRef"))
        lon = tags.get("GPS GPSLongitude")
        lon_ref = texte_ou_none(tags.get("GPS GPSLongitudeRef"))

        if lat and lat_ref and lon and lon_ref:
            lat_deg, lat_min, lat_sec = [float(v) for v in lat.values]
            lon_deg, lon_min, lon_sec = [float(v) for v in lon.values]
            meta["latitude"] = dms_vers_decimal(lat_deg, lat_min, lat_sec, lat_ref)
            meta["longitude"] = dms_vers_decimal(lon_deg, lon_min, lon_sec, lon_ref)

    return meta


# ===========================================================================
# Documents PDF
# ===========================================================================

def extraire_pdf(chemin):
    """Lit les métadonnées d'un fichier PDF et renvoie un dictionnaire.

    Le dictionnaire part de metadonnees_vides(). Champs remplis :
      - auteur            <- /Author
      - logiciel          <- /Creator       le logiciel d'origine, par exemple Word
      - producteur        <- /Producer      l'outil qui a fabriqué le PDF
      - date_creation     <- /CreationDate  au format ISO
      - date_modification <- /ModDate       au format ISO

    Un PDF sans métadonnées renvoie un dictionnaire de None. Un PDF illisible
    lève une exception, que analyser_fichier() intercepte.
    """
    meta = metadonnees_vides()
    infos = PdfReader(chemin).metadata

    if infos is None:
        return meta

    meta["auteur"] = texte_ou_none(infos.author)
    meta["logiciel"] = texte_ou_none(infos.creator)
    meta["producteur"] = texte_ou_none(infos.producer)

    if infos.creation_date:
        meta["date_creation"] = infos.creation_date.isoformat()
    if infos.modification_date:
        meta["date_modification"] = infos.modification_date.isoformat()

    return meta


# ===========================================================================
# Documents Word (DOCX)
# ===========================================================================

def extraire_docx(chemin):
    """Lit les propriétés d'un document Word (.docx) et renvoie un dictionnaire.

    Le dictionnaire part de metadonnees_vides(). Champs remplis :
      - auteur               <- core_properties.author
      - dernier_modificateur <- core_properties.last_modified_by
      - date_creation        <- core_properties.created, au format ISO
      - date_modification    <- core_properties.modified, au format ISO
      - logiciel             <- docProps/app.xml, lu par lire_logiciel_docx()
    """
    meta = metadonnees_vides()
    infos = docx.Document(chemin).core_properties

    meta["auteur"] = texte_ou_none(infos.author)
    meta["dernier_modificateur"] = texte_ou_none(infos.last_modified_by)
    if infos.created:
        meta["date_creation"] = infos.created.isoformat()
    if infos.modified:
        meta["date_modification"] = infos.modified.isoformat()

    meta["logiciel"] = lire_logiciel_docx(chemin)

    return meta


def lire_logiciel_docx(chemin):
    """Renvoie le logiciel qui a enregistré le .docx, ou None.

    Un fichier .docx est une archive ZIP. Le fichier docProps/app.xml qu'elle
    contient indique le logiciel (<Application>) et sa version (<AppVersion>),
    par exemple "Microsoft Office Word 16.0000". Renvoie None si l'information
    est absente ou illisible.
    """
    try:
        with zipfile.ZipFile(chemin, "r") as docx_zip:
            if "docProps/app.xml" not in docx_zip.namelist():
                return None

            contenu_xml = docx_zip.read("docProps/app.xml")
            racine = ET.fromstring(contenu_xml)
            application = texte_ou_none(racine.findtext("{*}Application"))
            version = texte_ou_none(racine.findtext("{*}AppVersion"))

            if application and version:
                return f"{application} {version}"
            return application
    except Exception:
        return None


# Table de correspondance : extension -> fonction d'extraction.
# En Python, une fonction peut être rangée dans un dictionnaire puis appelée
# plus tard : EXTRACTEURS[".pdf"](chemin) revient à écrire extraire_pdf(chemin).
EXTRACTEURS = {
    ".jpg": extraire_jpg,
    ".jpeg": extraire_jpg,
    ".pdf": extraire_pdf,
    ".docx": extraire_docx,
}


# ===========================================================================
# Analyse d'un fichier et d'un dossier
# ===========================================================================

def analyser_fichier(chemin):
    """Analyse un fichier et renvoie un dictionnaire complet.

    Le dictionnaire contient toutes les clés de COLONNES_CSV :
      - fichier        le nom du fichier, par exemple "IMG_0412.jpg"
      - type           l'extension en minuscules, sans le point, par exemple "jpg"
      - taille_octets  la taille du fichier, en octets
      - sha256         l'empreinte SHA-256 du fichier
      - les champs de métadonnées, remplis par la fonction d'extraction adaptée
      - erreur         None si tout va bien, sinon la description du problème
      - chemin         le chemin du fichier, sous forme de texte

    La fonction ne plante jamais : si l'extraction échoue (fichier corrompu…),
    le résultat garde le nom, la taille et l'empreinte, et le champ « erreur »
    décrit le problème, par exemple "PdfStreamError : Stream has ended unexpectedly".
    """
    fichier = Path(chemin)
    resultat = {colonne: None for colonne in COLONNES_CSV}
    resultat["fichier"] = fichier.name
    resultat["type"] = fichier.suffix.lower().lstrip(".")
    resultat["chemin"] = str(fichier)

    try:
        resultat["taille_octets"] = fichier.stat().st_size
        resultat["sha256"] = calculer_sha256(fichier)
        metadonnees = EXTRACTEURS[fichier.suffix.lower()](chemin)  # Appelle la fonction d'extraction appropriée
        resultat.update(metadonnees)

    except Exception as erreur:
        resultat["erreur"] = f"{type(erreur).__name__} : {str(erreur)}"

    return resultat


def analyser_dossier(dossier):
    """Analyse tous les fichiers renvoyés par lister_fichiers(dossier).

    Renvoie la liste des dictionnaires produits par analyser_fichier(),
    dans le même ordre que lister_fichiers().
    """
    resultats = []
    for fichier in lister_fichiers(dossier):
        resultat = analyser_fichier(fichier)
        resultats.append(resultat)
    return resultats


# ===========================================================================
# Exports JSON et CSV
# ===========================================================================

def neutraliser_formule(valeur):
    """Empêche un tableur d'exécuter une métadonnée comme une formule.

    Un texte qui commence par =, +, -, @, une tabulation ou un saut de ligne
    reçoit une apostrophe devant : Excel l'affiche alors comme du texte.
    Les nombres (comme une longitude de -1.5115) et None restent inchangés.
    """
    if isinstance(valeur, str) and valeur.startswith(DEBUTS_DE_FORMULE):
        return "'" + valeur
    return valeur


def exporter_json(resultats, chemin_sortie):
    """Écrit `resultats` (une liste de dictionnaires) dans un fichier JSON.

    Le fichier est encodé en UTF-8, garde les accents tels quels
    (ensure_ascii=False) et reste lisible par un humain (indent=2).
    """
    with open(chemin_sortie, "w", encoding="utf-8") as fichier:
        json.dump(resultats, fichier, ensure_ascii=False, indent=2)


def exporter_csv(resultats, chemin_sortie):
    """Écrit `resultats` dans un fichier CSV, une ligne par fichier analysé.

    - Colonnes : celles de COLONNES_CSV, dans cet ordre, sous une ligne d'en-tête.
    - Séparateur « ; » et encodage "utf-8-sig" : le format qu'attend Excel en
      français, accents compris.
    - newline="" évite une ligne vide sur deux sous Windows.
    - Une valeur None devient une case vide.
    - Chaque valeur passe par neutraliser_formule(), pour qu'un tableur
      n'exécute aucune formule cachée dans une métadonnée.
    """
    with open(chemin_sortie, "w", encoding="utf-8-sig", newline="") as fichier:
        ecrivain = csv.DictWriter(fichier, fieldnames=COLONNES_CSV, delimiter=";")
        ecrivain.writeheader()
        for fiche in resultats:
            ligne = {cle: neutraliser_formule(valeur) for cle, valeur in fiche.items()}
            ecrivain.writerow(ligne)


# ===========================================================================
# Carte des photos géolocalisées
# ===========================================================================

def creer_carte(resultats, chemin_sortie):
    """Crée une carte HTML avec un marqueur par fichier géolocalisé.

    - Les résultats sans latitude ou sans longitude sont ignorés.
    - La fenêtre de chaque marqueur affiche le nom du fichier, l'appareil et
      la date de création.
    - La carte est cadrée pour que tous les marqueurs soient visibles.
    - Les métadonnées affichées sont échappées avec html.escape(), car elles
      proviennent de fichiers non fiables.

    Enregistre la carte dans `chemin_sortie` et renvoie le nombre de marqueurs.
    Si aucun fichier n'est géolocalisé, aucune carte n'est créée et la
    fonction renvoie 0.
    """
    points = []
    for fiche in resultats:
        lat = fiche.get("latitude")
        lon = fiche.get("longitude")
        if lat is not None and lon is not None:
            points.append(fiche)
    if not points:
        return 0  # Aucun fichier géolocalisé

    carte = folium.Map()
    for fiche in points:
        nom = html.escape(fiche["fichier"])
        appareil = html.escape(fiche.get("appareil") or "inconnu")
        date = html.escape(fiche.get("date_creation") or "inconnue")
        folium.Marker(
            location=[fiche["latitude"], fiche["longitude"]],
            popup=f"Fichier: {nom}<br>Appareil: {appareil}<br>Date de création: {date}",
            tooltip=nom,
        ).add_to(carte)
    carte.fit_bounds([[fiche["latitude"], fiche["longitude"]] for fiche in points], max_zoom=15)
    carte.save(chemin_sortie)
    return len(points)


# ===========================================================================
# Synthèse et ligne de commande
# ===========================================================================

def synthese(resultats):
    """Résume ce que les métadonnées révèlent : le point de vue de l'analyste.

    Renvoie un dictionnaire :
      {
        "personnes": [...],     # auteurs et derniers modificateurs, sans doublon
        "logiciels": [...],     # logiciels et producteurs, sans doublon
        "appareils": [...],     # appareils photo, sans doublon
        "nb_geolocalises": 3,   # nombre de fichiers avec latitude et longitude
        "nb_erreurs": 1,        # nombre de fichiers dont le champ « erreur » est rempli
      }
    Les trois listes sont triées par ordre alphabétique et ne contiennent pas None.
    """
    personnes = set()
    logiciels = set()
    appareils = set()
    nb_geolocalises = 0
    nb_erreurs = 0

    for fiche in resultats:
        for champ in ["auteur", "dernier_modificateur"]:
            if fiche.get(champ):
                personnes.add(fiche[champ])
        for champ in ["logiciel", "producteur"]:
            if fiche.get(champ):
                logiciels.add(fiche[champ])
        if fiche.get("appareil"):
            appareils.add(fiche["appareil"])
        if fiche.get("latitude") is not None and fiche.get("longitude") is not None:
            nb_geolocalises += 1
        if fiche.get("erreur"):
            nb_erreurs += 1
    return {
        "personnes": sorted(personnes),
        "logiciels": sorted(logiciels),
        "appareils": sorted(appareils),
        "nb_geolocalises": nb_geolocalises,
        "nb_erreurs": nb_erreurs,
    }


def main():
    """Point d'entrée en ligne de commande.

    Utilisation :
        python extracteur.py echantillons
        python extracteur.py echantillons --sortie enquete

    Analyse le dossier indiqué, écrit <sortie>.json, <sortie>.csv et
    <sortie>_carte.html (préfixe "rapport" par défaut), puis affiche la synthèse.
    """
    parser = argparse.ArgumentParser(description="Extracteur de métadonnées")
    parser.add_argument("dossier", help="Dossier à analyser")
    parser.add_argument("--sortie", default="rapport", help="Préfixe des fichiers de sortie (JSON, CSV, carte)")
    args = parser.parse_args()

    resultats = analyser_dossier(args.dossier)
    exporter_json(resultats, f"{args.sortie}.json")
    exporter_csv(resultats, f"{args.sortie}.csv")
    nb_marqueurs = creer_carte(resultats, f"{args.sortie}_carte.html")

    print(f"Analyse terminée. {len(resultats)} fichiers analysés.")
    print(f"Fichiers produits : {args.sortie}.json, {args.sortie}.csv, {args.sortie}_carte.html ({nb_marqueurs} marqueurs).")
    print(synthese(resultats))


# ===========================================================================
# Archives de la Wayback Machine
# ===========================================================================

def construire_url_cdx(domaine, limite=50):
    """Construit l'URL de l'API CDX qui liste les PDF archivés d'un domaine.

    Paramètres de la requête :
      url=<domaine>, matchType=domain   le domaine et ses sous-domaines
      output=json                       une réponse en JSON
      fl=timestamp,original             date de capture et adresse d'origine
      filter=mimetype:application/pdf   seulement les PDF
      filter=statuscode:200             seulement les captures réussies
      collapse=digest                   une seule copie par contenu identique
      limit=<limite>                    nombre maximal de résultats

    Les paramètres sont passés à urlencode() sous forme de liste de couples,
    ce qui permet de répéter la clé « filter ».
    """
    parametres = [
        ("url", domaine),
        ("matchType", "domain"),
        ("output", "json"),
        ("fl", "timestamp,original"),
        ("filter", "mimetype:application/pdf"),
        ("filter", "statuscode:200"),
        ("collapse", "digest"),
        ("limit", limite),
    ]
    return URL_API_CDX + "?" + urlencode(parametres)


def lire_reponse_cdx(donnees):
    """Transforme la réponse JSON de l'API CDX en liste de dictionnaires.

    `donnees` est une liste de listes dont la première ligne contient le nom
    des colonnes, par exemple ["timestamp", "original"]. Chaque valeur est
    lue d'après sa colonne, quel que soit l'ordre renvoyé par le service.
    Chaque capture devient un dictionnaire :
      {"horodatage": "20190612083512",
       "url_originale": "https://www.example.com/docs/rapport.pdf",
       "url_archive": "https://web.archive.org/web/20190612083512id_/https://www.example.com/docs/rapport.pdf"}

    Le suffixe id_ demande le fichier d'origine, sans la barre de navigation
    de la Wayback Machine. Une réponse vide ([]) donne une liste vide.
    """
    if not donnees:
        return []
    entete = donnees[0]
    archives = []
    for ligne in donnees[1:]:
        enregistrement = dict(zip(entete, ligne))
        horodatage = enregistrement["timestamp"]
        url = enregistrement["original"]
        archives.append({
            "horodatage": horodatage,
            "url_originale": url,
            "url_archive": f"https://web.archive.org/web/{horodatage}id_/{url}",
        })
    return archives


def telecharger_archives(domaine, dossier_sortie, limite=20):
    """Télécharge les PDF archivés d'un domaine dans `dossier_sortie`.

    1. Interroge l'API CDX, à l'adresse construite par construire_url_cdx().
    2. Lit la réponse avec lire_reponse_cdx().
    3. Télécharge chaque capture sous un nom de fichier sûr, préfixé par sa
       date de capture.
    4. Marque une pause d'une seconde entre deux téléchargements, par respect
       pour ce service gratuit.

    Renvoie la liste des fichiers enregistrés. Pour les analyser ensuite :
        python extracteur.py <dossier_sortie>
    """
    dossier = Path(dossier_sortie)
    dossier.mkdir(parents=True, exist_ok=True)
    entetes = {"User-Agent": "extracteur-metadonnees/1.0 (projet d'apprentissage)"}
    reponse = requests.get(construire_url_cdx(domaine, limite), headers=entetes, timeout=60)
    reponse.raise_for_status()
    fichiers = []
    for archive in lire_reponse_cdx(reponse.json()):
        nom = Path(urlparse(archive["url_originale"]).path).name or "document.pdf"
        nom = re.sub(r"[^\w.-]", "_", nom)
        if not nom.lower().endswith(".pdf"):
            nom += ".pdf"
        destination = dossier / f"{archive['horodatage']}_{nom}"
        telechargement = requests.get(archive["url_archive"], headers=entetes, timeout=60)
        if telechargement.ok:
            destination.write_bytes(telechargement.content)
            fichiers.append(destination)
        time.sleep(1)
    return fichiers


if __name__ == "__main__":
    main()
