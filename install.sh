#!/usr/bin/env bash
# Installs Lanterne for the current user (nothing is written outside ~).
# Installe Lanterne pour l'utilisateur courant (rien n'est écrit hors de ~).
# Usage: bash install.sh            install or update / installer ou mettre à jour
#        bash install.sh --remove   uninstall, keeps your data / désinstaller, garde tes données
set -euo pipefail

APP_ID="fr.perso.Lanterne"
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin/lanterne"
APPS="$HOME/.local/share/applications"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"

lang="${LANTERNE_LANG:-${LANGUAGE:-${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}}}"
case "$lang" in fr*) FR=1 ;; *) FR=0 ;; esac
say() { if [[ $FR == 1 ]]; then echo "$1"; else echo "$2"; fi; }

if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$BIN" "$APPS/$APP_ID.desktop" "$ICON" "$APPS"/lanterne-*.desktop
  update-desktop-database "$APPS" 2>/dev/null || true
  say "Lanterne est désinstallé." "Lanterne has been uninstalled."
  say "Conservés : ~/.config/lanterne, ~/.local/share/lanterne et ~/Games/Lanterne." \
      "Kept: ~/.config/lanterne, ~/.local/share/lanterne and ~/Games/Lanterne."
  exit 0
fi

say "Vérification de GTK 4 et libadwaita…" "Checking GTK 4 and libadwaita…"
if ! python3 - <<'PY'
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)
PY
then
  say "Il manque GTK 4 / libadwaita 1.5+ pour Python (python3-gobject)." \
      "GTK 4 / libadwaita 1.5+ for Python (python3-gobject) is missing."
  say "Sur Bazzite : rpm-ostree install python3-gobject libadwaita, puis redémarre." \
      "On Bazzite: rpm-ostree install python3-gobject libadwaita, then reboot."
  exit 1
fi

install -Dm755 "$SRC/lanterne.py" "$BIN"
install -Dm644 "$SRC/$APP_ID.svg" "$ICON"
mkdir -p "$APPS"
cat > "$APPS/$APP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Lanterne
Comment=Play your Windows games with Proton, without Steam
Comment[fr]=Lance tes jeux Windows avec Proton, sans Steam
Exec=$BIN
Icon=$APP_ID
Categories=Game;
Keywords=proton;wine;games;windows;umu;jeux;
StartupNotify=true
DBusActivatable=false
DESKTOP

update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

say "Lanterne est installé. Cherche « Lanterne » dans tes applications." \
    "Lanterne is installed. Look for “Lanterne” in your applications."
echo
say "Import de tes jeux non-Steam et de leurs sauvegardes depuis Steam…" \
    "Importing your non-Steam games and their saves from Steam…"
"$BIN" --importer-steam || say "  L’import a échoué. Relance-le avec : lanterne --importer-steam" \
                              "  The import failed. Run it again with: lanterne --import-steam"
echo
if command -v umu-run >/dev/null; then
  say "umu-launcher détecté : $(command -v umu-run)" "umu-launcher found: $(command -v umu-run)"
else
  say "umu-launcher n'est pas installé : Lanterne te proposera de le télécharger." \
      "umu-launcher isn’t installed: Lanterne will offer to download it."
fi
