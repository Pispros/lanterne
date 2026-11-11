#!/usr/bin/env python3
"""
Lanterne — lanceur de jeux Windows pour Bazzite (GNOME).

Proton sans Steam, grâce à umu-launcher. Interface GTK 4 + libadwaita.
Données :
  ~/.config/lanterne/bibliotheque.json   bibliothèque et réglages
  ~/.local/share/lanterne/               Proton, jaquettes, journaux, umu
  ~/Games/Lanterne/<jeu>/                préfixes Wine (un par jeu)
"""

import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from datetime import datetime
from pathlib import Path, PurePosixPath

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

APP_ID = "fr.perso.Lanterne"
VERSION = "1.7"

# ─────────────────────────────────────────────────────────────── chemins ──

HOME = Path.home()
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "lanterne"
DATA_DIR = Path(GLib.get_user_data_dir()) / "lanterne"
LIB_FILE = CONFIG_DIR / "bibliotheque.json"
PROTON_DIR = DATA_DIR / "proton"
COVERS_DIR = DATA_DIR / "jaquettes"
LOGS_DIR = DATA_DIR / "journaux"
DL_DIR = DATA_DIR / "telechargements"
UMU_DIR = DATA_DIR / "umu"
BACKUPS_DIR = DATA_DIR / "sauvegardes-mises-de-cote"
PREVIEWS_DIR = DATA_DIR / "apercus"
UMU_RUNTIME_DIR = Path(GLib.get_user_data_dir()) / "umu"
PREFIXES_DIR = HOME / "Games" / "Lanterne"
APPS_DIR = Path(GLib.get_user_data_dir()) / "applications"
STEAM_COMPAT = HOME / ".local/share/Steam/compatibilitytools.d"

# Dossiers où des versions de Proton peuvent déjà se trouver (ProtonPlus,
# ProtonUp-Qt, téléchargements automatiques d'umu…). On ne fait que les lire.
EXTRA_PROTON_DIRS = [
    STEAM_COMPAT,
    HOME / ".steam/root/compatibilitytools.d",
    HOME / ".var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d",
    Path("/usr/share/steam/compatibilitytools.d"),
]

# Installations possibles de Steam, lues seulement pour importer des sauvegardes.
STEAM_ROOTS = [
    HOME / ".local/share/Steam",
    HOME / ".steam/steam",
    HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    HOME / ".var/app/com.valvesoftware.Steam/data/Steam",
]
STORE_SEARCH = "https://store.steampowered.com/api/storesearch/?l=english&cc=FR&term="

GE_API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases?per_page=8"
UMU_API = "https://api.github.com/repos/Open-Wine-Components/umu-launcher/releases/latest"

# ──────────────────────────────────────────────────────────────── langue ──
# Les textes du code sont en français ; EN (plus bas) les traduit en anglais.
# Langue : LANTERNE_LANG=fr|en, sinon le réglage de l'appli, sinon la langue du système.

def _detect_lang():
    forced = os.environ.get("LANTERNE_LANG", "")
    if not forced:
        try:
            forced = json.loads(LIB_FILE.read_text(encoding="utf-8")).get("settings", {}).get("language", "")
        except (OSError, ValueError, AttributeError):
            forced = ""
    if forced in ("fr", "en"):
        return forced
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            return "fr" if val.split(":")[0].lower().startswith("fr") else "en"
    return "en"


LANG = _detect_lang()


def _(text):
    return text if LANG == "fr" else EN.get(text, text)


def _n(singular, plural, n):
    if LANG == "fr":
        return singular if abs(n) <= 1 else plural
    return EN.get(singular, singular) if n == 1 else EN.get(plural, plural)


# Traductions anglaises (clé : texte français du code).
EN = {
    'Trouvée automatiquement, ou une image verticale de 600 × 900': 'Found automatically, or a portrait image of 600 × 900',
    'jeu Steam': 'Steam game',
    'jeu non-Steam ajouté à Steam': 'non-Steam game added to Steam',
    'préfixe Steam': 'Steam prefix',
    'Jamais': 'Never',
    'Aujourd’hui': 'Today',
    'Hier': 'Yesterday',
    'GitHub est injoignable. Vérifie ta connexion à Internet.': 'GitHub can’t be reached. Check your internet connection.',
    'GE-Proton auto': 'GE-Proton auto',
    'Toujours la dernière GE-Proton (auto)': 'Always the latest GE-Proton (auto)',
    'ton dossier personnel tout entier': 'your entire home folder',
    'un chemin contenant « : », non pris en charge': 'a path containing “:”, which isn’t supported',
    'Premier lancement : umu télécharge ses composants, compte quelques minutes.': 'First launch: umu is downloading its components, this takes a few minutes.',
    'ajouté': 'added',
    'Jeux non-Steam trouvés dans Steam :': 'Non-Steam games found in Steam:',
    'Dossiers Steam :': 'Steam folders:',
    'Bibliothèques :': 'Libraries:',
    'Ses fichiers seront effacés du disque.': 'Its files will be deleted from disk.',
    'Moins d’une minute': 'Less than a minute',
    'Jamais lancé': 'Never played',
    'Nouveau jeu': 'New game',
    'GitHub limite les requêtes pour le moment. Réessaie dans quelques minutes.': 'GitHub is rate-limiting requests right now. Try again in a few minutes.',
    'Extraction…': 'Extracting…',
    'La dernière version d’umu-launcher ne propose pas d’archive « zipapp ».': 'The latest umu-launcher release doesn’t include a “zipapp” archive.',
    'umu-launcher est introuvable.': 'umu-launcher can’t be found.',
    'Aucun exécutable n’est défini pour ce jeu.': 'No executable is set for this game.',
    'Aucun préfixe Wine n’est défini pour ce jeu.': 'No Wine prefix is set for this game.',
    'ajouté avec tout son préfixe Steam': 'added with its whole Steam prefix',
    '  aucun nouveau jeu à ajouter': '  no new games to add',
    '  aucun jeu dans Lanterne': '  no games in Lanterne',
    '  aucun trouvé parmi :': '  none found among:',
    'Cette archive ne contient aucune sauvegarde.': 'This archive doesn’t contain any saves.',
    'Programmes Windows': 'Windows programs',
    'Tous les fichiers': 'All files',
    'Choisir…': 'Choose…',
    'Lancer un installeur…': 'Run an installer…',
    'Recherche d’une jaquette sur Steam…': 'Looking for cover art on Steam…',
    'Recherche du jeu dans Steam…': 'Looking for the game in Steam…',
    'Installeur lancé. Une fois l’installation finie, choisis l’exécutable du jeu.': 'Installer started. When it’s done, choose the game’s executable.',
    'Installation terminée : choisis maintenant l’exécutable du jeu.': 'Installation finished: now choose the game’s executable.',
    'Ajouter un jeu': 'Add a game',
    'En cours': 'Running',
    'Annuler': 'Cancel',
    'Supprimer': 'Delete',
    'Préparation…': 'Preparing…',
    'Utiliser pour tous les jeux': 'Use for all games',
    'Bibliothèque': 'Library',
    'Importer mes jeux depuis Steam…': 'Import my games from Steam…',
    'Isoler tous mes jeux': 'Isolate all my games',
    'Ouvrir le dossier des préfixes': 'Open prefixes folder',
    'Ouvrir le dossier des journaux': 'Open logs folder',
    'À propos de Lanterne': 'About Lanterne',
    'Le jeu': 'The game',
    'OK': 'OK',
    'Test de l’isolation en cours… (quelques secondes, plus au premier lancement)': 'Testing isolation… (a few seconds, longer on first launch)',
    '\n\nLes sauvegardes actuelles seront mises de côté, puis remplacées.': '\n\nYour current saves will be set aside, then replaced.',
    'Import depuis Steam en cours…': 'Importing from Steam…',
    'Plus tard': 'Later',
    'Installer umu-launcher': 'Install umu-launcher',
    'Lance directement un jeu de la bibliothèque': 'Launch a game from the library directly',
    'Lanterne nécessite libadwaita 1.5 ou plus récent.': 'Lanterne requires libadwaita 1.5 or newer.',
    'Il y a {n} jours': '{n} days ago',
    'GitHub a répondu avec l’erreur {0}.': 'GitHub returned error {0}.',
    'Vérification…': 'Verifying…',
    'umu-run est absent de l’archive téléchargée.': 'umu-run is missing from the downloaded archive.',
    'Des guillemets sont mal fermés dans les arguments ou les variables.': 'Quotes aren’t closed properly in the arguments or variables.',
    '  ✓ {name} : {n} fichier ({size}) depuis {src} ({kind})': '  ✓ {name}: {n} file ({size}) from {src} ({kind})',
    '  ✓ {name} : {n} fichiers ({size}) depuis {src} ({kind})': '  ✓ {name}: {n} files ({size}) from {src} ({kind})',
    'Jeux non-Steam dans Steam : {0}': 'Non-Steam games in Steam: {0}',
    'Jeux dans Lanterne : {0}': 'Games in Lanterne: {0}',
    'Ce jeu n’a aucune sauvegarde à exporter.': 'This game has no saves to export.',
    'Ce fichier n’est pas une archive .zip valide.': 'This file isn’t a valid .zip archive.',
    'Lancé avec Lanterne': 'Launched with Lanterne',
    'Chercher une jaquette': 'Find cover art',
    'Nom du jeu': 'Game name',
    'Tape le nom du jeu': 'Type the game’s name',
    'Recherche impossible': 'Search failed',
    'Aucun jeu trouvé': 'No games found',
    'Essaie le nom anglais du jeu, ou un nom plus court.': 'Try the game’s English name, or a shorter name.',
    'Modifier le jeu': 'Edit game',
    'Ajouter': 'Add',
    'Enregistrer': 'Save',
    'Jeu': 'Game',
    'Nom': 'Name',
    'Exécutable': 'Executable',
    'Pas encore installé ?': 'Not installed yet?',
    'Lance son installeur dans le préfixe de ce jeu, puis choisis l’exécutable.': 'Run its installer in this game’s prefix, then choose the executable.',
    'Jaquette': 'Cover art',
    'Retirer la jaquette': 'Remove cover art',
    'Compatibilité': 'Compatibility',
    'Version de Proton': 'Proton version',
    'Préfixe Wine (laisser vide pour le créer automatiquement)': 'Wine prefix (leave empty to create it automatically)',
    'Identifiant umu (facultatif, active les correctifs du jeu)': 'umu ID (optional, enables the game’s fixes)',
    'Confidentialité': 'Privacy',
    'Isoler de tes fichiers personnels': 'Isolate from your personal files',
    'Le jeu ne voit que son dossier et son préfixe. Désactive-le s’il a besoin de mods ou de fichiers rangés ailleurs.': 'The game only sees its own folder and prefix. Turn this off if it needs mods or files stored elsewhere.',
    'Performances et affichage': 'Performance and display',
    'Affiche les FPS, températures et charges en jeu': 'Shows FPS, temperatures and load in game',
    'Optimise le processeur pendant la partie': 'Optimizes the CPU while you play',
    'FSR plein écran': 'Fullscreen FSR',
    'Agrandit l’image quand le jeu tourne sous ta résolution native': 'Upscales the image when the game runs below your native resolution',
    'Wayland natif': 'Native Wayland',
    'Expérimental, demande une GE-Proton récente': 'Experimental, requires a recent GE-Proton',
    'Avancé': 'Advanced',
    'Séparés par des espaces, entre guillemets s’ils en contiennent.': 'Separated by spaces, in quotes if they contain any.',
    'Arguments de lancement': 'Launch arguments',
    'Variables d’environnement (ex. DXVK_HUD=fps)': 'Environment variables (e.g. DXVK_HUD=fps)',
    'Supprimer ce jeu…': 'Delete this game…',
    'Aucun fichier choisi': 'No file chosen',
    'Choisir l’exécutable du jeu': 'Choose the game’s executable',
    'Choisir l’installeur du jeu': 'Choose the game’s installer',
    'Choisir le dossier du préfixe Wine': 'Choose the Wine prefix folder',
    'Choisir une jaquette': 'Choose cover art',
    'Cette image n’a pas pu être lue. Essaie un PNG ou un JPEG.': 'This image couldn’t be read. Try a PNG or JPEG.',
    'Modifications enregistrées': 'Changes saved',
    'Plus d’options': 'More options',
    'Modifier…': 'Edit…',
    'Chercher une jaquette en ligne…': 'Find cover art online…',
    'Supprimer de la bibliothèque…': 'Remove from library…',
    'Isolé': 'Isolated',
    'Temps de jeu': 'Play time',
    'Dernière session': 'Last session',
    'Arrêter': 'Stop',
    'Jouer': 'Play',
    'Modifier': 'Edit',
    'Supprimer de la bibliothèque': 'Remove from library',
    'Outils': 'Tools',
    'Tout s’exécute dans le préfixe Wine de ce jeu.': 'Everything runs in this game’s Wine prefix.',
    'Configuration de Wine': 'Wine configuration',
    'Version de Windows, lecteurs, audio…': 'Windows version, drives, audio…',
    'Winetricks : composants à installer (ex. vcrun2022 d3dx9)': 'Winetricks: components to install (e.g. vcrun2022 d3dx9)',
    'Lancer un autre exécutable': 'Run another executable',
    'Installeur, launcher, outil de mods…': 'Installer, launcher, mod tool…',
    'Sauvegardes': 'Saves',
    'Importer depuis Steam': 'Import from Steam',
    'Récupère les sauvegardes d’un jeu Steam ou d’un raccourci non-Steam. Celles du jeu sont d’abord mises de côté.': 'Gets the saves of a Steam game or a non-Steam shortcut. This game’s saves are set aside first.',
    'Exporter les sauvegardes': 'Export saves',
    'Dans une archive .zip, pour les garder à l’abri ou les emporter sur un autre PC': 'To a .zip archive, to keep them safe or take them to another PC',
    'Importer une archive de sauvegardes': 'Import a saves archive',
    'Une archive exportée par Lanterne. Les sauvegardes actuelles sont d’abord mises de côté.': 'An archive exported by Lanterne. Your current saves are set aside first.',
    'Ouvrir': 'Open',
    'Sauvegardes mises de côté': 'Set-aside saves',
    'Fichiers': 'Files',
    'Dossier du jeu': 'Game folder',
    'Préfixe Wine': 'Wine prefix',
    'Journal du dernier lancement': 'Last launch log',
    'Installer': 'Install',
    'Ta bibliothèque est vide': 'Your library is empty',
    'Ajoute un jeu Windows (.exe). Lanterne s’occupe de Proton.': 'Add a Windows game (.exe). Lanterne takes care of Proton.',
    'Joués récemment': 'Recently played',
    'Supprimer…': 'Delete…',
    'Détails': 'Details',
    'Moteur': 'Engine',
    'umu-launcher fait tourner Proton hors de Steam, dans le même environnement d’exécution.': 'umu-launcher runs Proton outside Steam, in the same runtime environment.',
    'Par défaut': 'Default',
    'Proton pour les nouveaux jeux': 'Proton for new games',
    'Le mode auto utilise toujours la dernière GE-Proton': 'Auto mode always uses the latest GE-Proton',
    'Versions installées': 'Installed versions',
    'GE-Proton à télécharger': 'GE-Proton downloads',
    'Builds communautaires de GloriousEggroll, avec des correctifs de jeux en plus.': 'Community builds by GloriousEggroll, with extra game fixes.',
    'Ajouter un jeu (Ctrl+N)': 'Add a game (Ctrl+N)',
    'Rechercher (Ctrl+F)': 'Search (Ctrl+F)',
    'Rechercher un jeu…': 'Search for a game…',
    'Voir le journal': 'View log',
    'Le jeu sera retiré de Lanterne. Ses fichiers d’installation ne sont pas touchés.': 'The game will be removed from Lanterne. Its installation files won’t be touched.',
    'Retiré du menu des applications': 'Removed from the applications menu',
    'Jaquette mise à jour': 'Cover art updated',
    '{n} jeu isolé.': '{n} game isolated.',
    '{n} jeux isolés.': '{n} games isolated.',
    '\n\nCes jeux sont rangés directement dans un dossier personnel (Téléchargements, Documents…), qu’il faudrait partager en entier. Déplace-les dans leur propre dossier pour pouvoir les isoler :\n': '\n\nThese games are stored directly in a personal folder (Downloads, Documents…), which would have to be shared entirely. Move them into their own folder to isolate them:\n',
    'Isolation des jeux': 'Game isolation',
    'Ce jeu n’a pas encore de sauvegarde à exporter.': 'This game has no saves to export yet.',
    'Ferme le jeu avant d’importer des sauvegardes.': 'Close the game before importing saves.',
    'Importer': 'Import',
    '. Les anciens sont mis de côté': '. The old ones were set aside',
    'Ferme le jeu avant d’importer ses sauvegardes.': 'Close the game before importing its saves.',
    'Indique au moins un composant, par exemple vcrun2022.': 'Enter at least one component, for example vcrun2022.',
    'Winetricks a terminé l’installation': 'Winetricks finished installing',
    'Lancer un exécutable dans ce préfixe': 'Run an executable in this prefix',
    'umu-launcher est nécessaire': 'umu-launcher is required',
    'C’est le moteur qui fait tourner Proton sans Steam. Lanterne peut le télécharger depuis GitHub et l’installer dans ton dossier personnel.': 'It’s the engine that runs Proton without Steam. Lanterne can download it from GitHub and install it in your home folder.',
    'Ce jeu n’existe plus dans la bibliothèque de Lanterne.': 'This game is no longer in Lanterne’s library.',
    'Tes jeux Windows avec Proton, sans Steam.': 'Your Windows games with Proton, without Steam.',
    'Fait sur mesure pour Bazzite': 'Made for Bazzite',
    '  Steam est introuvable sur ce système : rien à importer.': '  Steam can’t be found on this system: nothing to import.',
    'Aucune archive trouvée pour {0}.': 'No archive found for {0}.',
    'Le fichier téléchargé est corrompu (somme de contrôle invalide).': 'The downloaded file is corrupted (invalid checksum).',
    'le dossier {0} tout entier': 'the entire {0} folder',
    'Fichier introuvable : {0}': 'File not found: {0}',
    '\n      anciennes sauvegardes mises de côté dans {0}': '\n      old saves set aside in {0}',
    '  {0}  ({1} préfixes dans compatdata)': '  {0}  ({1} prefixes in compatdata)',
    '    exécutable : {0}': '    executable : {0}',
    '(vide)': '(empty)',
    '    préfixe    : {0}': '    prefix     : {0}',
    'aucun (jamais lancé avec Proton ?)': 'none (never launched with Proton?)',
    'pas trouvé dans Steam': 'not found in Steam',
    'Cette archive contient des fichiers hors des dossiers de sauvegarde (Documents, Saved Games, AppData) : import refusé.': 'This archive contains files outside the save folders (Documents, Saved Games, AppData): import refused.',
    'Aucune image disponible pour ce jeu sur Steam.': 'No image available for this game on Steam.',
    'Chercher en ligne': 'Search online',
    'Choisir une image sur le disque': 'Choose an image from disk',
    'Importer les sauvegardes de Steam': 'Import saves from Steam',
    'Choisis l’exécutable : Lanterne cherchera le jeu dans Steam': 'Choose the executable: Lanterne will look for the game in Steam',
    'Choisir un dossier': 'Choose a folder',
    'Aucune jaquette trouvée : cherche-la ou choisis une image': 'No cover art found: search for it or choose an image',
    'Recherche impossible hors connexion': 'Search unavailable offline',
    'Retirer du menu des applications': 'Remove from the applications menu',
    'Ajouter au menu des applications': 'Add to the applications menu',
    'Ajouté le': 'Added on',
    'Vérifier l’isolation': 'Check isolation',
    'Importer…': 'Import…',
    'Exporter…': 'Export…',
    'Copies faites avant chaque import': 'Copies made before each import',
    'Aucune pour l’instant': 'None yet',
    'Utile si le jeu ne démarre pas': 'Useful if the game won’t start',
    'Pas encore de journal': 'No log yet',
    'Essaie un autre nom.': 'Try another name.',
    '{n} jeu': '{n} game',
    '{n} jeux': '{n} games',
    'Actualiser': 'Refresh',
    'Mettre à jour': 'Update',
    'Non installé, nécessaire pour lancer tes jeux': 'Not installed, required to launch your games',
    'umu-launcher est installé': 'umu-launcher is installed',
    'Recherche des dernières versions…': 'Looking for the latest versions…',
    'Liste indisponible': 'List unavailable',
    'Réessayer': 'Try again',
    'Installée': 'Installed',
    'Installation impossible : {0}': 'Installation failed: {0}',
    'Menu principal': 'Main menu',
    'Supprimer aussi son préfixe Wine : sauvegardes, réglages et le jeu lui-même s’il y est installé': 'Also delete its Wine prefix: saves, settings and the game itself if it’s installed there',
    'L’isolation fonctionne': 'Isolation works',
    'Importer ces sauvegardes ?': 'Import these saves?',
    'Import depuis Steam': 'Import from Steam',
    'Le préfixe Steam de ce jeu ne contient aucune sauvegarde.': 'This game’s Steam prefix contains no saves.',
    '{n} fichier de sauvegarde importé depuis Steam ({size})': '{n} save file imported from Steam ({size})',
    '{n} fichiers de sauvegarde importés depuis Steam ({size})': '{n} save files imported from Steam ({size})',
    'Ce jeu est introuvable dans Steam.': 'This game can’t be found in Steam.',
    'Importer les sauvegardes de Steam ?': 'Import saves from Steam?',
    'Winetricks installe {0}… Ça peut prendre un moment.': 'Winetricks is installing {0}… This may take a while.',
    'Impossible d’ouvrir {0} : {1}': 'Can’t open {0}: {1}',
    'Format d’archive inconnu : {0}': 'Unknown archive format: {0}',
    'autre dossier': 'other folder',
    'introuvable': 'not found',
    'Isolation impossible : il faudrait partager {0}. Range le jeu ou son installeur dans son propre dossier (par exemple ~/Games/NomDuJeu), ou désactive l’isolation dans ses réglages.': 'Isolation impossible: {0} would have to be shared. Move the game or its installer into its own folder (for example ~/Games/GameName), or turn off isolation in its settings.',
    'Impossible de créer le préfixe : {0}': 'Can’t create the prefix: {0}',
    'Proton « {0} » est introuvable. Choisis une autre version dans les réglages du jeu.': 'Proton “{0}” can’t be found. Choose another version in the game’s settings.',
    '  · {0} : sauvegardes déjà importées': '  · {0}: saves already imported',
    '  · {0} : introuvable dans Steam (ni jeu Steam, ni raccourci non-Steam)': '  · {0}: not found in Steam (neither a Steam game nor a non-Steam shortcut)',
    '  · {0} : trouvé dans Steam ({1}), mais son préfixe ne contient aucune sauvegarde. Elles sont sans doute dans le dossier du jeu, déjà partagé.': '  · {0}: found in Steam ({1}), but its prefix contains no saves. They’re probably in the game folder, which is already shared.',
    '  · {0} : sauvegardes Lanterne plus récentes que celles de Steam, conservées': '  · {0}: Lanterne saves are newer than Steam’s, kept',
    '  · {0} : exécutable introuvable ({1}), ignoré': '  · {0}: executable not found ({1}), skipped',
    '  … {0} : copie de son préfixe Steam (jeu et sauvegardes)…': '  … {0}: copying its Steam prefix (game and saves)…',
    '(sans nom)': '(no name)',
    '    sauvegardes: {0}': '    saves      : {0}',
    'oui': 'yes',
    'aucune dans le préfixe': 'none in the prefix',
    '« {0} » s’est fermé au bout de {1} s. Ouvre Lanterne pour consulter son journal.': '“{0}” closed after {1} s. Open Lanterne to check its log.',
    'Non installé sur ce système': 'Not installed on this system',
    'Trouvée sur Steam : {0}': 'Found on Steam: {0}',
    'Aucune sauvegarde Steam trouvée pour ce jeu': 'No Steam saves found for this game',
    'Images': 'Images',
    'Impossible d’isoler ce jeu : il faudrait partager {0}. Range-le dans son propre dossier, ou désactive l’isolation.': 'This game can’t be isolated: {0} would have to be shared. Move it into its own folder, or turn off isolation.',
    '« {0} » ajouté à la bibliothèque': '“{0}” added to the library',
    'Tester': 'Test',
    '{0}, la nouvelle version de GE-Proton, est disponible': '{0}, the new GE-Proton version, is available',
    'Reprendre': 'Continue playing',
    'Fourni par le système : {0}': 'Provided by the system: {0}',
    'Installé par Lanterne': 'Installed by Lanterne',
    'Aucune version installée': 'No versions installed',
    'Installe une GE-Proton ci-dessous, ou laisse le mode auto la télécharger au premier lancement.': 'Install a GE-Proton below, or let auto mode download it on first launch.',
    'Utilisée par {n} jeu, dans {dir}': 'Used by {n} game, in {dir}',
    'Utilisée par {n} jeux, dans {dir}': 'Used by {n} games, in {dir}',
    'Le jeu qui l’utilise passera en mode auto.': 'The game using it will switch to auto mode.',
    'Les {n} jeux qui l’utilisent passeront en mode auto.': 'The {n} games using it will switch to auto mode.',
    'Installation d’umu impossible : {0}': 'Couldn’t install umu: {0}',
    'Dernière': 'Latest',
    '{0} est installée': '{0} is installed',
    'Fermeture de « {0} »…': 'Closing “{0}”…',
    'Session terminée : {0} sur « {1} »': 'Session over: {0} of “{1}”',
    'Supprimer « {0} » ?': 'Delete “{0}”?',
    '« {0} » supprimé': '“{0}” deleted',
    '« {0} » est maintenant dans le menu des applications': '“{0}” is now in the applications menu',
    '{name} est utilisée par {n} jeu': '{name} is now used by {n} game',
    '{name} est utilisée par {n} jeux': '{name} is now used by {n} games',
    '{name} est prête. Les jeux en mode auto l’utiliseront d’office.': '{name} is ready. Games in auto mode will use it automatically.',
    'L’isolation ne fonctionne pas': 'Isolation doesn’t work',
    'Le jeu voit encore ton dossier personnel. Ta version d’umu ignore probablement les réglages d’isolation du conteneur. Envoie le journal « isolation » du dossier des journaux pour qu’on corrige.': 'The game can still see your home folder. Your umu version probably ignores the container’s isolation settings. Send the “isolation” log from the logs folder so it can be fixed.',
    'Test non concluant': 'Inconclusive test',
    'Le test n’a pas pu s’exécuter. Le dossier des journaux contient le détail (fichier se terminant par « -isolation.log »).': 'The test couldn’t run. The logs folder has the details (file ending in “-isolation.log”).',
    'Archives de sauvegardes': 'Saves archives',
    '{n} fichier de sauvegarde, exporté depuis « {game} » le {date}.': '{n} save file, exported from “{game}” on {date}.',
    '{n} fichiers de sauvegarde, exportés depuis « {game} » le {date}.': '{n} save files, exported from “{game}” on {date}.',
    '\n\nAttention : cette archive semble venir d’un autre jeu que « {0} ».': '\n\nWarning: this archive seems to come from a different game than “{0}”.',
    'Aucune sauvegarde Steam trouvée pour ce jeu.': 'No Steam saves found for this game.',
    'L’outil s’est terminé avec une erreur. Consulte le dossier des journaux.': 'The tool ended with an error. Check the logs folder.',
    '{n} jeu ajouté, sauvegardes importées pour {saved}.': '{n} game added, saves imported for {saved}.',
    '{n} jeux ajoutés, sauvegardes importées pour {saved}.': '{n} games added, saves imported for {saved}.',
    '{0} [{1}], sauvegardes : {2}': '{0} [{1}], saves: {2}',
    'non': 'no',
    'Sauvegarde impossible : {0}': 'Couldn’t save: {0}',
    'Trouvées : {0} ({1})': 'Found: {0} ({1})',
    'Le jeu ne voit que {0} et son préfixe. Ce test le confirme sur ta machine.': 'The game only sees {0} and its prefix. This test confirms it on your machine.',
    '{0} de jeu, dernière session : {1}': '{0} played, last session: {1}',
    '{0} supprimée': '{0} deleted',
    'umu-launcher {0} est installé': 'umu-launcher {0} is installed',
    'Publiée le {0}, {1:.0f} Mo': 'Released {0}, {1:.0f} MB',
    'Lancement de « {0} »…': 'Launching “{0}”…',
    '« {0} » s’est fermé au bout de {1} s': '“{0}” closed after {1} s',
    'Test impossible : {0}': 'Test failed: {0}',
    '« {0} » ne voit pas ton dossier personnel. Il n’a accès qu’à {1} et à son préfixe Wine.': '“{0}” can’t see your home folder. It only has access to {1} and its Wine prefix.',
    '{n} fichier de sauvegarde, exporté depuis « {game} ».': '{n} save file, exported from “{game}”.',
    '{n} fichiers de sauvegarde, exportés depuis « {game} ».': '{n} save files, exported from “{game}”.',
    '{n} fichier de sauvegarde.': '{n} save file.',
    '{n} fichiers de sauvegarde.': '{n} save files.',
    '{n} fichier de sauvegarde importé': '{n} save file imported',
    '{n} fichiers de sauvegarde importés': '{n} save files imported',
    'Import impossible : {0}': 'Import failed: {0}',
    'Import des sauvegardes impossible : {0}': 'Couldn’t import saves: {0}',
    'Trouvé dans Steam ({0}), mais son préfixe ne contient aucune sauvegarde : elles sont sans doute dans le dossier du jeu.': 'Found in Steam ({0}), but its prefix contains no saves: they’re probably in the game folder.',
    'Source : {0} ({1}), dans {2}.\n\nLes sauvegardes actuelles de ce jeu dans Lanterne seront mises de côté, puis remplacées par celles de Steam.': 'Source: {0} ({1}), in {2}.\n\nThis game’s current saves in Lanterne will be set aside, then replaced with Steam’s.',
    'Recherche dans Steam impossible : {0}': 'Couldn’t search Steam: {0}',
    'Trouvé dans Steam ({0}), mais sans sauvegarde dans son préfixe': 'Found in Steam ({0}), but no saves in its prefix',
    'Jaquette non copiée : {0}': 'Cover art not copied: {0}',
    'Version {0} installée par Lanterne': 'Version {0} installed by Lanterne',
    'Suppression impossible : {0}': 'Couldn’t delete: {0}',
    'Raccourci impossible : {0}': 'Couldn’t create shortcut: {0}',
    '{n} fichier exporté dans {dest}': '{n} file exported to {dest}',
    '{n} fichiers exportés dans {dest}': '{n} files exported to {dest}',
    'Export impossible : {0}': 'Export failed: {0}',
    'Sauvegardes :': 'Saves:',
    '  + {0} : {1}': '  + {0}: {1}',
    'Chercher en ligne…': 'Search online…',
    'Trouver une jaquette': 'Find cover art',
    'Chercher une jaquette en ligne': 'Find cover art online',
    'Chercher les jaquettes manquantes': 'Find missing cover art',
    'Tous tes jeux ont déjà une jaquette.': 'All your games already have cover art.',
    'Recherche d’une jaquette pour {n} jeu…': 'Looking for cover art for {n} game…',
    'Recherche de jaquettes pour {n} jeux…': 'Looking for cover art for {n} games…',
    'Recherche des jaquettes impossible hors connexion.': 'Can’t search for cover art offline.',
    '{n} jaquette trouvée': '{n} cover found',
    '{n} jaquettes trouvées': '{n} covers found',
    '. Pour les autres, cherche-les depuis la page du jeu.': '. For the others, search from the game’s page.',
}

