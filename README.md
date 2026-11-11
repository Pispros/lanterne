# Lanterne

A Windows apps launcher for Bazzite (GNOME). Runs your apps with Proton, without Steam, using umu-launcher. Available in English and French.

## Install

```bash
bash install.sh           # installs, then imports your non-Steam apps from Steam
bash install.sh --remove  # uninstalls (keeps your data)
```

## Features

- Runs each app with the latest GE-Proton, or the version you choose.
- Finds cover art on Steam.
- Imports your non-Steam apps and their saves from Steam.
- Exports and imports a app’s saves as `.zip`.
- Isolates each app from your personal files. Use **Test** on the app’s page to check it.
- Tracks play time and can add a shortcut to the applications menu.

Right-click a app to edit or delete it.

## Where files are

| What | Where |
|---|---|
| Wine prefixes and saves | `~/apps/Lanterne/<app>/` |
| Library and settings | `~/.config/lanterne/` |
| Proton, cover art, logs | `~/.local/share/lanterne/` |

## Command line

```bash
lanterne --import-steam      # import non-Steam apps and their saves
lanterne --steam-diagnostic  # show what Lanterne sees in Steam
```

## Language

Lanterne follows your system language. To change it, use **Switch to English** / **Passer en français** in the main menu, or set `LANTERNE_LANG=en` or `fr`.

## Limits

- apps protected by Steam DRM or some anti-cheats won’t start.
- An isolated app still has internet access and can’t see mods stored outside its folder.

If a app won’t start, open its log from its page (**Files** section).
