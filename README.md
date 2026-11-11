# Lanterne

A Windows game launcher for Bazzite (GNOME). Runs your games with Proton, without Steam, using umu-launcher. Available in English and French.

## Install

```bash
bash install.sh           # installs, then imports your non-Steam games from Steam
bash install.sh --remove  # uninstalls (keeps your data)
```

## Features

- Runs each game with the latest GE-Proton, or the version you choose.
- Finds cover art on Steam.
- Imports your non-Steam games and their saves from Steam.
- Exports and imports a game’s saves as `.zip`.
- Isolates each game from your personal files. Use **Test** on the game’s page to check it.
- Tracks play time and can add a shortcut to the applications menu.

Right-click a game to edit or delete it.

## Where files are

| What | Where |
|---|---|
| Wine prefixes and saves | `~/Games/Lanterne/<game>/` |
| Library and settings | `~/.config/lanterne/` |
| Proton, cover art, logs | `~/.local/share/lanterne/` |

## Command line

```bash
lanterne --import-steam      # import non-Steam games and their saves
lanterne --steam-diagnostic  # show what Lanterne sees in Steam
```

## Language

Lanterne follows your system language. To change it, use **Switch to English** / **Passer en français** in the main menu, or set `LANTERNE_LANG=en` or `fr`.

## Limits

- Games protected by Steam DRM or some anti-cheats won’t start.
- An isolated game still has internet access and can’t see mods stored outside its folder.

If a game won’t start, open its log from its page (**Files** section).