CARD_W, CARD_H = 188, 282

GAME_DEFAULTS = {
    "id": "", "name": "", "exe": "", "args": "", "env": "",
    "proton": "auto", "prefix": "", "gameid": "", "cover": "",
    "mangohud": False, "gamemode": False, "fsr": False, "wayland": False,
    "playtime": 0, "last_played": 0, "added": 0,
    "steam_appid": "", "cover_auto": False, "steam_import_done": False, "sandbox": False,
}
SETTINGS_DEFAULTS = {"default_proton": "auto", "sort": 0, "window": [1240, 820], "language": "auto"}

MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
             "juil.", "août", "sept.", "oct.", "nov.", "déc."]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ──────────────────────────────────────────────────────────────── outils ──


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def short_path(p):
    s, h = str(p), str(HOME)
    if s == h or s.startswith(h + os.sep):
        return "~" + s[len(h):]
    return s


def is_inside(path, base):
    try:
        p = Path(path).expanduser().resolve()
        b = Path(base).resolve()
    except OSError:
        return False
    return p != b and b in p.parents


def fmt_duration(sec):
    sec = int(sec or 0)
    if sec < 60:
        return _("Moins d’une minute") if sec else _("Jamais lancé")
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    if LANG == "fr":
        return f"{h} h {m:02d}" if m else f"{h} h"
    return f"{h}h {m:02d}m" if m else f"{h}h"


def fmt_date(ts):
    d = datetime.fromtimestamp(ts)
    if LANG == "fr":
        return f"{d.day} {MONTHS_FR[d.month - 1]} {d.year}"
    return f"{MONTHS_EN[d.month - 1]} {d.day}, {d.year}"


def fmt_last(ts):
    if not ts:
        return _("Jamais")
    days = (datetime.now().date() - datetime.fromtimestamp(ts).date()).days
    if days <= 0:
        return _("Aujourd’hui")
    if days == 1:
        return _("Hier")
    if days < 7:
        return _("Il y a {n} jours").format(n=days)
    return fmt_date(ts)


