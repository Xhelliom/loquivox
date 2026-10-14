# Styles de la bulle du compagnon — maquettes

Rendu par défaut inchangé (`goutte`). Se choisit dans Réglages → Compagnon (ou `[companion] bubble_style` de config.toml) ; en démo :

```
python -m loquivox.ui.companion_bubble --demo --style <nom> [--state <état>]
python -m loquivox.ui.companion_bubble --gallery
```

## Coût CPU en veille

Mesure en état `idle`, 15 s, en % d'un cœur, commande
`python -m loquivox.ui.companion_bubble --demo --style <nom> --state idle`.

| Style        | CPU (idle) |
|--------------|-----------:|
| `goutte`     | ~6 %       |
| `reacteur`   | ~4 %       |
| `radar`      | ~3 %       |
| `plasma`     | ~2 %       |

## GIF (5 états chacun)

- `reacteur.gif`
- `radar.gif`
- `plasma.gif`
- `gallery.gif` — les quatre styles côte à côte