def fmt_iso(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return fmt_date(d.timestamp())


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s or "jeu"


GENERIC_STEMS = {"game", "launcher", "start", "play", "run", "main", "client", "bin",
                 "win64", "win32", "x64", "x86", "binaries", "setup", "install", "app"}


def _tidy(s):
    s = re.sub(r"[_.]+", " ", s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -")


def _squash(s):
    return re.sub(r"[^0-9a-z]", "", s.lower())


def _pretty(s):
    return s.title() if s.islower() else s


def guess_name(path, installer=False):
    """Devine un nom de jeu lisible à partir du chemin d'un .exe."""
    p = Path(path)
    if installer:
        s = re.sub(r"\(.*?\)|\[.*?\]", " ", p.stem)
        s = re.sub(r"(?<![A-Za-z0-9])v?\d+(?:\.\d+)+\w*", " ", s)
        s = re.sub(r"(?i)setup|installer|install", " ", s)
        s = _tidy(s)
        s = re.sub(r"(?i)\b(gog|x64|x86|64 ?bit|32 ?bit)\b", " ", s)
        s = re.sub(r"\s+", " ", s).strip(" -")
        return _pretty(s) if s else _("Nouveau jeu")
    parts = p.parts
    if "Binaries" in parts:  # jeux Unreal : Racine/Nom/Binaries/Win64/Nom-Win64-Shipping.exe
        i = len(parts) - 1 - parts[::-1].index("Binaries")
        if i >= 2:
            return parts[i - 2]
    folder = p.parent
    while folder.name and _squash(folder.name) in GENERIC_STEMS and folder.parent != folder:
        folder = folder.parent
    stem = re.sub(r"(?i)-win64-shipping$", "", p.stem)
    if _squash(stem) in GENERIC_STEMS or len(stem) <= 2:
        return folder.name or stem
    if folder.name and _squash(folder.name) == _squash(stem):
        return folder.name  # le nom du dossier est souvent mieux écrit
    return _pretty(_tidy(stem)) or stem


def initials(name):
    words = re.findall(r"[^\W_]+", name)
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].capitalize()
    return (words[0][0] + words[1][0]).upper()


def palette_index(name):
    return zlib.crc32(name.encode("utf-8")) % len(PALETTES)


def open_path(p):
    try:
        Gio.AppInfo.launch_default_for_uri(Path(p).expanduser().resolve().as_uri(), None)
    except GLib.Error as e:
        print(_('Impossible d’ouvrir {0} : {1}').format(p, e.message), file=sys.stderr)


def run_async(func, on_done=None, on_error=None):
    """Exécute func dans un thread et rappelle on_done/on_error dans la boucle GTK."""
    def worker():
        try:
            res = func()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            if on_error:
                GLib.idle_add(_call_once, on_error, e)
        else:
            if on_done:
                GLib.idle_add(_call_once, on_done, res)
    threading.Thread(target=worker, daemon=True).start()


def _call_once(fn, arg):
    fn(arg)
    return False


# ─────────────────────────────────────────────────────────────── réseau ──

def http_get(url, api=False, timeout=30):
    headers = {"User-Agent": f"Lanterne/{VERSION}"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def http_json(url):
    with http_get(url, api=True) as r:
        return json.load(r)


def http_text(url):
    with http_get(url) as r:
        return r.read().decode("utf-8", "replace")


def download(url, dest, progress=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with http_get(url, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(part, dest)


def nice_error(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (403, 429):
            return _("GitHub limite les requêtes pour le moment. Réessaie dans quelques minutes.")
        return _('GitHub a répondu avec l’erreur {0}.').format(e.code)
    if isinstance(e, urllib.error.URLError):
        return _("GitHub est injoignable. Vérifie ta connexion à Internet.")
    return str(e) or e.__class__.__name__


def safe_extract(archive, dest):
    if hasattr(tarfile, "tar_filter"):
        archive.extractall(dest, filter="tar")
    else:
        archive.extractall(dest)


def unpack_any(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            safe_extract(t, dest)
    elif zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        raise RuntimeError(_('Format d’archive inconnu : {0}').format(archive.name))


# ─────────────────────────────────────────────────────── Proton et umu ──

def local_umu():
    p = UMU_DIR / "umu-run"
    return p if p.is_file() else None


def find_umu():
    system = shutil.which("umu-run")
    if system:
        return system
    loc = local_umu()
    return str(loc) if loc else None


def scan_protons():
    """Liste (nom, chemin) des versions de Proton installées, GE-Proton en tête."""
    found = {}
    for base in [PROTON_DIR, *EXTRA_PROTON_DIRS]:
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for d in entries:
            if d.name.startswith(".") or d.name in found:
                continue
            if (d / "proton").is_file():
                found[d.name] = d
    items = sorted(found.items(), key=lambda kv: natural_key(kv[0]), reverse=True)
    return sorted(items, key=lambda kv: 0 if kv[0].startswith("GE-Proton") else 1)


def proton_label(value):
    if not value or value == "auto":
        return _("GE-Proton auto")
    return Path(value).name


def proton_options(current=None):
    values = ["auto"]
    labels = [_("Toujours la dernière GE-Proton (auto)")]
    for name, path in scan_protons():
        values.append(str(path))
        labels.append(name)
    if current and current not in values:
        values.append(current)
        exists = (Path(current) / "proton").is_file()
        labels.append(f"{Path(current).name} ({_('autre dossier') if exists else _('introuvable')})")
    return values, labels


def install_ge(rel, progress):
    """Télécharge, vérifie et extrait une version de GE-Proton. Retourne son dossier."""
    tag = rel["tag_name"]
    assets = rel.get("assets", [])
    tar_asset = next((a for a in assets if a["name"].endswith(".tar.gz")), None)
    sha_asset = next((a for a in assets if a["name"].endswith(".sha512sum")), None)
    if not tar_asset:
        raise RuntimeError(_('Aucune archive trouvée pour {0}.').format(tag))
    dest = DL_DIR / tar_asset["name"]
    mb = 1024 * 1024

    def on_dl(done, total):
        if total:
            progress(done / total * 0.92, f"{done // mb} / {total // mb} Mo")
        else:
            progress(None, f"{done // mb} Mo")

    download(tar_asset["browser_download_url"], dest, on_dl)
    try:
        if sha_asset:
            progress(None, _("Vérification…"))
            expected = http_text(sha_asset["browser_download_url"]).split()[0].lower()
            h = hashlib.sha512()
            with open(dest, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != expected:
                raise RuntimeError(_("Le fichier téléchargé est corrompu (somme de contrôle invalide)."))
        progress(None, _("Extraction…"))
        PROTON_DIR.mkdir(parents=True, exist_ok=True)
        work = PROTON_DIR / f".partiel-{tag}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        with tarfile.open(dest, "r:gz") as t:
            safe_extract(t, work)
        entries = list(work.iterdir())
        top = entries[0] if len(entries) == 1 and entries[0].is_dir() else work
        target = PROTON_DIR / tag
        shutil.rmtree(target, ignore_errors=True)
        top.rename(target)
        shutil.rmtree(work, ignore_errors=True)
        return target
    finally:
        dest.unlink(missing_ok=True)


def install_umu_files():
    """Installe la version « zipapp » d'umu-launcher dans ~/.local/share/lanterne/umu."""
    rel = http_json(UMU_API)
    asset = next((a for a in rel.get("assets", []) if "zipapp" in a["name"].lower()), None)
    if not asset:
        raise RuntimeError(_("La dernière version d’umu-launcher ne propose pas d’archive « zipapp »."))
    dest = DL_DIR / asset["name"]
    download(asset["browser_download_url"], dest)
    work = DATA_DIR / ".umu-partiel"
    shutil.rmtree(work, ignore_errors=True)

    def find():
        return next((p for p in work.rglob("umu-run") if p.is_file()), None)

    try:
        unpack_any(dest, work)
        found = find()
        if not found:  # archive dans l'archive
            for inner in [p for p in work.rglob("*") if p.is_file()]:
                if tarfile.is_tarfile(inner) or zipfile.is_zipfile(inner):
                    unpack_any(inner, work / (inner.name + ".d"))
            found = find()
        if not found:
            raise RuntimeError(_("umu-run est absent de l’archive téléchargée."))
        UMU_DIR.mkdir(parents=True, exist_ok=True)
        target = UMU_DIR / "umu-run"
        shutil.copy2(found, target)
        target.chmod(0o755)
        (UMU_DIR / "VERSION").write_text(rel.get("tag_name", ""), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        dest.unlink(missing_ok=True)
    return rel.get("tag_name", "")


class LaunchError(Exception):
    pass


class UmuMissing(LaunchError):
    pass


# ───────────────────────────────────────────────────────────── isolation ──
# Un jeu isolé tourne dans le conteneur d'umu (pressure-vessel) avec un dossier
# personnel privé : il ne voit que son propre dossier et son préfixe Wine.

def personal_dirs():
    dirs = []
    for key in ("DIRECTORY_DOCUMENTS", "DIRECTORY_DOWNLOAD", "DIRECTORY_DESKTOP", "DIRECTORY_PICTURES",
                "DIRECTORY_VIDEOS", "DIRECTORY_MUSIC", "DIRECTORY_PUBLIC_SHARE", "DIRECTORY_TEMPLATES"):
        try:
            d = GLib.get_user_special_dir(getattr(GLib.UserDirectory, key))
        except (AttributeError, TypeError):
            d = None
        if d:
            dirs.append(Path(d))
    return dirs


def game_root(exe):
    """Dossier racine du jeu à partager (pas seulement le sous-dossier de l'exécutable)."""
    p = Path(exe).resolve().parent
    parts = p.parts
    if "Binaries" in parts:  # Unreal : Racine/Nom/Binaries/Win64
        i = len(parts) - 1 - parts[::-1].index("Binaries")
        if i >= 2:
            p = Path(*parts[:i - 1])
    while _squash(p.name) in GENERIC_STEMS and p.parent != p:
        p = p.parent
    return p


def unsafe_share(path):
    """Retourne une raison si partager ce dossier exposerait des fichiers personnels."""
    try:
        rp = Path(path).resolve()
        home = HOME.resolve()
    except OSError:
        return None
    if rp == home or rp in home.parents:
        return _("ton dossier personnel tout entier")
    for d in personal_dirs():
        try:
            dr = d.resolve()
        except OSError:
            continue
        if rp == dr or rp in dr.parents:
            return _('le dossier {0} tout entier').format(short_path(dr))
    if ":" in str(rp):
        return _("un chemin contenant « : », non pris en charge")
    return None


def sandbox_ok(exe):
    return bool(exe) and unsafe_share(game_root(exe)) is None


def apply_sandbox(env, game, prefix, program=None):
    shared_rw = [prefix]
    shared_ro = []
    if game.get("exe"):
        shared_rw.append(game_root(game["exe"]))
    if program and os.sep in program and not any(is_inside(program, d) or Path(program).resolve() == d
                                                  for d in map(Path, shared_rw)):
        shared_ro.append(Path(program).resolve().parent)  # installeur, outil : lecture seule
    for d in shared_rw + shared_ro:
        reason = unsafe_share(d)
        if reason:
            raise LaunchError(_('Isolation impossible : il faudrait partager {0}. Range le jeu ou son installeur dans son propre dossier (par exemple ~/Games/NomDuJeu), ou désactive l’isolation dans ses réglages.').format(reason))
    # Ce dont umu et Proton ont besoin dans le conteneur, toujours en lecture seule : certains de
    # ces fichiers s'exécutent ensuite hors du conteneur, un jeu ne doit donc pas pouvoir les modifier.
    runtime = [UMU_RUNTIME_DIR, PROTON_DIR]
    proton = env.get("PROTONPATH", "")
    if proton == "GE-Proton":
        runtime.append(STEAM_COMPAT)  # là où umu télécharge la dernière GE-Proton
    elif proton:
        runtime.append(Path(proton))
    for d in runtime:
        if d.is_dir() and not any(d == x or is_inside(d, x) for x in map(Path, shared_rw)):
            shared_ro.append(d.resolve())
    home = Path(prefix) / ".dossier-perso-isole"
    home.mkdir(parents=True, exist_ok=True)
    env["PRESSURE_VESSEL_SHARE_HOME"] = "0"
    env["PRESSURE_VESSEL_HOME"] = str(home)
    env["PRESSURE_VESSEL_FILESYSTEMS_RW"] = ":".join(dict.fromkeys(str(d) for d in shared_rw))
    if shared_ro:
        env["PRESSURE_VESSEL_FILESYSTEMS_RO"] = ":".join(dict.fromkeys(str(d) for d in shared_ro))


def build_command(game, settings, program=None, extra_args=None):
    """Construit la commande umu-run, l'environnement et le dossier de travail."""
    umu = find_umu()
    if not umu:
        raise UmuMissing(_("umu-launcher est introuvable."))
    target = program or game.get("exe", "")
    if not target:
        raise LaunchError(_("Aucun exécutable n’est défini pour ce jeu."))
    is_path = os.sep in target
    if is_path and not Path(target).is_file():
        raise LaunchError(_('Fichier introuvable : {0}').format(short_path(target)))
    if not game.get("prefix"):
        raise LaunchError(_("Aucun préfixe Wine n’est défini pour ce jeu."))
    prefix = Path(game["prefix"]).expanduser()
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise LaunchError(_('Impossible de créer le préfixe : {0}').format(e.strerror)) from e

    env = os.environ.copy()
    proton = game.get("proton") or settings.get("default_proton") or "auto"
    if proton == "auto":
        env["PROTONPATH"] = "GE-Proton"  # umu télécharge et utilise la dernière GE-Proton
    else:
        if not (Path(proton) / "proton").is_file():
            raise LaunchError(_('Proton « {0} » est introuvable. Choisis une autre version dans les réglages du jeu.').format(Path(proton).name))
        env["PROTONPATH"] = proton
    env["WINEPREFIX"] = str(prefix)
    env["GAMEID"] = (game.get("gameid") or "").strip() or "umu-default"
    if game.get("mangohud"):
        env["MANGOHUD"] = "1"
    if game.get("fsr"):
        env["WINE_FULLSCREEN_FSR"] = "1"
    if game.get("wayland"):
        env["PROTON_ENABLE_WAYLAND"] = "1"
    try:
        for tok in shlex.split(game.get("env") or ""):
            key, sep, val = tok.partition("=")
            if sep and key:
                env[key] = val
        args = shlex.split(game.get("args") or "") if program is None else []
    except ValueError as e:
        raise LaunchError(_("Des guillemets sont mal fermés dans les arguments ou les variables.")) from e

    if game.get("sandbox"):
        apply_sandbox(env, game, prefix, program)

    cmd = []
    if game.get("gamemode") and shutil.which("gamemoderun"):
        cmd.append("gamemoderun")
    cmd += [umu, target, *args, *(extra_args or [])]
    cwd = str(Path(target).parent) if is_path else str(prefix)
    return cmd, env, cwd


def first_run_hint(game, settings):
    proton = game.get("proton") or settings.get("default_proton") or "auto"
    missing_runtime = not UMU_RUNTIME_DIR.exists()
    missing_ge = proton == "auto" and not any(
        d.name.startswith("GE-Proton") for d in (STEAM_COMPAT.iterdir() if STEAM_COMPAT.is_dir() else []))
    if missing_runtime or missing_ge:
        return _("Premier lancement : umu télécharge ses composants, compte quelques minutes.")
    return None


def fmt_size(n):
    if LANG == "fr":
        if n < 1024 * 1024:
            return f"{max(1, round(n / 1024))} Ko"
        return f"{n / 1024 / 1024:.1f} Mo".replace(".", ",")
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def similarity(a, b):
    a, b = _squash(a), _squash(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ─────────────────────────────────────────────── jaquettes (Steam) ──

def steam_search(term):
    with http_get(STORE_SEARCH + urllib.parse.quote(term), timeout=15) as r:
        data = json.load(r)
    return [i for i in data.get("items", []) if i.get("id") and i.get("type", "app") == "app"]


def best_match(results, name):
    best, score = None, 0.0
    for item in results:
        sc = similarity(item.get("name", ""), name)
        if sc > score:
            best, score = item, sc
    return best, score


def cover_urls(item):
    aid = item["id"]
    urls = []
    m = re.match(r"(https://[^?]+/)[^/?]+(?:\?.*)?$", item.get("tiny_image") or "")
    if m:
        urls.append(m.group(1) + "library_600x900_2x.jpg")
    hosts = ("https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps",
             "https://cdn.cloudflare.steamstatic.com/steam/apps")
    for name in ("library_600x900_2x.jpg", "library_600x900.jpg"):
        urls += [f"{h}/{aid}/{name}" for h in hosts]
    urls += [f"{h}/{aid}/header.jpg" for h in hosts]  # dernier recours : image horizontale
    return list(dict.fromkeys(urls))


def fetch_cover(item, dest):
    """Télécharge la jaquette verticale d'un jeu Steam. Retourne le chemin ou None."""
    for url in cover_urls(item):
        try:
            with http_get(url, timeout=15) as r:
                ctype = r.headers.get("Content-Type", "")
                data = r.read()
        except (urllib.error.URLError, OSError):
            continue
        if ctype.startswith("image/") and len(data) > 2048:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            return dest
    return None


def auto_cover_lookup(name, exe):
    """Cherche une jaquette à partir du nom du jeu et de son exécutable.
    Retourne (chemin, résultat Steam) ou None ; lève l'erreur réseau si tout a échoué."""
    queries = []
    for q in (name, guess_name(exe) if exe else "", _tidy(Path(exe).stem) if exe else ""):
        q = (q or "").strip()
        if q and q.lower() not in (x.lower() for x in queries):
            queries.append(q)
    errors = 0
    for q in queries:
        try:
            item, score = best_match(steam_search(q), q)
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors += 1
            last = e
            continue
        if item and score >= 0.6:
            path = fetch_cover(item, PREVIEWS_DIR / f"steam-{item['id']}.jpg")
            if path:
                return path, item
    if queries and errors == len(queries):
        raise last
    return None


# ───────────────────────────────────────── sauvegardes depuis Steam ──

SAVE_DIRS = ("Documents", "Saved Games", "AppData")
# Dossiers d'AppData qui ne contiennent jamais de sauvegardes.
APPDATA_SKIP = {"temp", "microsoft", "d3dscache", "nvidia", "nvidia corporation", "crashdumps",
                "cef", "steam", "wine"}
SOURCE_KINDS = {"steam": _("jeu Steam"), "shortcut": _("jeu non-Steam ajouté à Steam"), "prefix": _("préfixe Steam")}


def steam_roots():
    roots, seen = [], set()
    for r in STEAM_ROOTS:
        try:
            rr = r.resolve()
        except OSError:
            continue
        if rr not in seen and (rr / "steamapps").is_dir():
            seen.add(rr)
            roots.append(rr)
    return roots


def steam_libraries():
    libs, seen = [], set()
    for root in steam_roots():
        cands = [root / "steamapps"]
        try:
            text = (root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text):
            cands.append(Path(m.group(1).replace("\\\\", "\\")) / "steamapps")
        for c in cands:
            try:
                rc = c.resolve()
            except OSError:
                continue
            if rc not in seen and rc.is_dir():
                seen.add(rc)
                libs.append(rc)
    return libs


def app_manifests():
    out = []
    for lib in steam_libraries():
        for f in lib.glob("appmanifest_*.acf"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            kv = {}
            for k, v in re.findall(r'^\s*"(appid|name|installdir)"\s+"([^"]*)"', text, re.M):
                kv.setdefault(k, v)
            if kv.get("appid"):
                out.append({**kv, "library": lib})
    return out


def parse_binary_vdf(data):
    """Lit le format VDF binaire de Steam (shortcuts.vdf)."""
    pos = 0

    def read_str():
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return s

    def read_map():
        nonlocal pos
        d = {}
        while pos < len(data):
            t = data[pos]
            pos += 1
            if t == 0x08:
                return d
            key = read_str()
            if t == 0x00:
                d[key] = read_map()
            elif t == 0x01:
                d[key] = read_str()
            elif t == 0x02:
                d[key] = int.from_bytes(data[pos:pos + 4], "little", signed=True)
                pos += 4
            elif t == 0x07:
                d[key] = int.from_bytes(data[pos:pos + 8], "little")
                pos += 8
            else:
                raise ValueError(f"type VDF inconnu {t}")
        return d
    return read_map()


def steam_shortcuts():
    out = []
    for root in steam_roots():
        for f in root.glob("userdata/*/config/shortcuts.vdf"):
            try:
                tree = parse_binary_vdf(f.read_bytes())
            except (OSError, ValueError, IndexError):
                continue
            entries = next((v for k, v in tree.items() if k.lower() == "shortcuts" and isinstance(v, dict)), {})
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                e = {k.lower(): v for k, v in entry.items()}
                exe, name = str(e.get("exe", "")), str(e.get("appname", ""))
                aid = e.get("appid")
                if isinstance(aid, int) and aid:
                    aid &= 0xFFFFFFFF
                else:  # anciens raccourcis : identifiant calculé par Steam
                    aid = (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000
                out.append({"appid": str(aid), "name": name, "exe": exe.strip('"'),
                            "startdir": str(e.get("startdir", "")).strip('"'),
                            "launch": str(e.get("launchoptions", "")),
                            "grid_dir": str(f.parent / "grid")})
    return out


def _walk_saves(base):
    """Parcourt les dossiers de sauvegarde d'un utilisateur Wine, sans suivre les liens."""
    for top in SAVE_DIRS:
        start = base / top
        if not start.is_dir() or start.is_symlink():
            continue
        for root, dirs, files in os.walk(start, followlinks=False):
            r = Path(root)
            rel = r.relative_to(base)
            dirs[:] = [d for d in dirs if not (r / d).is_symlink()
                       and not (len(rel.parts) == 2 and rel.parts[0] == "AppData" and d.lower() in APPDATA_SKIP)]
            yield r, rel, [f for f in files if (r / f).is_file() and not (r / f).is_symlink()]


def has_user_data(base):
    return any(files for _r, _rel, files in _walk_saves(Path(base)))


def copy_saves(src, dst):
    count = size = 0
    for r, rel, files in _walk_saves(Path(src)):
        out = Path(dst) / rel
        out.mkdir(parents=True, exist_ok=True)
        for f in files:
            target = out / f
            if target.is_symlink() or target.is_dir():
                continue
            shutil.copy2(r / f, target)
            count += 1
            size += (r / f).stat().st_size
    return count, size


def find_steam_source(game):
    """Retrouve le préfixe Proton de Steam qui correspond à ce jeu, s'il en a un."""
    libs = steam_libraries()
    if not libs:
        return None
    name = game.get("name") or ""
    exe = os.path.realpath(game["exe"]) if game.get("exe") else ""

    def make(cid, label, kind):
        fallback = None
        for lib in libs:
            su = lib / "compatdata" / str(cid) / "pfx/drive_c/users/steamuser"
            if su.is_dir():
                src = {"id": str(cid), "label": label or name, "kind": kind, "path": str(su),
                       "has_data": has_user_data(su)}
                if src["has_data"]:
                    return src
                fallback = fallback or src
        return fallback

    # 1. L'exécutable est installé dans un préfixe Steam.
    m = re.search(r"/compatdata/(\d+)/pfx/", exe)
    if m and (src := make(m.group(1), name, "prefix")):
        return src
    manifests = app_manifests()
    # 2. L'exécutable est dans steamapps/common/<jeu>.
    for man in manifests:
        if exe and man.get("installdir") and is_inside(exe, Path(man["library"]) / "common" / man["installdir"]):
            if src := make(man["appid"], man.get("name"), "steam"):
                return src
    # 3. Un raccourci non-Steam pointe vers le même exécutable (ou porte le même nom).
    best = None
    for sc in steam_shortcuts():
        sexe = os.path.realpath(sc["exe"]) if sc["exe"] else ""
        if exe and sexe == exe:
            score = 1.0
        elif exe and sc["startdir"] and is_inside(exe, sc["startdir"]):
            score = 0.95
        else:
            score = similarity(sc["name"], name)
        if score >= 0.85 and (best is None or score > best[0]):
            best = (score, sc)
    if best and (src := make(best[1]["appid"], best[1]["name"], "shortcut")):
        return src
    # 4. L'identifiant Steam trouvé lors de la recherche de jaquette.
    aid = str(game.get("steam_appid") or "")
    gm = re.fullmatch(r"umu-(\d+)", (game.get("gameid") or "").strip())
    for cand in filter(None, {aid, gm.group(1) if gm else ""}):
        label = next((x.get("name") for x in manifests if x["appid"] == cand), name)
        if src := make(cand, label, "steam"):
            return src
    # 5. Un jeu Steam installé porte le même nom.
    scored = sorted(((similarity(x.get("name", ""), name), x) for x in manifests), key=lambda t: -t[0])
    for score, man in scored[:3]:
        if score >= 0.85 and (src := make(man["appid"], man.get("name"), "steam")):
            return src
    return None


def newest_save(base):
    newest = 0.0
    for r, _rel, files in _walk_saves(Path(base)):
        for f in files:
            try:
                newest = max(newest, (r / f).stat().st_mtime)
            except OSError:
                pass
    return newest


def import_all_from_steam(store, log=print):
    """Importe les sauvegardes Steam de tous les jeux de la bibliothèque qui ne l'ont pas encore été.
    Ne remplace jamais des sauvegardes Lanterne plus récentes que celles de Steam."""
    imported = 0
    for g in store.games:
        if g.get("steam_import_done"):
            log(_('  · {0} : sauvegardes déjà importées').format(g['name']))
            continue
        src = find_steam_source(g)
        if not src:
            log(_('  · {0} : introuvable dans Steam (ni jeu Steam, ni raccourci non-Steam)').format(g['name']))
            continue
        if not src["has_data"]:
            log(_('  · {0} : trouvé dans Steam ({1}), mais son préfixe ne contient aucune sauvegarde. Elles sont sans doute dans le dossier du jeu, déjà partagé.').format(g['name'], src['label']))
            g["steam_import_done"] = True
            continue
        dst = Path(g["prefix"]).expanduser() / "drive_c/users/steamuser"
        if newest_save(dst) > newest_save(src["path"]):
            g["steam_import_done"] = True
            log(_('  · {0} : sauvegardes Lanterne plus récentes que celles de Steam, conservées').format(g['name']))
            continue
        count, size, backup = import_saves(src["path"], g["prefix"], g["id"])
        g["steam_import_done"] = True
        imported += 1
        line = _n("  ✓ {name} : {n} fichier ({size}) depuis {src} ({kind})",
                  "  ✓ {name} : {n} fichiers ({size}) depuis {src} ({kind})", count).format(
            name=g["name"], n=count, size=fmt_size(size), src=src["label"], kind=SOURCE_KINDS[src["kind"]])
        if backup:
            line += _('\n      anciennes sauvegardes mises de côté dans {0}').format(short_path(backup))
        log(line)
    store.save()
    return imported


WIN_SUFFIXES = {".exe", ".bat", ".msi", ".lnk"}


def compat_tool_for(appid):
    """Nom de la version de Proton choisie dans Steam pour ce jeu, s'il y en a une."""
    for root in steam_roots():
        try:
            text = (root / "config/config.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'"%s"\s*\{[^{}]*?"name"\s*"([^"]*)"' % re.escape(str(appid)), text)
        if m:
            return m.group(1)
    return ""


def parse_launch_options(opts):
    """Convertit les options de lancement Steam (« VAR=1 gamemoderun %command% -dx11 »)."""
    opts = (opts or "").strip()
    flags = {}
    if "%command%" not in opts:
        return "", opts, flags
    before, _, after = opts.partition("%command%")
    try:
        tokens = shlex.split(before)
    except ValueError:
        tokens = []
    env = []
    for t in tokens:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            if t == "MANGOHUD=1":
                flags["mangohud"] = True
            else:
                env.append(shlex.quote(t))
        elif t == "gamemoderun":
            flags["gamemode"] = True
        elif t == "mangohud":
            flags["mangohud"] = True
    return " ".join(env), after.strip(), flags


def import_steam_shortcuts(store, log=print):
    """Ajoute à Lanterne les jeux Windows non-Steam présents dans Steam."""
    known_exes = {os.path.realpath(g["exe"]) for g in store.games if g.get("exe")}
    known_names = {_squash(g["name"]) for g in store.games}
    installed = {n: str(p) for n, p in scan_protons()}
    added = 0
    for sc in steam_shortcuts():
        exe = sc["exe"]
        if Path(exe).suffix.lower() not in WIN_SUFFIXES:
            continue  # raccourci vers une appli Linux (Heroic, Lutris…)
        name = sc["name"] or guess_name(exe)
        real = os.path.realpath(exe)
        if real in known_exes or _squash(name) in known_names:
            continue
        if not Path(real).is_file():
            log(_('  · {0} : exécutable introuvable ({1}), ignoré').format(name, short_path(exe)))
            continue
        gid = uuid.uuid4().hex[:12]
        env, args, flags = parse_launch_options(sc.get("launch"))
        game = {**GAME_DEFAULTS, "id": gid, "name": name, "exe": real, "args": args, "env": env, **flags,
                "proton": installed.get(compat_tool_for(sc["appid"]), "auto"), "added": int(time.time()),
                "sandbox": sandbox_ok(real)}
        game["prefix"] = default_prefix(name, gid, store)
        for ext in ("png", "jpg", "jpeg", "webp"):  # jaquette choisie dans Steam
            grid = Path(sc["grid_dir"]) / f"{sc['appid']}p.{ext}"
            if grid.is_file():
                try:
                    game["cover"] = store_cover(gid, str(grid))
                    game["cover_auto"] = True
                except OSError:
                    pass
                break
        note = _("ajouté")
        m = re.match(r"^(.*/compatdata/\d+/pfx)/(.+)$", real)
        if m:  # jeu installé dans le préfixe Steam : on récupère tout le préfixe
            log(_('  … {0} : copie de son préfixe Steam (jeu et sauvegardes)…').format(name))
            shutil.copytree(m.group(1), game["prefix"], symlinks=True, dirs_exist_ok=True)
            game["exe"] = str(Path(game["prefix"]) / m.group(2))
            game["sandbox"] = True
            game["steam_import_done"] = True
            note = _("ajouté avec tout son préfixe Steam")
        store.games.append(game)
        store.save()
        known_exes.add(real)
        known_names.add(_squash(name))
        added += 1
        log(_("  + {0} : {1}").format(name, note))
    return added


def import_everything_from_steam(store, log=print):
    log(_("Jeux non-Steam trouvés dans Steam :"))
    added = import_steam_shortcuts(store, log)
    if not added:
        log(_("  aucun nouveau jeu à ajouter"))
    log("")
    log(_("Sauvegardes :"))
    if not store.games:
        log(_("  aucun jeu dans Lanterne"))
        return added, 0
    return added, import_all_from_steam(store, log)


def steam_diagnostic():
    print(_("Dossiers Steam :"))
    for r in steam_roots() or []:
        print(f"  {short_path(r)}")
    if not steam_roots():
        print(_("  aucun trouvé parmi :"), ", ".join(short_path(r) for r in STEAM_ROOTS))
    print(_("Bibliothèques :"))
    for lib in steam_libraries():
        cd = lib / "compatdata"
        n = len([d for d in cd.iterdir() if d.is_dir()]) if cd.is_dir() else 0
        print(_('  {0}  ({1} préfixes dans compatdata)').format(short_path(lib), n))
    scs = steam_shortcuts()
    print(_('Jeux non-Steam dans Steam : {0}').format(len(scs)))
    for sc in scs:
        pfx = next((lib / "compatdata" / sc["appid"] / "pfx" for lib in steam_libraries()
                    if (lib / "compatdata" / sc["appid"] / "pfx").is_dir()), None)
        su = pfx / "drive_c/users/steamuser" if pfx else None
        print(f"  {sc['name'] or _('(sans nom)')}  [{sc['appid']}]")
        print(_('    exécutable : {0}').format(short_path(sc['exe']) or _('(vide)')))
        print(_('    préfixe    : {0}').format(short_path(pfx) if pfx else _('aucun (jamais lancé avec Proton ?)')))
        if su:
            print(_("    sauvegardes: {0}").format(_("oui") if has_user_data(su) else _("aucune dans le préfixe")))
    store = Store()
    print(_('Jeux dans Lanterne : {0}').format(len(store.games)))
    for g in store.games:
        src = find_steam_source(g)
        print(f"  {g['name']}  ->  " + (_("{0} [{1}], sauvegardes : {2}").format(
            src["label"], src["id"], _("oui") if src["has_data"] else _("non")) if src else _("pas trouvé dans Steam")))
        print(_('    exécutable : {0}').format(short_path(g['exe'])))


SAVE_MANIFEST = "lanterne-sauvegardes.json"


def save_dir(prefix):
    return Path(prefix).expanduser() / "drive_c/users/steamuser"


def export_saves(prefix, dest, game_name):
    """Exporte les sauvegardes d'un jeu dans une archive .zip. Retourne le nombre de fichiers."""
    base = save_dir(prefix)
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".part")
    count = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(SAVE_MANIFEST, json.dumps({"jeu": game_name, "date": int(time.time()),
                                                  "lanterne": VERSION}, ensure_ascii=False, indent=2))
            for r, rel, files in _walk_saves(base):
                for f in files:
                    z.write(r / f, (PurePosixPath(*rel.parts) / f).as_posix())
                    count += 1
        if not count:
            raise RuntimeError(_("Ce jeu n’a aucune sauvegarde à exporter."))
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return count


def read_save_archive(path):
    """Vérifie une archive de sauvegardes. Retourne (infos, nombre de fichiers)."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise RuntimeError(_("Ce fichier n’est pas une archive .zip valide.")) from e
    with z:
        meta = {}
        files = []
        for info in z.infolist():
            name = info.filename
            if name == SAVE_MANIFEST:
                try:
                    meta = json.loads(z.read(name).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    meta = {}
                continue
            if info.is_dir():
                continue
            parts = PurePosixPath(name).parts
            if (name.startswith("/") or "\\" in name or ".." in parts or not parts
                    or parts[0] not in SAVE_DIRS):
                raise RuntimeError(_("Cette archive contient des fichiers hors des dossiers de sauvegarde "
                                   "(Documents, Saved Games, AppData) : import refusé."))
            files.append(name)
    if not files:
        raise RuntimeError(_("Cette archive ne contient aucune sauvegarde."))
    return meta, len(files)


def import_save_archive(path, prefix, gid):
    """Importe une archive de sauvegardes, après avoir mis de côté les sauvegardes actuelles."""
    read_save_archive(path)
    dst = save_dir(prefix)
    dst.mkdir(parents=True, exist_ok=True)
    root = dst.resolve()
    backup = None
    if has_user_data(dst):
        backup = BACKUPS_DIR / f"{gid}-{datetime.now():%Y%m%d-%H%M%S}"
        copy_saves(dst, backup)
    count = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir() or info.filename == SAVE_MANIFEST:
                continue
            target = dst / PurePosixPath(info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            parent = target.parent.resolve()
            if (parent != root and root not in parent.parents) or target.is_symlink() or target.is_dir():
                continue  # jamais d'écriture hors du préfixe
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            mtime = time.mktime(info.date_time + (0, 0, -1))
            os.utime(target, (mtime, mtime))
            count += 1
    return count, backup


def import_saves(src_user, prefix, gid):
    """Copie les sauvegardes Steam dans le préfixe du jeu, après avoir mis de côté les actuelles."""
    dst = Path(prefix).expanduser() / "drive_c/users/steamuser"
    backup = None
    if dst.is_dir() and has_user_data(dst):
        backup = BACKUPS_DIR / f"{gid}-{datetime.now():%Y%m%d-%H%M%S}"
        copy_saves(dst, backup)
    count, size = copy_saves(src_user, dst)
    return count, size, backup


# ──────────────────────────────────────────────────────── bibliothèque ──

class Store:
    def __init__(self):
        self.games = []
        self.settings = dict(SETTINGS_DEFAULTS)
        self.load()

    def load(self):
        try:
            data = json.loads(LIB_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            try:
                shutil.copy2(LIB_FILE, LIB_FILE.with_suffix(".json.illisible"))
            except OSError:
                pass
            return
        self.settings.update(data.get("settings", {}))
        self.games = [{**GAME_DEFAULTS, **g} for g in data.get("games", []) if g.get("id")]

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = LIB_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "settings": self.settings, "games": self.games},
                                      indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, LIB_FILE)
        except OSError as e:
            print(_('Sauvegarde impossible : {0}').format(e), file=sys.stderr)

    def get(self, gid):
        return next((g for g in self.games if g["id"] == gid), None)

    def upsert(self, game):
        for i, g in enumerate(self.games):
            if g["id"] == game["id"]:
                self.games[i] = game
                break
        else:
            self.games.append(game)
        self.save()

    def remove(self, gid):
        self.games = [g for g in self.games if g["id"] != gid]
        self.save()


def default_prefix(name, gid, store):
    used = {str(Path(g["prefix"]).expanduser()) for g in store.games if g["id"] != gid and g.get("prefix")}
    base = slugify(name)
    candidate, n = PREFIXES_DIR / base, 2
    while str(candidate) in used:
        candidate, n = PREFIXES_DIR / f"{base}-{n}", n + 1
    return str(candidate)


def store_cover(gid, src):
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = COVERS_DIR / f"{gid}-{int(time.time())}{Path(src).suffix.lower() or '.png'}"
    shutil.copy2(src, dest)
    return str(dest)


def delete_cover(path):
    if path and is_inside(path, COVERS_DIR):
        Path(path).unlink(missing_ok=True)


def shortcut_path(gid):
    return APPS_DIR / f"lanterne-{gid}.desktop"


def write_shortcut(game):
    script = Path(sys.argv[0]).resolve()
    icon = game["cover"] if game.get("cover") and Path(game["cover"]).is_file() else APP_ID
    name = game["name"].replace("\n", " ")
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    p = shortcut_path(game["id"])
    p.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={name}\nComment={_('Lancé avec Lanterne')}\n"
        f'Exec="{sys.executable}" "{script}" --launch {game["id"]}\n'
        f"Icon={icon}\nCategories=Game;\nTerminal=false\n", encoding="utf-8")
    p.chmod(0o755)


def remove_shortcut(gid):
    shortcut_path(gid).unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────── processus ──

class Runner(GObject.Object):
    """Lance les jeux, suit ceux en cours et compte le temps de jeu."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (str, int, float)),
    }

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.procs = {}
        self.starts = {}

    def is_running(self, gid):
        return gid in self.procs

    def _spawn(self, cmd, env, cwd, logpath):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(logpath, "w", encoding="utf-8") as log:
            log.write(f"# Lanterne {VERSION} — {datetime.now():%d/%m/%Y %H:%M:%S}\n"
                      f"# Commande   : {shlex.join(cmd)}\n"
                      f"# PROTONPATH : {env.get('PROTONPATH')}\n"
                      f"# WINEPREFIX : {env.get('WINEPREFIX')}\n"
                      + (f"# Isolé, lecture-écriture : {env['PRESSURE_VESSEL_FILESYSTEMS_RW']}\n"
                         f"# Isolé, lecture seule    : {env.get('PRESSURE_VESSEL_FILESYSTEMS_RO', '')}\n"
                         if env.get("PRESSURE_VESSEL_HOME") else "") + "\n")
            log.flush()
            return subprocess.Popen(cmd, env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)

    def launch(self, game):
        gid = game["id"]
        if gid in self.procs:
            return None
        cmd, env, cwd = build_command(game, self.app.store.settings)
        hint = first_run_hint(game, self.app.store.settings)
        proc = self._spawn(cmd, env, cwd, LOGS_DIR / f"{gid}.log")
        self.procs[gid] = proc
        self.starts[gid] = time.monotonic()
        game["last_played"] = int(time.time())
        self.app.store.save()
        self.app.hold()
        threading.Thread(target=self._wait, args=(gid, proc), daemon=True).start()
        self.emit("changed", gid)
        return hint

    def _wait(self, gid, proc):
        code = proc.wait()
        GLib.idle_add(self._on_exit, gid, code)

    def _on_exit(self, gid, code):
        self.procs.pop(gid, None)
        duration = time.monotonic() - self.starts.pop(gid, time.monotonic())
        game = self.app.store.get(gid)
        if game:
            game["playtime"] = int(game.get("playtime", 0) + duration)
            game["last_played"] = int(time.time())
            self.app.store.save()
        self.emit("changed", gid)
        self.emit("finished", gid, int(code), float(duration))
        if self.app.win is None and duration < 15 and game:
            self.app.notify(_('« {0} » s’est fermé au bout de {1} s. Ouvre Lanterne pour consulter son journal.').format(game['name'], int(duration)))
        self.app.release()
        return False

    def stop(self, gid):
        proc = self.procs.get(gid)
        if not proc:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        GLib.timeout_add_seconds(6, self._force_kill, gid, proc)

    def _force_kill(self, gid, proc):
        if self.procs.get(gid) is proc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        return False

    def run_tool(self, game, program, extra_args=None, on_exit=None, log_suffix="outil"):
        """Lance un programme dans le préfixe du jeu, sans compter de temps de jeu."""
        cmd, env, cwd = build_command(game, self.app.store.settings, program=program, extra_args=extra_args)
        proc = self._spawn(cmd, env, cwd, LOGS_DIR / f"{game['id']}-{log_suffix}.log")

        def wait():
            code = proc.wait()
            if on_exit:
                GLib.idle_add(_call_once, on_exit, code)
        threading.Thread(target=wait, daemon=True).start()


# ─────────────────────────────────────────────────────────────── style ──

PALETTES = [
    ("#f97316", "#be185d"), ("#06b6d4", "#4338ca"), ("#a855f7", "#db2777"),
    ("#10b981", "#0e7490"), ("#f59e0b", "#b91c1c"), ("#6366f1", "#0284c7"),
    ("#14b8a6", "#4d7c0f"), ("#e11d48", "#6d28d9"), ("#0f766e", "#1e3a8a"),
    ("#c026d3", "#ea580c"),
]

# La lueur ambrée est la signature de Lanterne : un jeu en cours « s'allume ».
GLOW = "#f5b83d"

CSS = """
flowbox.library > flowboxchild { padding: 0; margin: 0; border-radius: 14px; background: none; }
flowbox.library > flowboxchild:focus-visible { outline: 3px solid @accent_color; outline-offset: 4px; }

.game-card { border-radius: 14px; box-shadow: 0 6px 18px rgba(0,0,0,.28); }
.game-card.running { box-shadow: 0 0 0 3px GLOW, 0 0 34px alpha(GLOW, .55); }

.card-caption {
  background-image: linear-gradient(to top, rgba(0,0,0,.88), rgba(0,0,0,.55) 55%, rgba(0,0,0,0));
  padding: 40px 12px 12px 12px;
}
.card-title { color: white; font-weight: 800; font-size: 15px; }
.card-sub { color: rgba(255,255,255,.72); font-size: 12px; }

.card-play {
  min-width: 58px; min-height: 58px; padding: 0; border-radius: 999px;
  background-color: @accent_bg_color; color: @accent_fg_color;
  box-shadow: 0 6px 18px rgba(0,0,0,.45);
  opacity: 0; transition: opacity 140ms ease-out;
}
.card-play image { -gtk-icon-size: 24px; }
flowbox.library > flowboxchild:hover .card-play,
.card-play:focus-visible { opacity: 1; }
.card-play.running { opacity: 1; background-color: GLOW; color: #2b1a00; }

.badge-running {
  background-color: GLOW; color: #2b1a00; border-radius: 999px;
  padding: 3px 10px; font-size: 11px; font-weight: 800;
}

.cover-frame { border-radius: 12px; box-shadow: 0 8px 22px rgba(0,0,0,.30); }
.detail-cover { border-radius: 16px; box-shadow: 0 22px 48px rgba(0,0,0,.50); }
.detail-cover.running { box-shadow: 0 0 0 3px GLOW, 0 0 60px alpha(GLOW, .55); }
.cover-initial { color: rgba(255,255,255,.92); font-weight: 900; }
.cover-initial.s1 { font-size: 18px; }
.cover-initial.s2 { font-size: 34px; }
.cover-initial.s3 { font-size: 58px; }
.cover-initial.s4 { font-size: 84px; }

.backdrop { filter: blur(52px) saturate(1.4); opacity: .55; }
.backdrop-fade {
  background-image: linear-gradient(to bottom, alpha(@window_bg_color, .10),
                                    alpha(@window_bg_color, .70) 62%, @window_bg_color);
}

.hero {
  padding: 20px 26px 20px 20px; border-radius: 20px;
  background-color: alpha(currentColor, .05);
}
.hero-kicker { font-size: 13px; font-weight: 700; color: @accent_color; }
.hero-title { font-size: 28px; font-weight: 900; }
.lib-title { font-size: 22px; font-weight: 800; }

.detail-title { font-size: 38px; font-weight: 900; }
.stat { background-color: alpha(currentColor, .07); border-radius: 12px; padding: 10px 16px; }
.stat-label { font-size: 12px; font-weight: 600; opacity: .65; }
.stat-value { font-size: 16px; font-weight: 700; }
.chip { background-color: alpha(currentColor, .09); border-radius: 999px; padding: 4px 12px; font-size: 13px; font-weight: 600; }
.tag { background-color: alpha(currentColor, .10); border-radius: 999px; padding: 2px 10px; font-size: 12px; font-weight: 700; }
.tag.accent { background-color: @accent_bg_color; color: @accent_fg_color; }

.play-button { padding: 10px 34px; font-size: 16px; font-weight: 800; }
.play-button.running { background-color: GLOW; color: #2b1a00; }
""".replace("GLOW", GLOW) + "".join(
    f".cover-{i} {{ background-image: linear-gradient(135deg, {a}, {b}); }}\n"
    for i, (a, b) in enumerate(PALETTES))


# ──────────────────────────────────────────────────── widgets communs ──

_textures = {}


def load_texture(path):
    if not path:
        return None
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key not in _textures:
        try:
            _textures[key] = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            return None
    return _textures[key]


class CoverImage(Gtk.Widget):
    """Image recadrée façon « cover », à taille fixe (ou extensible si sans taille)."""

    __gtype_name__ = "LanterneCoverImage"

    def __init__(self, texture, width=None, height=None):
        super().__init__()
        self.texture = texture
        self.fixed = (width, height) if width else None

    def do_measure(self, orientation, for_size):
        if not self.fixed:
            return 0, 0, -1, -1
        v = self.fixed[0] if orientation == Gtk.Orientation.HORIZONTAL else self.fixed[1]
        return v, v, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        tw, th = self.texture.get_width(), self.texture.get_height()
        if w <= 0 or h <= 0 or tw <= 0 or th <= 0:
            return
        scale = max(w / tw, h / th)
        dw, dh = tw * scale, th * scale
        rect = Graphene.Rect().init((w - dw) / 2, (h - dh) / 2, dw, dh)
        snapshot.push_clip(Graphene.Rect().init(0, 0, w, h))
        if hasattr(snapshot, "append_scaled_texture"):
            snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR, rect)
        else:
            snapshot.append_texture(self.texture, rect)
        snapshot.pop()


def cover_widget(game, w=None, h=None, show_initial=True):
    tex = load_texture(game.get("cover"))
    if tex:
        return CoverImage(tex, w, h)
    box = Gtk.Box(css_classes=[f"cover-{palette_index(game.get('name') or '?')}"])
    if w:
        box.set_size_request(w, h)
    if show_initial:
        size = "s4" if (w or 0) >= 200 else "s3" if (w or 0) >= 140 else "s2" if (w or 0) >= 90 else "s1"
        box.append(Gtk.Label(label=initials(game.get("name") or "?"), hexpand=True, vexpand=True,
                             css_classes=["cover-initial", size]))
    return box


def make_frame(game, w, h, extra_class=None):
    frame = Gtk.Overlay(halign=Gtk.Align.START, valign=Gtk.Align.START, css_classes=["cover-frame"])
    if extra_class:
        frame.add_css_class(extra_class)
    frame.set_overflow(Gtk.Overflow.HIDDEN)
    frame.set_child(cover_widget(game, w, h))
    return frame


def make_button(label=None, icon=None, cb=None, classes=(), tooltip=None):
    b = Gtk.Button(valign=Gtk.Align.CENTER)
    if label and icon:
        b.set_child(Adw.ButtonContent(icon_name=icon, label=label))
    elif label:
        b.set_label(label)
    elif icon:
        b.set_icon_name(icon)
    for c in classes:
        b.add_css_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    if cb:
        b.connect("clicked", lambda *_: cb())
    return b


def tag_label(text, accent=False):
    lbl = Gtk.Label(label=text, valign=Gtk.Align.CENTER, css_classes=["tag"])
    if accent:
        lbl.add_css_class("accent")
    return lbl


def stat_tile(label, value):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["stat"])
    box.append(Gtk.Label(label=label, xalign=0, css_classes=["stat-label"]))
    box.append(Gtk.Label(label=value, xalign=0, css_classes=["stat-value"]))
    return box


def file_filter(name, suffixes=(), pixbuf=False, everything=False):
    f = Gtk.FileFilter()
    f.set_name(name)
    for s in suffixes:
        f.add_suffix(s)
    if pixbuf:
        f.add_pixbuf_formats()
    if everything:
        f.add_pattern("*")
    return f


def filters(*items):
    store = Gio.ListStore.new(Gtk.FileFilter)
    for f in items:
        store.append(f)
    return store


def exe_filters():
    return filters(file_filter(_("Programmes Windows"), ("exe", "msi", "bat", "lnk")),
                   file_filter(_("Tous les fichiers"), everything=True))


# ────────────────────────────────────────────────── fenêtre d'édition ──

COVER_HINT = _("Trouvée automatiquement, ou une image verticale de 600 × 900")


class CoverSearchDialog(Adw.Dialog):
    """Recherche de jaquettes sur Steam, avec aperçus cliquables."""

    THUMB_W, THUMB_H = 150, 225

    def __init__(self, win, query, on_pick):
        super().__init__(title=_("Chercher une jaquette"), content_width=780, content_height=640)
        self.win = win
        self.on_pick = on_pick
        self.items = []
        self._req = 0
        self._timer = 0
        self._closed = False
        self.connect("closed", self._on_closed)

        header = Adw.HeaderBar()
        self.entry = Gtk.SearchEntry(text=query, hexpand=True, placeholder_text=_("Nom du jeu"))
        self.entry.connect("search-changed", self._on_changed)
        self.entry.connect("activate", lambda *_: self.search())
        eclamp = Adw.Clamp(maximum_size=420)
        eclamp.set_child(self.entry)
        header.set_title_widget(eclamp)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.status = Adw.StatusPage(icon_name="system-search-symbolic")
        self.stack.add_named(self.status, "status")
        self.stack.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                         width_request=32, height_request=32), "loading")
        self.flow = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                                activate_on_single_click=True, column_spacing=18, row_spacing=22,
                                max_children_per_line=8, valign=Gtk.Align.START,
                                margin_start=24, margin_end=24, margin_top=20, margin_bottom=24,
                                css_classes=["library"])
        self.flow.connect("child-activated", self._on_activated)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.flow)
        self.stack.add_named(scroller, "results")

        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.set_content(self.stack)
        self.set_child(tv)
        self.search()

    def _on_closed(self, *_):
        self._closed = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_changed(self, *_):
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(450, self._fire)

    def _fire(self):
        self._timer = 0
        self.search()
        return False

    def _show_status(self, title, description=""):
        self.status.set_title(title)
        self.status.set_description(description)
        self.stack.set_visible_child_name("status")

    def search(self):
        q = self.entry.get_text().strip()
        if not q:
            self._show_status(_("Tape le nom du jeu"))
            return
        self._req += 1
        req = self._req
        self.stack.set_visible_child_name("loading")
        run_async(lambda: steam_search(q), lambda res: self._show(req, q, res),
                  lambda e: self._error(req, e))

    def _error(self, req, e):
        if not self._closed and req == self._req:
            self._show_status(_("Recherche impossible"), nice_error(e))

    def _show(self, req, query, results):
        if self._closed or req != self._req:
            return
        while (c := self.flow.get_first_child()) is not None:
            self.flow.remove(c)
        results.sort(key=lambda i: -similarity(i.get("name", ""), query))
        self.items = results[:12]
        if not self.items:
            self._show_status(_("Aucun jeu trouvé"), _("Essaie le nom anglais du jeu, ou un nom plus court."))
            return
        for item in self.items:
            card = Gtk.Overlay(halign=Gtk.Align.CENTER, css_classes=["game-card"])
            card.set_overflow(Gtk.Overflow.HIDDEN)
            card.set_child(cover_widget({"name": item.get("name", "?")}, self.THUMB_W, self.THUMB_H))
            cap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, css_classes=["card-caption"])
            cap.append(Gtk.Label(label=item.get("name", ""), xalign=0, wrap=True, lines=2, max_width_chars=1,
                                 ellipsize=Pango.EllipsizeMode.END, hexpand=True, css_classes=["card-title"]))
            card.add_overlay(cap)
            child = Gtk.FlowBoxChild()
            child.set_child(card)
            self.flow.append(child)
            self._load_thumb(req, item, card)
        self.stack.set_visible_child_name("results")

    def _load_thumb(self, req, item, card):
        dest = PREVIEWS_DIR / f"steam-{item['id']}.jpg"

        def done(path):
            if self._closed or req != self._req or not path:
                return
            tex = load_texture(str(path))
            if tex:
                card.set_child(CoverImage(tex, self.THUMB_W, self.THUMB_H))
        run_async(lambda: dest if dest.is_file() else fetch_cover(item, dest), done)

    def _on_activated(self, _flow, child):
        idx = child.get_index()
        if not 0 <= idx < len(self.items):
            return
        item = self.items[idx]
        dest = PREVIEWS_DIR / f"steam-{item['id']}.jpg"
        if dest.is_file():
            self._pick(item, dest)
            return

        def done(path):
            if path:
                self._pick(item, path)
            else:
                self.win.toast(_("Aucune image disponible pour ce jeu sur Steam."))
        run_async(lambda: fetch_cover(item, dest), done)

    def _pick(self, item, path):
        self.on_pick(str(path), item)
        self.close()


class GameDialog(Adw.Dialog):
    def __init__(self, win, game=None):
        super().__init__()
        self.win = win
        self.store = win.store
        self.is_new = game is None
        self.gid = game["id"] if game else uuid.uuid4().hex[:12]
        g = {**GAME_DEFAULTS, **(game or {})}
        if self.is_new:
            g["proton"] = self.store.settings.get("default_proton", "auto")
            g["sandbox"] = True
        self.exe = g["exe"]
        self.cover_src = g["cover"]
        self.cover_changed = False
        self.cover_auto_ok = not g["cover"]  # la recherche auto peut-elle choisir la jaquette ?
        self.cover_searched = bool(g.get("cover_auto"))
        self.steam_appid = str(g.get("steam_appid") or "")
        self.steam_source = None
        self._cover_req = 0
        self._detect_req = 0
        self._closed = False
        self.connect("closed", self._on_closed)

        self.set_title(_("Ajouter un jeu") if self.is_new else _("Modifier le jeu"))
        self.set_content_width(620)
        self.set_content_height(800)

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(make_button(_("Annuler"), cb=self.close))
        self.save_btn = make_button(_("Ajouter") if self.is_new else _("Enregistrer"), cb=self.on_save,
                                    classes=("suggested-action",))
        header.pack_end(self.save_btn)
        page = Adw.PreferencesPage()
        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.set_content(page)
        self.set_child(tv)

        # Jeu
        grp = Adw.PreferencesGroup(title=_("Jeu"))
        page.add(grp)
        self.name_row = Adw.EntryRow(title=_("Nom"))
        self.name_row.set_text(g["name"])
        self.name_row.connect("changed", self._on_name_changed)
        grp.add(self.name_row)

        self.exe_row = Adw.ActionRow(title=_("Exécutable"), use_markup=False, subtitle_lines=2)
        pick = make_button(_("Choisir…"), cb=self.pick_exe)
        self.exe_row.add_suffix(pick)
        self.exe_row.set_activatable_widget(pick)
        grp.add(self.exe_row)

        inst = Adw.ActionRow(title=_("Pas encore installé ?"),
                             subtitle=_("Lance son installeur dans le préfixe de ce jeu, puis choisis l’exécutable."))
        b = make_button(_("Lancer un installeur…"), cb=self.pick_installer)
        inst.add_suffix(b)
        inst.set_activatable_widget(b)
        grp.add(inst)

        self.cover_row = Adw.ActionRow(title=_("Jaquette"), subtitle=COVER_HINT, use_markup=False)
        self.cover_slot = Gtk.Box(valign=Gtk.Align.CENTER, margin_top=8, margin_bottom=8)
        self.cover_row.add_prefix(self.cover_slot)
        self.clear_cover_btn = make_button(icon="edit-clear-symbolic", cb=self.clear_cover,
                                           classes=("flat",), tooltip=_("Retirer la jaquette"))
        self.cover_row.add_suffix(self.clear_cover_btn)
        self.cover_row.add_suffix(make_button(_("Chercher en ligne…"), cb=self.open_cover_search))
        self.cover_row.add_suffix(make_button(icon="document-open-symbolic", cb=self.pick_cover,
                                              classes=("flat",), tooltip=_("Choisir une image sur le disque")))
        grp.add(self.cover_row)

        # Import des sauvegardes Steam (à l'ajout seulement)
        if self.is_new:
            grp_s = Adw.PreferencesGroup(title="Steam")
            page.add(grp_s)
            self.import_row = Adw.SwitchRow(title=_("Importer les sauvegardes de Steam"), active=True,
                                            use_markup=False,
                                            subtitle=_("Choisis l’exécutable : Lanterne cherchera le jeu dans Steam"))
            grp_s.add(self.import_row)

        # Compatibilité
        grp2 = Adw.PreferencesGroup(title=_("Compatibilité"))
        page.add(grp2)
        self.proton_values, labels = proton_options(g["proton"])
        self.proton_row = Adw.ComboRow(title=_("Version de Proton"), model=Gtk.StringList.new(labels))
        if g["proton"] in self.proton_values:
            self.proton_row.set_selected(self.proton_values.index(g["proton"]))
        grp2.add(self.proton_row)

        self.prefix_row = Adw.EntryRow(title=_("Préfixe Wine (laisser vide pour le créer automatiquement)"))
        self.prefix_row.set_text(g["prefix"])
        self.prefix_row.add_suffix(make_button(icon="folder-open-symbolic", cb=self.pick_prefix,
                                               classes=("flat",), tooltip=_("Choisir un dossier")))
        grp2.add(self.prefix_row)

        self.gameid_row = Adw.EntryRow(title=_("Identifiant umu (facultatif, active les correctifs du jeu)"))
        self.gameid_row.set_text(g["gameid"])
        grp2.add(self.gameid_row)

        # Confidentialité
        grp_p = Adw.PreferencesGroup(title=_("Confidentialité"))
        page.add(grp_p)
        self.sandbox_row = Adw.SwitchRow(
            title=_("Isoler de tes fichiers personnels"), active=bool(g["sandbox"]),
            subtitle=_("Le jeu ne voit que son dossier et son préfixe. Désactive-le s’il a besoin de mods "
                     "ou de fichiers rangés ailleurs."))
        grp_p.add(self.sandbox_row)

        # Performances
        grp3 = Adw.PreferencesGroup(title=_("Performances et affichage"))
        page.add(grp3)
        has_mango = shutil.which("mangohud") is not None
        has_gm = shutil.which("gamemoderun") is not None
        self.switches = {}
        for key, title, sub, available in (
            ("mangohud", "MangoHud", _("Affiche les FPS, températures et charges en jeu"), has_mango),
            ("gamemode", "GameMode", _("Optimise le processeur pendant la partie"), has_gm),
            ("fsr", _("FSR plein écran"), _("Agrandit l’image quand le jeu tourne sous ta résolution native"), True),
            ("wayland", _("Wayland natif"), _("Expérimental, demande une GE-Proton récente"), True),
        ):
            row = Adw.SwitchRow(title=title, subtitle=sub if available else _("Non installé sur ce système"))
            row.set_active(bool(g[key]) and available)
            row.set_sensitive(available)
            grp3.add(row)
            self.switches[key] = row

        # Avancé
        grp4 = Adw.PreferencesGroup(title=_("Avancé"),
                                    description=_("Séparés par des espaces, entre guillemets s’ils en contiennent."))
        page.add(grp4)
        self.args_row = Adw.EntryRow(title=_("Arguments de lancement"))
        self.args_row.set_text(g["args"])
        grp4.add(self.args_row)
        self.env_row = Adw.EntryRow(title=_("Variables d’environnement (ex. DXVK_HUD=fps)"))
        self.env_row.set_text(g["env"])
        grp4.add(self.env_row)

        if not self.is_new:
            grp5 = Adw.PreferencesGroup()
            page.add(grp5)
            delete = make_button(_("Supprimer ce jeu…"), "user-trash-symbolic", classes=("pill", "destructive-action"))
            delete.set_halign(Gtk.Align.CENTER)
            delete.connect("clicked", self._on_delete)
            grp5.add(delete)

        self.update_exe_row()
        self.update_cover_preview()
        self.validate()

    def _on_delete(self, *_):
        gid = self.gid
        self.close()
        self.win.delete_game(gid)

    # état
    def _on_closed(self, *_):
        self._closed = True

    def _on_name_changed(self, *_):
        self.validate()
        if not self.cover_src:
            self.update_cover_preview()

    # recherche automatique (jaquette + Steam)
    def set_appid(self, aid):
        self.steam_appid = str(aid)
        if not self.gameid_row.get_text().strip():
            self.gameid_row.set_text(f"umu-{aid}")
        self.detect_steam()

    def auto_cover(self):
        if not self.cover_auto_ok or not self.exe:
            return
        name = self.name_row.get_text().strip()
        exe = self.exe
        self._cover_req += 1
        req = self._cover_req
        self.cover_row.set_subtitle(_("Recherche d’une jaquette sur Steam…"))

        def done(res):
            if self._closed or req != self._cover_req or not self.cover_auto_ok:
                return
            self.cover_searched = True
            if not res:
                self.cover_row.set_subtitle(_("Aucune jaquette trouvée : cherche-la ou choisis une image"))
                return
            path, item = res
            self.cover_src = str(path)
            self.cover_changed = True
            self.update_cover_preview()
            self.cover_row.set_subtitle(_('Trouvée sur Steam : {0}').format(item['name']))
            self.set_appid(item["id"])

        def fail(_e):
            if not self._closed and req == self._cover_req:
                self.cover_row.set_subtitle(_("Recherche impossible hors connexion"))
        run_async(lambda: auto_cover_lookup(name, exe), done, fail)

    def open_cover_search(self):
        query = self.name_row.get_text().strip() or (guess_name(self.exe) if self.exe else "")

        def on_pick(path, item):
            if self._closed:
                return
            self._cover_req += 1
            self.cover_auto_ok = False
            self.cover_searched = True
            self.cover_src = path
            self.cover_changed = True
            self.update_cover_preview()
            self.cover_row.set_subtitle(_('Trouvée sur Steam : {0}').format(item['name']))
            self.set_appid(item["id"])
        CoverSearchDialog(self.win, query, on_pick).present(self.win)

    def detect_steam(self):
        if not self.is_new or not self.exe:
            return
        self._detect_req += 1
        req = self._detect_req
        probe = {"name": self.name_row.get_text().strip(), "exe": self.exe,
                 "steam_appid": self.steam_appid, "gameid": self.gameid_row.get_text().strip()}
        self.import_row.set_subtitle(_("Recherche du jeu dans Steam…"))

        def done(src):
            if self._closed or req != self._detect_req:
                return
            self.steam_source = src if src and src["has_data"] else None
            if src and src["has_data"]:
                self.import_row.set_subtitle(_('Trouvées : {0} ({1})').format(src['label'], SOURCE_KINDS[src['kind']]))
                self.import_row.set_sensitive(True)
            elif src:
                self.import_row.set_subtitle(_('Trouvé dans Steam ({0}), mais sans sauvegarde dans son préfixe').format(src['label']))
                self.import_row.set_sensitive(False)
            else:
                self.import_row.set_subtitle(_("Aucune sauvegarde Steam trouvée pour ce jeu"))
                self.import_row.set_sensitive(False)
        run_async(lambda: find_steam_source(probe), done, lambda _e: done(None))

    def validate(self, *_):
        self.save_btn.set_sensitive(bool(self.name_row.get_text().strip() and self.exe))

    def update_exe_row(self):
        self.exe_row.set_subtitle(short_path(self.exe) if self.exe else _("Aucun fichier choisi"))

    def update_cover_preview(self):
        child = self.cover_slot.get_first_child()
        if child:
            self.cover_slot.remove(child)
        fake = {"name": self.name_row.get_text().strip() or "?", "cover": self.cover_src}
        frame = make_frame(fake, 48, 72)
        frame.remove_css_class("cover-frame")
        frame.add_css_class("card")
        self.cover_slot.append(frame)
        self.clear_cover_btn.set_visible(bool(self.cover_src))

    def collect(self):
        idx = self.proton_row.get_selected()
        proton = self.proton_values[idx] if 0 <= idx < len(self.proton_values) else "auto"
        data = {
            "name": self.name_row.get_text().strip(),
            "exe": self.exe,
            "proton": proton,
            "prefix": self.prefix_row.get_text().strip(),
            "gameid": self.gameid_row.get_text().strip(),
            "args": self.args_row.get_text().strip(),
            "env": self.env_row.get_text().strip(),
            "steam_appid": self.steam_appid,
            "sandbox": self.sandbox_row.get_active(),
        }
        for key, row in self.switches.items():
            data[key] = row.get_active()
        return data

    # sélecteurs de fichiers
    def _exe_folder_hint(self):
        if self.exe and Path(self.exe).parent.is_dir():
            return Path(self.exe).parent
        prefix = self.prefix_row.get_text().strip()
        if prefix:
            for c in (Path(prefix).expanduser() / "drive_c", Path(prefix).expanduser()):
                if c.is_dir():
                    return c
        games = HOME / "Games"
        return games if games.is_dir() else None

    def pick_exe(self):
        dlg = Gtk.FileDialog(title=_("Choisir l’exécutable du jeu"), filters=exe_filters())
        folder = self._exe_folder_hint()
        if folder:
            dlg.set_initial_folder(Gio.File.new_for_path(str(folder)))
        dlg.open(self.win, None, self._on_exe)

    def _on_exe(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        if not f or not f.get_path():
            return
        self.exe = f.get_path()
        self.update_exe_row()
        if not self.name_row.get_text().strip():
            self.name_row.set_text(guess_name(self.exe))
        self.validate()
        self.auto_cover()
        self.detect_steam()

    def pick_installer(self):
        dlg = Gtk.FileDialog(title=_("Choisir l’installeur du jeu"), filters=exe_filters())
        dl = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or HOME)
        if dl.is_dir():
            dlg.set_initial_folder(Gio.File.new_for_path(str(dl)))
        dlg.open(self.win, None, self._on_installer)

    def _on_installer(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        if not f or not f.get_path():
            return
        path = f.get_path()
        if not self.name_row.get_text().strip():
            self.name_row.set_text(guess_name(path, installer=True))
        name = self.name_row.get_text().strip()
        prefix = self.prefix_row.get_text().strip() or default_prefix(name, self.gid, self.store)
        self.prefix_row.set_text(prefix)
        tmp = {**GAME_DEFAULTS, **self.collect(), "id": self.gid, "prefix": prefix}
        try:
            self.win.runner.run_tool(tmp, path, on_exit=self._installer_done, log_suffix="installation")
        except UmuMissing:
            self.win.ask_install_umu()
            return
        except LaunchError as e:
            self.win.toast(str(e))
            return
        self.win.toast(_("Installeur lancé. Une fois l’installation finie, choisis l’exécutable du jeu."))

    def _installer_done(self, _code):
        if self._closed:
            return
        self.win.toast(_("Installation terminée : choisis maintenant l’exécutable du jeu."))
        self.pick_exe()

    def pick_prefix(self):
        dlg = Gtk.FileDialog(title=_("Choisir le dossier du préfixe Wine"))
        PREFIXES_DIR.mkdir(parents=True, exist_ok=True)
        dlg.set_initial_folder(Gio.File.new_for_path(str(PREFIXES_DIR)))
        dlg.select_folder(self.win, None, self._on_prefix)

    def _on_prefix(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        if f and f.get_path():
            self.prefix_row.set_text(f.get_path())

    def pick_cover(self):
        dlg = Gtk.FileDialog(title=_("Choisir une jaquette"),
                             filters=filters(file_filter(_("Images"), pixbuf=True)))
        pics = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pics and Path(pics).is_dir():
            dlg.set_initial_folder(Gio.File.new_for_path(pics))
        dlg.open(self.win, None, self._on_cover)

    def _on_cover(self, dlg, res):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        if not f or not f.get_path():
            return
        if load_texture(f.get_path()) is None:
            self.win.toast(_("Cette image n’a pas pu être lue. Essaie un PNG ou un JPEG."))
            return
        self._cover_req += 1
        self.cover_auto_ok = False
        self.cover_src = f.get_path()
        self.cover_changed = True
        self.update_cover_preview()
        self.cover_row.set_subtitle(COVER_HINT)

    def clear_cover(self):
        self._cover_req += 1
        self.cover_auto_ok = False
        self.cover_src = ""
        self.cover_changed = True
        self.update_cover_preview()
        self.cover_row.set_subtitle(COVER_HINT)

    # enregistrement
    def on_save(self):
        data = self.collect()
        if not data["name"] or not data["exe"]:
            return
        try:
            shlex.split(data["args"])
            shlex.split(data["env"])
        except ValueError:
            self.win.toast(_("Des guillemets sont mal fermés dans les arguments ou les variables."))
            return
        if not data["prefix"]:
            data["prefix"] = default_prefix(data["name"], self.gid, self.store)
        if data["sandbox"] and not sandbox_ok(data["exe"]):
            self.win.toast(_('Impossible d’isoler ce jeu : il faudrait partager {0}. Range-le dans son propre dossier, ou désactive l’isolation.').format(unsafe_share(game_root(data['exe']))), timeout=8)
            return
        base = self.store.get(self.gid) or {**GAME_DEFAULTS, "id": self.gid, "added": int(time.time())}
        old_cover = base.get("cover", "")
        if self.cover_changed:
            try:
                data["cover"] = store_cover(self.gid, self.cover_src) if self.cover_src else ""
            except OSError as e:
                self.win.toast(_('Jaquette non copiée : {0}').format(e.strerror))
                data["cover"] = old_cover
            if old_cover and old_cover != data["cover"]:
                delete_cover(old_cover)
        if self.cover_searched or data.get("cover", base.get("cover")):
            data["cover_auto"] = True
        game = {**base, **data}
        self.store.upsert(game)
        if shortcut_path(self.gid).exists():
            write_shortcut(game)
        if self.is_new and self.import_row.get_active():
            self.win.auto_import(game, self.steam_source, quiet=True)
        self.win.refresh()
        self.win.toast(_('« {0} » ajouté à la bibliothèque').format(game['name']) if self.is_new
                       else _("Modifications enregistrées"))
        self.close()


# ────────────────────────────────────────────────────── page d'un jeu ──

class DetailPage(Adw.NavigationPage):
    def __init__(self, win, gid):
        super().__init__(title="", tag=f"jeu-{gid}")
        self.win = win
        self.gid = gid
        header = Adw.HeaderBar()
        self.menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=_("Plus d’options"))
        header.pack_end(self.menu_btn)
        tv = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        tv.add_top_bar(header)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        tv.set_content(self.scroller)
        self.set_child(tv)
        self.refresh()

    def _menu(self, game):
        menu = Gio.Menu()
        has_shortcut = shortcut_path(self.gid).exists()
        for label, action in (
            (_("Modifier…"), "win.edit-game"),
            (_("Chercher une jaquette en ligne…"), "win.search-cover"),
            (_("Retirer du menu des applications") if has_shortcut else _("Ajouter au menu des applications"),
             "win.shortcut-game"),
            (_("Supprimer de la bibliothèque…"), "win.delete-game"),
        ):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, GLib.Variant.new_string(self.gid))
            menu.append_item(item)
        return menu

    def refresh(self):
        game = self.win.store.get(self.gid)
        if not game:
            return
        self.set_title(game["name"])
        self.menu_btn.set_menu_model(self._menu(game))
        running = self.win.runner.is_running(self.gid)
        scroll = self.scroller.get_vadjustment().get_value()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # En-tête : la jaquette floutée sert de décor.
        hero = Gtk.Overlay()
        hero.set_overflow(Gtk.Overflow.HIDDEN)
        backdrop = cover_widget(game, show_initial=False)
        backdrop.add_css_class("backdrop")
        hero.set_child(backdrop)
        hero.add_overlay(Gtk.Box(css_classes=["backdrop-fade"]))

        clamp = Adw.Clamp(maximum_size=1060, tightening_threshold=900)
        top = Gtk.Box(spacing=36, margin_start=36, margin_end=36, margin_top=24, margin_bottom=28)
        frame = make_frame(game, 240, 360, "detail-cover")
        if not load_texture(game.get("cover")):
            find = make_button(_("Trouver une jaquette"), "system-search-symbolic", classes=("pill",))
            find.set_halign(Gtk.Align.CENTER)
            find.set_valign(Gtk.Align.END)
            find.set_margin_bottom(18)
            find.set_action_name("win.search-cover")
            find.set_action_target_value(GLib.Variant.new_string(self.gid))
            frame.add_overlay(find)
        top.append(frame)
        if running:
            top.get_first_child().add_css_class("running")

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.END, hexpand=True)
        if running:
            info.append(Gtk.Label(label=_("En cours"), halign=Gtk.Align.START, css_classes=["badge-running"]))
        info.append(Gtk.Label(label=game["name"], xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              css_classes=["detail-title"]))
        chips = Gtk.Box(spacing=8)
        chips.append(Gtk.Label(label=proton_label(game.get("proton")), css_classes=["chip"]))
        for key, label in (("sandbox", _("Isolé")), ("mangohud", "MangoHud"), ("gamemode", "GameMode"),
                           ("fsr", "FSR"), ("wayland", "Wayland")):
            if game.get(key):
                chips.append(Gtk.Label(label=label, css_classes=["chip"]))
        info.append(chips)

        stats = Gtk.Box(spacing=10)
        stats.append(stat_tile(_("Temps de jeu"), fmt_duration(game.get("playtime"))))
        stats.append(stat_tile(_("Dernière session"), fmt_last(game.get("last_played"))))
        if game.get("added"):
            stats.append(stat_tile(_("Ajouté le"), fmt_date(game["added"])))
        info.append(stats)

        actions = Gtk.Box(spacing=10, margin_top=6)
        play = make_button(_("Arrêter") if running else _("Jouer"),
                           "media-playback-stop-symbolic" if running else "media-playback-start-symbolic",
                           cb=lambda: self.win.toggle_play(self.gid), classes=("pill", "play-button"))
        play.add_css_class("running" if running else "suggested-action")
        actions.append(play)
        edit = make_button(icon="document-edit-symbolic", classes=("circular",), tooltip=_("Modifier"))
        edit.set_action_name("win.edit-game")
        edit.set_action_target_value(GLib.Variant.new_string(self.gid))
        actions.append(edit)
        cover_btn = make_button(icon="image-x-generic-symbolic", classes=("circular",),
                                tooltip=_("Chercher une jaquette en ligne"))
        cover_btn.set_action_name("win.search-cover")
        cover_btn.set_action_target_value(GLib.Variant.new_string(self.gid))
        actions.append(cover_btn)
        trash = make_button(icon="user-trash-symbolic", classes=("circular",), tooltip=_("Supprimer de la bibliothèque"))
        trash.set_action_name("win.delete-game")
        trash.set_action_target_value(GLib.Variant.new_string(self.gid))
        actions.append(trash)
        info.append(actions)
        top.append(info)
        clamp.set_child(top)
        hero.add_overlay(clamp)
        hero.set_measure_overlay(clamp, True)
        root.append(hero)

        # Outils
        body = Adw.Clamp(maximum_size=1060, tightening_threshold=900)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                      margin_start=36, margin_end=36, margin_top=8, margin_bottom=40)
        tools = Adw.PreferencesGroup(title=_("Outils"), description=_("Tout s’exécute dans le préfixe Wine de ce jeu."))

        def row(title, subtitle, icon, button):
            r = Adw.ActionRow(title=title, subtitle=subtitle or "", use_markup=False, subtitle_lines=2)
            r.add_prefix(Gtk.Image.new_from_icon_name(icon))
            r.add_suffix(button)
            r.set_activatable_widget(button)
            return r

        tools.add(row(_("Configuration de Wine"), _("Version de Windows, lecteurs, audio…"),
                      "preferences-system-symbolic",
                      make_button(_("Ouvrir"), cb=lambda: self.win.run_tool(self.gid, "winecfg"))))
        wt = Adw.EntryRow(title=_("Winetricks : composants à installer (ex. vcrun2022 d3dx9)"),
                          show_apply_button=True)
        wt.connect("apply", lambda r: self.win.run_winetricks(self.gid, r.get_text()))
        tools.add(wt)
        tools.add(row(_("Lancer un autre exécutable"), _("Installeur, launcher, outil de mods…"),
                      "application-x-executable-symbolic",
                      make_button(_("Choisir…"), cb=lambda: self.win.pick_and_run(self.gid))))
        if game.get("sandbox"):
            tools.add(row(_("Vérifier l’isolation"),
                          _('Le jeu ne voit que {0} et son préfixe. Ce test le confirme sur ta machine.').format(short_path(game_root(game['exe']))),
                          "security-high-symbolic",
                          make_button(_("Tester"), cb=lambda: self.win.test_isolation(self.gid))))
        box.append(tools)

        saves = Adw.PreferencesGroup(title=_("Sauvegardes"))
        saves.add(row(_("Importer depuis Steam"),
                      _("Récupère les sauvegardes d’un jeu Steam ou d’un raccourci non-Steam. "
                      "Celles du jeu sont d’abord mises de côté."),
                      "document-save-symbolic",
                      make_button(_("Importer…"), cb=lambda: self.win.import_from_steam(self.gid))))
        saves.add(row(_("Exporter les sauvegardes"), _("Dans une archive .zip, pour les garder à l’abri ou "
                      "les emporter sur un autre PC"), "document-send-symbolic",
                      make_button(_("Exporter…"), cb=lambda: self.win.export_saves(self.gid))))
        saves.add(row(_("Importer une archive de sauvegardes"), _("Une archive exportée par Lanterne. Les "
                      "sauvegardes actuelles sont d’abord mises de côté."), "document-open-symbolic",
                      make_button(_("Importer…"), cb=lambda: self.win.import_save_archive(self.gid))))
        has_backups = BACKUPS_DIR.is_dir() and any(BACKUPS_DIR.glob(f"{self.gid}-*"))
        b = make_button(icon="folder-open-symbolic", classes=("flat",), tooltip=_("Ouvrir"),
                        cb=lambda: open_path(BACKUPS_DIR))
        b.set_sensitive(has_backups)
        saves.add(row(_("Sauvegardes mises de côté"),
                      _("Copies faites avant chaque import") if has_backups else _("Aucune pour l’instant"),
                      "folder-symbolic", b))
        box.append(saves)

        files = Adw.PreferencesGroup(title=_("Fichiers"))
        exe_dir = Path(game["exe"]).parent if game.get("exe") else None
        b = make_button(icon="folder-open-symbolic", classes=("flat",), tooltip=_("Ouvrir"),
                        cb=lambda: open_path(exe_dir))
        b.set_sensitive(bool(exe_dir and exe_dir.is_dir()))
        files.add(row(_("Dossier du jeu"), short_path(exe_dir) if exe_dir else "", "folder-symbolic", b))
        prefix = Path(game["prefix"]).expanduser() if game.get("prefix") else None
        b = make_button(icon="folder-open-symbolic", classes=("flat",), tooltip=_("Ouvrir"),
                        cb=lambda: open_path(prefix))
        b.set_sensitive(bool(prefix and prefix.is_dir()))
        files.add(row(_("Préfixe Wine"), short_path(prefix) if prefix else "", "drive-harddisk-symbolic", b))
        log = LOGS_DIR / f"{self.gid}.log"
        b = make_button(icon="document-open-symbolic", classes=("flat",), tooltip=_("Ouvrir"),
                        cb=lambda: open_path(log))
        b.set_sensitive(log.is_file())
        files.add(row(_("Journal du dernier lancement"),
                      _("Utile si le jeu ne démarre pas") if log.is_file() else _("Pas encore de journal"),
                      "text-x-generic-symbolic", b))
        box.append(files)
        body.set_child(box)
        root.append(body)

        self.scroller.set_child(root)
        GLib.idle_add(lambda: self.scroller.get_vadjustment().set_value(scroll) or False)


# ──────────────────────────────────────────────────────── bibliothèque ──

class LibraryView(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.query = ""
        self._ids = []

        self.banner = Adw.Banner(button_label=_("Installer"))
        self.banner.connect("button-clicked", self._on_banner)
        self.append(self.banner)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.append(self.stack)

        empty = Adw.StatusPage(icon_name=APP_ID, title=_("Ta bibliothèque est vide"),
                               description=_("Ajoute un jeu Windows (.exe). Lanterne s’occupe de Proton."))
        add = make_button(_("Ajouter un jeu"), classes=("pill", "suggested-action"))
        add.set_halign(Gtk.Align.CENTER)
        add.set_action_name("win.add-game")
        empty.set_child(add)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(Adw.StatusPage(icon_name="system-search-symbolic", title=_("Aucun jeu trouvé"),
                                            description=_("Essaie un autre nom.")), "noresult")

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=1600, tightening_threshold=1400)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=26,
                          margin_start=32, margin_end=32, margin_top=24, margin_bottom=40)
        self.hero_slot = Gtk.Box()
        content.append(self.hero_slot)

        head = Gtk.Box(spacing=12)
        head.append(Gtk.Label(label=_("Bibliothèque"), css_classes=["lib-title"]))
        self.count = Gtk.Label(css_classes=["dim-label"], margin_top=6)
        head.append(self.count)
        head.append(Gtk.Box(hexpand=True))
        self.sort = Gtk.DropDown.new_from_strings([_("Joués récemment"), _("Nom"), _("Temps de jeu")])
        self.sort.set_selected(int(win.store.settings.get("sort", 0)))
        self.sort.set_valign(Gtk.Align.CENTER)
        self.sort.connect("notify::selected", self._on_sort)
        head.append(self.sort)
        content.append(head)

        self.flow = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                                activate_on_single_click=True, column_spacing=22, row_spacing=28,
                                min_children_per_line=1, max_children_per_line=16,
                                valign=Gtk.Align.START, css_classes=["library"])
        self.flow.connect("child-activated", self._on_activated)
        content.append(self.flow)
        clamp.set_child(content)
        scroller.set_child(clamp)
        self.stack.add_named(scroller, "grid")

    def _on_sort(self, *_):
        self.win.store.settings["sort"] = int(self.sort.get_selected())
        self.win.store.save()
        self.refresh()

    def _on_activated(self, _flow, child):
        idx = child.get_index()
        if 0 <= idx < len(self._ids):
            self.win.open_detail(self._ids[idx])

    def _on_banner(self, *_):
        tag = self.win.proton_page.latest_tag()
        self.win.stack.set_visible_child_name("proton")
        if tag:
            self.win.proton_page.install_tag(tag)

    def update_banner(self):
        pp = getattr(self.win, "proton_page", None)
        latest = pp.latest_tag() if pp else None
        s = self.win.store
        uses_fixed = (s.settings.get("default_proton", "auto") != "auto"
                      or any(g.get("proton", "auto") != "auto" for g in s.games))
        show = bool(latest) and uses_fixed and not (pp and pp.installing) \
            and latest not in {n for n, _ in scan_protons()}
        if show:
            self.banner.set_title(_('{0}, la nouvelle version de GE-Proton, est disponible').format(latest))
        self.banner.set_revealed(show)

    def build_card(self, game):
        gid = game["id"]
        running = self.win.runner.is_running(gid)
        card = Gtk.Overlay(halign=Gtk.Align.CENTER, valign=Gtk.Align.START, css_classes=["game-card"])
        card.set_overflow(Gtk.Overflow.HIDDEN)
        if running:
            card.add_css_class("running")
        card.set_child(cover_widget(game, CARD_W, CARD_H))

        cap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.END,
                      css_classes=["card-caption"])
        cap.append(Gtk.Label(label=game["name"], xalign=0, wrap=True, lines=2, max_width_chars=1,
                             ellipsize=Pango.EllipsizeMode.END, hexpand=True, css_classes=["card-title"]))
        if running:
            sub = _("En cours")
        elif game.get("playtime"):
            sub = fmt_duration(game["playtime"])
        else:
            sub = _("Jamais lancé")
        cap.append(Gtk.Label(label=sub, xalign=0, css_classes=["card-sub"]))
        card.add_overlay(cap)

        play = make_button(icon="media-playback-stop-symbolic" if running else "media-playback-start-symbolic",
                           cb=lambda: self.win.toggle_play(gid), classes=("card-play",),
                           tooltip=_("Arrêter") if running else _("Jouer"))
        play.set_halign(Gtk.Align.CENTER)
        if running:
            play.add_css_class("running")
        card.add_overlay(play)

        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda _g, _n, x, y: self._context_menu(card, gid, x, y))
        card.add_controller(right)
        hold = Gtk.GestureLongPress()
        hold.connect("pressed", lambda _g, x, y: self._context_menu(card, gid, x, y))
        card.add_controller(hold)

        child = Gtk.FlowBoxChild()
        child.set_child(card)
        return child

    def _context_menu(self, widget, gid, x, y):
        menu = Gio.Menu()
        running = self.win.runner.is_running(gid)
        for label, action in ((_("Arrêter") if running else _("Jouer"), "win.play-game"),
                              (_("Modifier…"), "win.edit-game"),
                              (_("Chercher une jaquette en ligne…"), "win.search-cover"),
                              (_("Supprimer…"), "win.delete-game")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, GLib.Variant.new_string(gid))
            menu.append_item(item)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(widget)
        pop.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)

        def cleanup(p):
            GLib.idle_add(lambda: (p.unparent() if p.get_parent() else None) or False)
        pop.connect("closed", cleanup)
        pop.popup()

    def build_hero(self, game):
        gid = game["id"]
        box = Gtk.Box(spacing=24, css_classes=["hero"], hexpand=True)
        box.append(make_frame(game, 104, 156))
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, hexpand=True)
        running = self.win.runner.is_running(gid)
        info.append(Gtk.Label(label=_("En cours") if running else _("Reprendre"), xalign=0,
                              css_classes=["hero-kicker"]))
        info.append(Gtk.Label(label=game["name"], xalign=0, wrap=True, css_classes=["hero-title"]))
        info.append(Gtk.Label(label=_('{0} de jeu, dernière session : {1}').format(fmt_duration(game.get('playtime')), fmt_last(game.get('last_played')).lower()),
                              xalign=0, wrap=True, css_classes=["dim-label"]))
        buttons = Gtk.Box(spacing=10, margin_top=10)
        play = make_button(_("Arrêter") if running else _("Jouer"),
                           "media-playback-stop-symbolic" if running else "media-playback-start-symbolic",
                           cb=lambda: self.win.toggle_play(gid), classes=("pill", "play-button"))
        play.add_css_class("running" if running else "suggested-action")
        buttons.append(play)
        buttons.append(make_button(_("Détails"), cb=lambda: self.win.open_detail(gid), classes=("pill", "flat")))
        info.append(buttons)
        box.append(info)
        return box

    def refresh(self):
        store = self.win.store
        games = store.games
        self.update_banner()
        if not games:
            self.stack.set_visible_child_name("empty")
            return
        q = self.query.strip().lower()
        shown = [g for g in games if q in g["name"].lower()] if q else list(games)
        mode = int(store.settings.get("sort", 0))
        if mode == 1:
            shown.sort(key=lambda g: natural_key(g["name"]))
        elif mode == 2:
            shown.sort(key=lambda g: -int(g.get("playtime") or 0))
        else:
            shown.sort(key=lambda g: -int(g.get("last_played") or g.get("added") or 0))

        child = self.hero_slot.get_first_child()
        if child:
            self.hero_slot.remove(child)
        recent = max(games, key=lambda g: int(g.get("last_played") or 0))
        if not q and recent.get("last_played"):
            self.hero_slot.append(self.build_hero(recent))
            self.hero_slot.set_visible(True)
        else:
            self.hero_slot.set_visible(False)

        n = len(shown)
        self.count.set_label(_n("{n} jeu", "{n} jeux", n).format(n=n))
        while (c := self.flow.get_first_child()) is not None:
            self.flow.remove(c)
        self._ids = []
        for g in shown:
            self.flow.append(self.build_card(g))
            self._ids.append(g["id"])
        self.stack.set_visible_child_name("grid" if shown else "noresult")


# ───────────────────────────────────────────────────────── page Proton ──

class ProtonPage(Adw.PreferencesPage):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.releases = None
        self.fetching = False
        self.fetch_error = None
        self.installing = None
        self.progress = (0.0, "")
        self.pbar = None
        self._pulse_id = 0
        self.umu_busy = False
        self._updating = False
        self.default_values = []
        self._rows = {"inst": [], "rel": []}

        g = Adw.PreferencesGroup(title=_("Moteur"),
                                 description=_("umu-launcher fait tourner Proton hors de Steam, "
                                             "dans le même environnement d’exécution."))
        self.umu_row = Adw.ActionRow(title="umu-launcher", use_markup=False)
        self.umu_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.umu_btn = make_button(cb=self.install_umu)
        self.umu_row.add_suffix(self.umu_spinner)
        self.umu_row.add_suffix(self.umu_btn)
        g.add(self.umu_row)
        self.add(g)

        g2 = Adw.PreferencesGroup(title=_("Par défaut"))
        self.default_row = Adw.ComboRow(title=_("Proton pour les nouveaux jeux"),
                                        subtitle=_("Le mode auto utilise toujours la dernière GE-Proton"))
        self.default_row.connect("notify::selected", self._on_default_changed)
        g2.add(self.default_row)
        self.add(g2)

        self.inst_group = Adw.PreferencesGroup(title=_("Versions installées"))
        self.add(self.inst_group)

        self.rel_group = Adw.PreferencesGroup(
            title=_("GE-Proton à télécharger"),
            description=_("Builds communautaires de GloriousEggroll, avec des correctifs de jeux en plus."))
        self.rel_group.set_header_suffix(make_button(icon="view-refresh-symbolic", cb=self.fetch_releases,
                                                     classes=("flat",), tooltip=_("Actualiser")))
        self.add(self.rel_group)

        self.refresh_local()
        self.fetch_releases()

    # utilitaires
    def _clear(self, group, key):
        for r in self._rows[key]:
            group.remove(r)
        self._rows[key].clear()

    def _add(self, group, key, row):
        group.add(row)
        self._rows[key].append(row)

    def latest_tag(self):
        return self.releases[0]["tag_name"] if self.releases else None

    # état local
    def refresh_local(self):
        system = shutil.which("umu-run")
        loc = local_umu()
        self.umu_btn.remove_css_class("suggested-action")
        if system:
            self.umu_row.set_subtitle(_('Fourni par le système : {0}').format(system))
            self.umu_btn.set_visible(False)
        elif loc:
            ver_file = UMU_DIR / "VERSION"
            ver = ver_file.read_text(encoding="utf-8").strip() if ver_file.is_file() else ""
            self.umu_row.set_subtitle(_('Version {0} installée par Lanterne').format(ver) if ver else _("Installé par Lanterne"))
            self.umu_btn.set_label(_("Mettre à jour"))
            self.umu_btn.set_visible(True)
        else:
            self.umu_row.set_subtitle(_("Non installé, nécessaire pour lancer tes jeux"))
            self.umu_btn.set_label(_("Installer"))
            self.umu_btn.add_css_class("suggested-action")
            self.umu_btn.set_visible(True)
        self.umu_btn.set_sensitive(not self.umu_busy)
        self.umu_spinner.set_visible(self.umu_busy)
        self.umu_spinner.set_spinning(self.umu_busy)

        current = self.win.store.settings.get("default_proton", "auto")
        values, labels = proton_options(current)
        self._updating = True
        self.default_values = values
        self.default_row.set_model(Gtk.StringList.new(labels))
        self.default_row.set_selected(values.index(current) if current in values else 0)
        self._updating = False

        self._clear(self.inst_group, "inst")
        protons = scan_protons()
        if not protons:
            self._add(self.inst_group, "inst", Adw.ActionRow(
                title=_("Aucune version installée"),
                subtitle=_("Installe une GE-Proton ci-dessous, ou laisse le mode auto la télécharger "
                         "au premier lancement.")))
        games = self.win.store.games
        for name, path in protons:
            n = sum(1 for g in games if g.get("proton") == str(path))
            sub = short_path(path.parent)
            if n:
                sub = _n("Utilisée par {n} jeu, dans {dir}", "Utilisée par {n} jeux, dans {dir}", n).format(
                    n=n, dir=sub)
            r = Adw.ActionRow(title=name, subtitle=sub, use_markup=False)
            if current == str(path):
                r.add_suffix(tag_label(_("Par défaut"), accent=True))
            trash = make_button(icon="user-trash-symbolic", classes=("flat",), tooltip=_("Supprimer"),
                                cb=lambda nm=name, p=path: self.confirm_delete(nm, p))
            trash.set_sensitive(os.access(path.parent, os.W_OK))
            r.add_suffix(trash)
            self._add(self.inst_group, "inst", r)

    def _on_default_changed(self, *_):
        if self._updating:
            return
        idx = self.default_row.get_selected()
        if 0 <= idx < len(self.default_values):
            self.win.store.settings["default_proton"] = self.default_values[idx]
            self.win.store.save()
            GLib.idle_add(lambda: self.win.refresh() or False)

    def confirm_delete(self, name, path):
        users = [g for g in self.win.store.games if g.get("proton") == str(path)]
        body = _("Ses fichiers seront effacés du disque.")
        if users:
            body += " " + _n("Le jeu qui l’utilise passera en mode auto.",
                             "Les {n} jeux qui l’utilisent passeront en mode auto.", len(users)).format(n=len(users))
        d = Adw.AlertDialog(heading=f"Supprimer {name} ?", body=body)
        d.add_response("cancel", _("Annuler"))
        d.add_response("delete", _("Supprimer"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def on_response(_d, resp):
            if resp != "delete":
                return

            def done(_res):
                for g in self.win.store.games:
                    if g.get("proton") == str(path):
                        g["proton"] = "auto"
                if self.win.store.settings.get("default_proton") == str(path):
                    self.win.store.settings["default_proton"] = "auto"
                self.win.store.save()
                self.win.refresh()
                self.refresh_releases()
                self.win.toast(_('{0} supprimée').format(name))

            run_async(lambda: shutil.rmtree(path), done,
                      lambda e: self.win.toast(_('Suppression impossible : {0}').format(e)))
        d.connect("response", on_response)
        d.present(self.win)

    # umu
    def install_umu(self):
        if self.umu_busy:
            return
        self.umu_busy = True
        self.refresh_local()

        def done(tag):
            self.umu_busy = False
            self.refresh_local()
            self.win.toast(_('umu-launcher {0} est installé').format(tag) if tag else _("umu-launcher est installé"))

        def fail(e):
            self.umu_busy = False
            self.refresh_local()
            self.win.toast(_('Installation d’umu impossible : {0}').format(nice_error(e)))
        run_async(install_umu_files, done, fail)

    # versions en ligne
    def fetch_releases(self):
        if self.fetching:
            return
        self.fetching = True
        self.fetch_error = None
        self.refresh_releases()
        run_async(lambda: http_json(GE_API), self._on_releases, self._on_releases_error)

    def _on_releases(self, data):
        self.fetching = False
        self.releases = [r for r in data if not r.get("draft") and not r.get("prerelease")
                         and any(a["name"].endswith(".tar.gz") for a in r.get("assets", []))]
        self.refresh_releases()
        self.win.library.update_banner()

    def _on_releases_error(self, e):
        self.fetching = False
        self.fetch_error = nice_error(e)
        self.refresh_releases()

    def refresh_releases(self):
        self._clear(self.rel_group, "rel")
        self.pbar = None
        if self.fetching and not self.releases:
            r = Adw.ActionRow(title=_("Recherche des dernières versions…"))
            r.add_suffix(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER))
            self._add(self.rel_group, "rel", r)
            return
        if not self.releases:
            r = Adw.ActionRow(title=_("Liste indisponible"), subtitle=self.fetch_error or "", use_markup=False)
            r.add_suffix(make_button(_("Réessayer"), cb=self.fetch_releases))
            self._add(self.rel_group, "rel", r)
            return
        installed = {n for n, _ in scan_protons()}
        for i, rel in enumerate(self.releases):
            tag = rel["tag_name"]
            asset = next(a for a in rel["assets"] if a["name"].endswith(".tar.gz"))
            r = Adw.ActionRow(title=tag, use_markup=False,
                              subtitle=_('Publiée le {0}, {1:.0f} Mo').format(fmt_iso(rel.get('published_at')), asset.get('size', 0) / 1e6))
            if i == 0:
                r.add_suffix(tag_label(_("Dernière")))
            if tag in installed:
                ok = Gtk.Image.new_from_icon_name("object-select-symbolic")
                ok.add_css_class("success")
                ok.set_tooltip_text(_("Installée"))
                r.add_suffix(ok)
            elif self.installing == tag:
                self.pbar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, show_text=True, width_request=170)
                frac, text = self.progress
                if frac is not None:
                    self.pbar.set_fraction(frac)
                self.pbar.set_text(text)
                r.add_suffix(self.pbar)
            else:
                b = make_button(_("Installer"), cb=lambda rl=rel: self.install_release(rl),
                                classes=("suggested-action",) if i == 0 else ())
                b.set_sensitive(self.installing is None)
                r.add_suffix(b)
            self._add(self.rel_group, "rel", r)

    def install_tag(self, tag):
        rel = next((r for r in (self.releases or []) if r["tag_name"] == tag), None)
        if rel:
            self.install_release(rel)

    def install_release(self, rel):
        if self.installing:
            return
        self.installing = rel["tag_name"]
        self.progress = (0.0, _("Préparation…"))
        self.refresh_releases()
        self.win.library.update_banner()

        def progress(frac, text):
            GLib.idle_add(self._set_progress, frac, text)
        run_async(lambda: install_ge(rel, progress), self._on_installed, self._on_install_error)

    def _set_progress(self, frac, text):
        self.progress = (frac, text)
        if self.pbar:
            if frac is not None:
                self.pbar.set_fraction(frac)
            self.pbar.set_text(text)
        if frac is None and not self._pulse_id:
            self._pulse_id = GLib.timeout_add(120, self._pulse)
        return False

    def _pulse(self):
        if self.installing and self.progress[0] is None:
            if self.pbar:
                self.pbar.pulse()
            return True
        self._pulse_id = 0
        return False

    def _on_installed(self, path):
        tag = self.installing
        self.installing = None
        self.refresh_releases()
        self.win.refresh()
        toast = Adw.Toast(title=_('{0} est installée').format(tag), use_markup=False, timeout=10)
        toast.set_button_label(_("Utiliser pour tous les jeux"))
        toast.set_action_name("win.use-proton-everywhere")
        toast.set_action_target_value(GLib.Variant.new_string(str(path)))
        self.win.toasts.add_toast(toast)

    def _on_install_error(self, e):
        self.installing = None
        self.refresh_releases()
        self.win.library.update_banner()
        self.win.toast(_('Installation impossible : {0}').format(nice_error(e)))


# ─────────────────────────────────────────────────── fenêtre principale ──

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Lanterne")
        self.app = app
        self.store = app.store
        self.runner = app.runner
        self.detail = None
        w, h = (self.store.settings.get("window") or SETTINGS_DEFAULTS["window"])[:2]
        self.set_default_size(max(int(w), 720), max(int(h), 560))

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        self.nav = Adw.NavigationView()
        self.nav.connect("popped", self._on_popped)
        self.nav.connect("notify::visible-page", self._on_visible_page)
        self.toasts.set_child(self.nav)

        self.stack = Adw.ViewStack()
        self.library = LibraryView(self)
        self.proton_page = ProtonPage(self)
        self.stack.add_titled_with_icon(self.library, "library", _("Bibliothèque"), "view-grid-symbolic")
        self.stack.add_titled_with_icon(self.proton_page, "proton", "Proton", "emblem-system-symbolic")

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("Ajouter un jeu (Ctrl+N)"))
        add_btn.set_action_name("win.add-game")
        header.pack_start(add_btn)
        menu = Gio.Menu()
        menu.append(_("Importer mes jeux depuis Steam…"), "win.import-steam")
        menu.append(_("Chercher les jaquettes manquantes"), "win.find-missing-covers")
        menu.append(_("Isoler tous mes jeux"), "win.sandbox-all")
        menu.append(_("Ouvrir le dossier des préfixes"), "win.open-prefixes")
        menu.append(_("Ouvrir le dossier des journaux"), "win.open-logs")
        menu.append("Switch to English" if LANG == "fr" else "Passer en français", "win.switch-language")
        menu.append(_("À propos de Lanterne"), "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       primary=True, tooltip_text=_("Menu principal")))
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=_("Rechercher (Ctrl+F)"))
        header.pack_end(self.search_btn)

        self.searchbar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Rechercher un jeu…"), hexpand=True)
        sclamp = Adw.Clamp(maximum_size=520)
        sclamp.set_child(self.search_entry)
        self.searchbar.set_child(sclamp)
        self.searchbar.connect_entry(self.search_entry)
        self.searchbar.set_key_capture_widget(self)
        self.search_btn.bind_property("active", self.searchbar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL)
        self.searchbar.connect("notify::search-mode-enabled", self._on_search_mode)
        self.search_entry.connect("search-changed", self._on_search)

        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.add_top_bar(self.searchbar)
        tv.set_content(self.stack)
        self.main_page = Adw.NavigationPage(title="Lanterne", tag="main", child=tv)
        self.nav.add(self.main_page)

        self._setup_actions()
        self._handlers = [self.runner.connect("changed", lambda *_: self.refresh()),
                          self.runner.connect("finished", self._on_finished)]
        self.connect("close-request", self._on_close)
        self.refresh()
        self._cover_queue = []
        self._cover_report = None  # compteurs quand la recherche est lancée à la main
        GLib.timeout_add_seconds(2, self.auto_covers)

    # actions
    def _setup_actions(self):
        def add(name, cb, param=False):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s") if param else None)
            a.connect("activate", cb)
            self.add_action(a)

        add("add-game", lambda *_: GameDialog(self).present(self))
        add("edit-game", lambda _a, p: self.edit_game(p.get_string()), True)
        add("delete-game", lambda _a, p: self.delete_game(p.get_string()), True)
        add("shortcut-game", lambda _a, p: self.toggle_shortcut(p.get_string()), True)
        add("open-log", lambda _a, p: open_path(LOGS_DIR / f"{p.get_string()}.log"), True)
        add("search-cover", lambda _a, p: self.search_cover(p.get_string()), True)
        add("play-game", lambda _a, p: self.toggle_play(p.get_string()), True)
        add("import-steam", lambda *_: self.import_steam_library())
        add("sandbox-all", lambda *_: self.sandbox_all())
        add("find-missing-covers", lambda *_: self.find_missing_covers())
        add("switch-language", lambda *_: self.switch_language())
        add("use-proton-everywhere", lambda _a, p: self.use_proton_everywhere(p.get_string()), True)
        add("open-prefixes", lambda *_: (PREFIXES_DIR.mkdir(parents=True, exist_ok=True), open_path(PREFIXES_DIR)))
        add("open-logs", lambda *_: (LOGS_DIR.mkdir(parents=True, exist_ok=True), open_path(LOGS_DIR)))
        add("search", lambda *_: self.search_btn.set_active(not self.search_btn.get_active()))

    def toast(self, text, timeout=4):
        self.toasts.add_toast(Adw.Toast(title=text, use_markup=False, timeout=timeout))

    def refresh(self, *_):
        self.library.refresh()
        self.proton_page.refresh_local()
        if self.detail:
            self.detail.refresh()

    # navigation et recherche
    def open_detail(self, gid):
        if self.detail:
            self.nav.pop_to_tag("main")
        self.detail = DetailPage(self, gid)
        self.nav.push(self.detail)

    def _on_popped(self, _nav, page):
        if page is self.detail:
            self.detail = None

    def _on_visible_page(self, *_):
        self.searchbar.set_key_capture_widget(self if self.nav.get_visible_page() is self.main_page else None)

    def _on_search_mode(self, *_):
        if self.searchbar.get_search_mode():
            self.stack.set_visible_child_name("library")
        else:
            self.search_entry.set_text("")

    def _on_search(self, entry):
        self.library.query = entry.get_text()
        self.library.refresh()

    # jeux
    def toggle_play(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        if self.runner.is_running(gid):
            self.runner.stop(gid)
            self.toast(_('Fermeture de « {0} »…').format(game['name']))
            return
        try:
            hint = self.runner.launch(game)
        except UmuMissing:
            self.ask_install_umu()
        except LaunchError as e:
            self.toast(str(e), timeout=6)
        else:
            self.toast(hint or _('Lancement de « {0} »…').format(game['name']), timeout=6 if hint else 3)

    def _on_finished(self, _runner, gid, _code, duration):
        game = self.store.get(gid)
        name = game["name"] if game else _("Le jeu")
        if duration < 15:
            t = Adw.Toast(title=_('« {0} » s’est fermé au bout de {1} s').format(name, int(duration)),
                          use_markup=False, timeout=10)
            t.set_button_label(_("Voir le journal"))
            t.set_action_name("win.open-log")
            t.set_action_target_value(GLib.Variant.new_string(gid))
            self.toasts.add_toast(t)
        else:
            self.toast(_('Session terminée : {0} sur « {1} »').format(fmt_duration(duration), name))

    def edit_game(self, gid):
        game = self.store.get(gid)
        if game:
            GameDialog(self, game).present(self)

    def delete_game(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        d = Adw.AlertDialog(heading=_('Supprimer « {0} » ?').format(game['name']),
                            body=_("Le jeu sera retiré de Lanterne. Ses fichiers d’installation ne sont pas touchés."))
        prefix = Path(game["prefix"]).expanduser() if game.get("prefix") else None
        check = None
        if prefix and prefix.is_dir() and is_inside(prefix, PREFIXES_DIR):
            check = Gtk.CheckButton(label=_("Supprimer aussi son préfixe Wine : sauvegardes, réglages "
                                          "et le jeu lui-même s’il y est installé"))
            d.set_extra_child(check)
        d.add_response("cancel", _("Annuler"))
        d.add_response("delete", _("Supprimer"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def on_response(_d, resp):
            if resp != "delete":
                return
            if self.runner.is_running(gid):
                self.runner.stop(gid)
            self.store.remove(gid)
            delete_cover(game.get("cover"))
            remove_shortcut(gid)
            if check and check.get_active():
                run_async(lambda: shutil.rmtree(prefix, ignore_errors=True))
            if self.detail and self.detail.gid == gid:
                self.nav.pop_to_tag("main")
            self.refresh()
            self.toast(_('« {0} » supprimé').format(game['name']))
        d.connect("response", on_response)
        d.present(self)

    def toggle_shortcut(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        if shortcut_path(gid).exists():
            remove_shortcut(gid)
            self.toast(_("Retiré du menu des applications"))
        else:
            try:
                write_shortcut(game)
            except OSError as e:
                self.toast(_('Raccourci impossible : {0}').format(e.strerror))
                return
            self.toast(_('« {0} » est maintenant dans le menu des applications').format(game['name']))
        if self.detail:
            self.detail.refresh()

    def use_proton_everywhere(self, path):
        if not (Path(path) / "proton").is_file():
            return
        n = 0
        for g in self.store.games:
            if g.get("proton") != "auto" and Path(g.get("proton", "")).name.startswith("GE-Proton"):
                g["proton"] = path
                n += 1
        if self.store.settings.get("default_proton") != "auto":
            self.store.settings["default_proton"] = path
        self.store.save()
        self.refresh()
        name = Path(path).name
        self.toast(_n("{name} est utilisée par {n} jeu", "{name} est utilisée par {n} jeux", n).format(name=name, n=n)
                   if n else _("{name} est prête. Les jeux en mode auto l’utiliseront d’office.").format(name=name))

    # jaquettes
    def search_cover(self, gid):
        game = self.store.get(gid)
        if not game:
            return

        def on_pick(path, item):
            g = self.store.get(gid)
            if not g:
                return
            try:
                new = store_cover(gid, path)
            except OSError as e:
                self.toast(_('Jaquette non copiée : {0}').format(e.strerror))
                return
            old = g.get("cover")
            g.update(cover=new, cover_auto=True)
            if not g.get("steam_appid"):
                g["steam_appid"] = str(item["id"])
            if not g.get("gameid"):
                g["gameid"] = f"umu-{item['id']}"
            self.store.save()
            if old and old != new:
                delete_cover(old)
            if shortcut_path(gid).exists():
                write_shortcut(g)
            self.refresh()
            self.toast(_("Jaquette mise à jour"))
        CoverSearchDialog(self, game["name"], on_pick).present(self)

    def find_missing_covers(self):
        targets = [g for g in self.store.games if not load_texture(g.get("cover")) and g.get("exe")]
        if not targets:
            self.toast(_("Tous tes jeux ont déjà une jaquette."))
            return
        for g in targets:
            g["cover"] = ""
            g["cover_auto"] = False
        self._cover_report = {"total": len(targets), "found": 0}
        self.toast(_n("Recherche d’une jaquette pour {n} jeu…", "Recherche de jaquettes pour {n} jeux…",
                      len(targets)).format(n=len(targets)))
        self.auto_covers()

    def _cover_search_done(self, offline=False):
        report, self._cover_report = self._cover_report, None
        if not report:
            return
        if offline:
            self.toast(_("Recherche des jaquettes impossible hors connexion."))
            return
        msg = _n("{n} jaquette trouvée", "{n} jaquettes trouvées", report["found"]).format(n=report["found"])
        if report["found"] < report["total"]:
            msg += _(". Pour les autres, cherche-les depuis la page du jeu.")
        self.toast(msg, timeout=6)

    def auto_covers(self):
        """Au démarrage, cherche une jaquette pour les jeux qui n'en ont pas encore."""
        self._cover_queue = [g["id"] for g in self.store.games
                             if not g.get("cover") and not g.get("cover_auto") and g.get("exe")]
        self._next_cover()
        return False

    def _next_cover(self):
        if self.app.win is not self:
            return
        if not self._cover_queue:
            self._cover_search_done()
            return
        gid = self._cover_queue.pop(0)
        game = self.store.get(gid)
        if not game:
            self._next_cover()
            return
        name, exe = game["name"], game["exe"]

        def done(res):
            g = self.store.get(gid)
            if g:
                g["cover_auto"] = True
                if res and not g.get("cover"):
                    path, item = res
                    try:
                        g["cover"] = store_cover(gid, path)
                    except OSError:
                        pass
                    if not g.get("steam_appid"):
                        g["steam_appid"] = str(item["id"])
                    if self._cover_report:
                        self._cover_report["found"] += 1
                self.store.save()
                if res and self.app.win is self:
                    self.refresh()
            self._next_cover()

        def fail(_e):  # pas de réseau : on réessaiera au prochain démarrage
            self._cover_queue = []
            self._cover_search_done(offline=True)
        run_async(lambda: auto_cover_lookup(name, exe), done, fail)

    def switch_language(self):
        self.store.settings["language"] = "en" if LANG == "fr" else "fr"
        self.store.save()
        d = Adw.AlertDialog(heading="Restart Lanterne" if LANG == "fr" else "Redémarre Lanterne",
                            body="The new language will apply the next time you open Lanterne."
                            if LANG == "fr" else "La nouvelle langue s’appliquera à la prochaine ouverture de Lanterne.")
        d.add_response("later", "Later" if LANG == "fr" else "Plus tard")
        d.add_response("quit", "Quit now" if LANG == "fr" else "Quitter maintenant")
        d.set_response_appearance("quit", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _d, r: r == "quit" and self.app.quit())
        d.present(self)

    # isolation
    def sandbox_all(self):
        done, skipped = 0, []
        for g in self.store.games:
            if g.get("sandbox"):
                continue
            if sandbox_ok(g.get("exe")):
                g["sandbox"] = True
                done += 1
            else:
                skipped.append(g["name"])
        self.store.save()
        self.refresh()
        body = _n("{n} jeu isolé.", "{n} jeux isolés.", done).format(n=done)
        if skipped:
            body += (_("\n\nCes jeux sont rangés directement dans un dossier personnel (Téléchargements, "
                     "Documents…), qu’il faudrait partager en entier. Déplace-les dans leur propre dossier "
                     "pour pouvoir les isoler :\n") + "\n".join(f"• {n}" for n in skipped))
        d = Adw.AlertDialog(heading=_("Isolation des jeux"), body=body)
        d.add_response("ok", _("OK"))
        d.present(self)

    def test_isolation(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        prefix = Path(game["prefix"]).expanduser()
        drive = prefix / "drive_c"
        canary = HOME / ".lanterne-test-isolation"
        result = drive / "lanterne-isolation.txt"
        bat = drive / "lanterne-isolation.bat"
        try:
            drive.mkdir(parents=True, exist_ok=True)
            canary.write_text("Fichier de test de Lanterne, il sera supprimé.\n", encoding="utf-8")
            result.unlink(missing_ok=True)
            winpath = "Z:" + str(canary.resolve()).replace("/", "\\")
            bat.write_text("@echo off\r\n"
                           f'if exist "{winpath}" (echo VISIBLE> C:\\lanterne-isolation.txt) '
                           "else (echo ISOLE> C:\\lanterne-isolation.txt)\r\n", encoding="utf-8")
        except OSError as e:
            self.toast(_('Test impossible : {0}').format(e.strerror))
            return

        def finished(_code):
            try:
                verdict = result.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                verdict = ""
            for f in (canary, result, bat):
                f.unlink(missing_ok=True)
            if verdict == "ISOLE":
                heading, body = (_("L’isolation fonctionne"),
                                 _('« {0} » ne voit pas ton dossier personnel. Il n’a accès qu’à {1} et à son préfixe Wine.').format(game['name'], short_path(game_root(game['exe']))))
            elif verdict == "VISIBLE":
                heading, body = (_("L’isolation ne fonctionne pas"),
                                 _("Le jeu voit encore ton dossier personnel. Ta version d’umu ignore "
                                 "probablement les réglages d’isolation du conteneur. Envoie le journal "
                                 "« isolation » du dossier des journaux pour qu’on corrige."))
            else:
                heading, body = (_("Test non concluant"),
                                 _("Le test n’a pas pu s’exécuter. Le dossier des journaux contient "
                                 "le détail (fichier se terminant par « -isolation.log »)."))
            d = Adw.AlertDialog(heading=heading, body=body)
            d.add_response("ok", _("OK"))
            d.present(self)

        try:
            self.runner.run_tool(game, str(bat), on_exit=finished, log_suffix="isolation")
        except UmuMissing:
            self.ask_install_umu()
            return
        except LaunchError as e:
            for f in (canary, bat):
                f.unlink(missing_ok=True)
            self.toast(str(e), timeout=8)
            return
        self.toast(_("Test de l’isolation en cours… (quelques secondes, plus au premier lancement)"), timeout=5)

    # export / import d'archives de sauvegardes
    def export_saves(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        if not has_user_data(save_dir(game["prefix"])):
            self.toast(_("Ce jeu n’a pas encore de sauvegarde à exporter."))
            return
        safe = re.sub(r'[\\/:*?"<>|]+', " ", game["name"]).strip() or "jeu"
        dlg = Gtk.FileDialog(title=_("Exporter les sauvegardes"),
                             initial_name=f"{safe} - sauvegardes - {datetime.now():%Y-%m-%d}.zip")
        docs = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
        if docs and Path(docs).is_dir():
            dlg.set_initial_folder(Gio.File.new_for_path(docs))

        def chosen(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            if not f or not f.get_path():
                return
            dest = Path(f.get_path())
            if dest.suffix.lower() != ".zip":
                dest = dest.with_name(dest.name + ".zip")
            run_async(lambda: export_saves(game["prefix"], dest, game["name"]),
                      lambda n: self.toast(_n("{n} fichier exporté dans {dest}", "{n} fichiers exportés dans {dest}",
                                              n).format(n=n, dest=short_path(dest)), timeout=6),
                      lambda e: self.toast(_('Export impossible : {0}').format(e), timeout=6))
        dlg.save(self, None, chosen)

    def import_save_archive(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        if self.runner.is_running(gid):
            self.toast(_("Ferme le jeu avant d’importer des sauvegardes."))
            return
        dlg = Gtk.FileDialog(title=_("Importer une archive de sauvegardes"),
                             filters=filters(file_filter(_("Archives de sauvegardes"), ("zip",))))

        def confirm(path, meta, n):
            if meta.get("jeu") and isinstance(meta.get("date"), (int, float)):
                body = _n("{n} fichier de sauvegarde, exporté depuis « {game} » le {date}.",
                          "{n} fichiers de sauvegarde, exportés depuis « {game} » le {date}.", n).format(
                    n=n, game=meta["jeu"], date=fmt_date(meta["date"]))
            elif meta.get("jeu"):
                body = _n("{n} fichier de sauvegarde, exporté depuis « {game} ».",
                          "{n} fichiers de sauvegarde, exportés depuis « {game} ».", n).format(n=n, game=meta["jeu"])
            else:
                body = _n("{n} fichier de sauvegarde.", "{n} fichiers de sauvegarde.", n).format(n=n)
            if meta.get("jeu") and similarity(meta["jeu"], game["name"]) < 0.85:
                body += _('\n\nAttention : cette archive semble venir d’un autre jeu que « {0} ».').format(game['name'])
            body += _("\n\nLes sauvegardes actuelles seront mises de côté, puis remplacées.")
            d = Adw.AlertDialog(heading=_("Importer ces sauvegardes ?"), body=body)
            d.add_response("cancel", _("Annuler"))
            d.add_response("import", _("Importer"))
            d.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
            d.set_default_response("cancel")
            d.set_close_response("cancel")

            def on_response(_d, r):
                if r != "import":
                    return

                def done(res):
                    count, backup = res
                    msg = _n("{n} fichier de sauvegarde importé", "{n} fichiers de sauvegarde importés",
                             count).format(n=count)
                    self.toast(msg + (_(". Les anciens sont mis de côté") if backup else ""), timeout=6)
                    if self.detail and self.detail.gid == gid:
                        self.detail.refresh()
                run_async(lambda: import_save_archive(path, game["prefix"], gid), done,
                          lambda e: self.toast(_('Import impossible : {0}').format(e), timeout=6))
            d.connect("response", on_response)
            d.present(self)

        def picked(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if not f or not f.get_path():
                return
            path = f.get_path()
            run_async(lambda: read_save_archive(path), lambda r: confirm(path, *r),
                      lambda e: self.toast(str(e), timeout=6))
        dlg.open(self, None, picked)

    # sauvegardes Steam
    def import_steam_library(self):
        self.toast(_("Import depuis Steam en cours…"), timeout=3)

        def work():
            lines = []
            store = Store()
            added, saved = import_everything_from_steam(store, lines.append)
            return added, saved, lines

        def done(res):
            added, saved, lines = res
            self.store.load()
            self.refresh()
            GLib.timeout_add_seconds(1, self.auto_covers)
            d = Adw.AlertDialog(heading=_("Import depuis Steam"),
                                body=_n("{n} jeu ajouté, sauvegardes importées pour {saved}.",
                                        "{n} jeux ajoutés, sauvegardes importées pour {saved}.", added).format(
                                    n=added, saved=saved) + "\n\n" + "\n".join(lines))
            d.add_response("ok", _("OK"))
            d.present(self)
        run_async(work, done, lambda e: self.toast(_('Import impossible : {0}').format(e), timeout=6))

    def auto_import(self, game, source=None, quiet=True):
        gid, prefix = game["id"], game["prefix"]

        def work():
            src = source or find_steam_source(game)
            if not src or not src.get("has_data"):
                return None
            count, size, backup = import_saves(src["path"], prefix, gid)
            return src, count, size, backup

        def done(res):
            if not res:
                if not quiet:
                    self.toast(_("Aucune sauvegarde Steam trouvée pour ce jeu."))
                return
            src, count, size, backup = res
            g = self.store.get(gid)
            if g:
                g["steam_import_done"] = True
                self.store.save()
            if not count:
                self.toast(_("Le préfixe Steam de ce jeu ne contient aucune sauvegarde."))
                return
            msg = _n("{n} fichier de sauvegarde importé depuis Steam ({size})",
                     "{n} fichiers de sauvegarde importés depuis Steam ({size})", count).format(
                n=count, size=fmt_size(size))
            if backup:
                msg += _(". Les anciens sont mis de côté")
            self.toast(msg, timeout=6)
            if self.detail and self.detail.gid == gid:
                self.detail.refresh()
        run_async(work, done, lambda e: self.toast(_('Import des sauvegardes impossible : {0}').format(e), timeout=6))

    def import_from_steam(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        if self.runner.is_running(gid):
            self.toast(_("Ferme le jeu avant d’importer ses sauvegardes."))
            return

        def found(src):
            if not src:
                self.toast(_("Ce jeu est introuvable dans Steam."))
                return
            if not src["has_data"]:
                self.toast(_('Trouvé dans Steam ({0}), mais son préfixe ne contient aucune sauvegarde : elles sont sans doute dans le dossier du jeu.').format(src['label']), timeout=8)
                return
            d = Adw.AlertDialog(
                heading=_("Importer les sauvegardes de Steam ?"),
                body=_('Source : {0} ({1}), dans {2}.\n\nLes sauvegardes actuelles de ce jeu dans Lanterne seront mises de côté, puis remplacées par celles de Steam.').format(src['label'], SOURCE_KINDS[src['kind']], short_path(src['path'])))
            d.add_response("cancel", _("Annuler"))
            d.add_response("import", _("Importer"))
            d.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
            d.set_default_response("import")
            d.set_close_response("cancel")
            d.connect("response", lambda _d, r: r == "import" and self.auto_import(game, src, quiet=False))
            d.present(self)
        run_async(lambda: find_steam_source(game), found,
                  lambda e: self.toast(_('Recherche dans Steam impossible : {0}').format(e)))

    # outils dans le préfixe
    def run_tool(self, gid, program, extra_args=None, done_msg=None, log_suffix="outil"):
        game = self.store.get(gid)
        if not game:
            return

        def on_exit(code):
            if done_msg:
                self.toast(done_msg if code == 0 else
                           _("L’outil s’est terminé avec une erreur. Consulte le dossier des journaux."))
        try:
            self.runner.run_tool(game, program, extra_args, on_exit=on_exit, log_suffix=log_suffix)
        except UmuMissing:
            self.ask_install_umu()
        except LaunchError as e:
            self.toast(str(e), timeout=6)

    def run_winetricks(self, gid, text):
        verbs = text.split()
        if not verbs:
            self.toast(_("Indique au moins un composant, par exemple vcrun2022."))
            return
        self.run_tool(gid, "winetricks", ["-q", *verbs], done_msg=_("Winetricks a terminé l’installation"),
                      log_suffix="winetricks")
        self.toast(_('Winetricks installe {0}… Ça peut prendre un moment.').format(', '.join(verbs)))

    def pick_and_run(self, gid):
        game = self.store.get(gid)
        if not game:
            return
        dlg = Gtk.FileDialog(title=_("Lancer un exécutable dans ce préfixe"), filters=exe_filters())
        if game.get("exe") and Path(game["exe"]).parent.is_dir():
            dlg.set_initial_folder(Gio.File.new_for_path(str(Path(game["exe"]).parent)))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.run_tool(gid, f.get_path(), log_suffix="autre")
        dlg.open(self, None, done)

    def ask_install_umu(self):
        d = Adw.AlertDialog(heading=_("umu-launcher est nécessaire"),
                            body=_("C’est le moteur qui fait tourner Proton sans Steam. Lanterne peut le "
                                 "télécharger depuis GitHub et l’installer dans ton dossier personnel."))
        d.add_response("cancel", _("Plus tard"))
        d.add_response("install", _("Installer umu-launcher"))
        d.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("install")
        d.set_close_response("cancel")

        def on_response(_d, resp):
            if resp == "install":
                self.stack.set_visible_child_name("proton")
                self.nav.pop_to_tag("main")
                self.proton_page.install_umu()
        d.connect("response", on_response)
        d.present(self)

    def _on_close(self, *_):
        self.store.settings["window"] = [self.get_width(), self.get_height()]
        self.store.save()
        for h in self._handlers:
            self.runner.disconnect(h)
        self.app.win = None
        return False


# ────────────────────────────────────────────────────────── application ──

class LanterneApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.store = Store()
        self.runner = Runner(self)
        self.win = None
        self.add_main_option("launch", ord("l"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             _("Lance directement un jeu de la bibliothèque"), "ID")

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(CSS)
        else:
            provider.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb, accels in (("quit", lambda *_: self.quit(), ["<Control>q"]),
                                 ("about", self.on_about, [])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("win.add-game", ["<Control>n"])
        self.set_accels_for_action("win.search", ["<Control>f"])

    def do_activate(self):
        if not self.win:
            self.win = MainWindow(self)
        self.win.present()

    def do_command_line(self, cmdline):
        opts = cmdline.get_options_dict().end().unpack()
        gid = opts.get("launch")
        if not gid:
            self.activate()
            return 0
        game = self.store.get(gid)
        if not game:
            self.notify(_("Ce jeu n’existe plus dans la bibliothèque de Lanterne."))
            return 1
        try:
            hint = self.runner.launch(game)
            if hint and not self.win:
                self.notify(hint)
        except UmuMissing:
            self.activate()
            self.win.ask_install_umu()
        except LaunchError as e:
            if self.win:
                self.win.toast(str(e), timeout=6)
            else:
                self.notify(str(e))
        return 0

    def notify(self, body):
        n = Gio.Notification.new("Lanterne")
        n.set_body(body)
        self.send_notification(None, n)

    def on_about(self, *_):
        about = Adw.AboutDialog(application_name="Lanterne", application_icon=APP_ID, version=VERSION,
                                comments=_("Tes jeux Windows avec Proton, sans Steam."),
                                developer_name=_("Fait sur mesure pour Bazzite"),
                                license_type=Gtk.License.MIT_X11)
        about.present(self.win)


def main():
    if sys.argv[1:2] in (["--diagnostic-steam"], ["--steam-diagnostic"]):
        steam_diagnostic()
        return 0
    if sys.argv[1:2] in (["--importer-sauvegardes"], ["--importer-steam"], ["--import-steam"]):
        if not steam_libraries():
            print(_("  Steam est introuvable sur ce système : rien à importer."))
            return 0
        added, saved = import_everything_from_steam(Store())
        print("\n  " + _n("{n} jeu ajouté, sauvegardes importées pour {saved}.",
                           "{n} jeux ajoutés, sauvegardes importées pour {saved}.", added).format(n=added, saved=saved))
        return 0
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
        print(_("Lanterne nécessite libadwaita 1.5 ou plus récent."), file=sys.stderr)
        return 1
    return LanterneApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
