# Décors 2D du laboratoire (#175)

Quatre décors schématiques **réutilisables** pour les vidéos pédagogiques verticales (9:16, 1080 × 1920),
livrés en version 1 dans le catalogue `resource/animation-assets/labo-decors-catalogue.json`
(`lody.animation-assets/1`, type `background`).

| Décor | Asset | Objets montrés |
|---|---|---|
| Salon | `ast_175000000001` `decor-salon` | téléviseur et ses entrées HDMI latérales, box et sa sortie HDMI, câble HDMI visible, meuble TV, télécommande |
| Bureau | `ast_175000000002` `decor-bureau` | tour PC vue de dos (sorties DisplayPort de la carte graphique, sortie VGA), écran 16:9 en DisplayPort, ancien écran 4:3 en VGA, câbles, clavier, souris |
| Plateau TV | `ast_175000000003` `decor-plateau` | caméra de studio de profil, voyant tally, sortie SDI (BNC) et câble, trépied à roulettes, projecteurs, fond bleu, bureau du présentateur, moniteur de retour, marque au sol |
| Régie | `ast_175000000004` `decor-regie` | mur de moniteurs (aperçu PVW vert, programme PGM rouge, 4 sources), mélangeur (rangées programme/aperçu, levier T-bar), pupitre, baie BNC |

Planche de contrôle : [animation/decors-labo/planche-decors.png](animation/decors-labo/planche-decors.png)
(à gauche le décor seul, à droite les zones, places et points d'intérêt).

## Fichiers d'un décor

`resource/animation-assets/<décor>/v1/` :

- `<décor>.svg` — décor complet (rôle `base` du manifeste) ;
- `<décor>-fond.svg`, `<décor>-objets.svg`, `<décor>-avant.svg` — **couches composables** : ordre de dessin
  fond → objets → *personnages* → avant-plan (un accoudoir, un dossier de chaise, le bureau du présentateur
  passent devant les personnages) ;
- `<décor>-scene.json` (`lody.decor/1`, rôle `scene`) — fiche de mise en scène :
  - `zones.subtitles` : bande des sous-titres (y 1500 → 1860), où ne figure que le sol uni ;
  - `zones.title` : zone conseillée pour un titre ; `floor_y` : jonction mur/sol ;
  - `places` : emplacements des personnages (`x`, `y` = pieds, `height` = hauteur conseillée, 820 px) ;
  - `hotspots` : points d'intérêt (`id` = groupe SVG, libellé, couche, `bbox` [x, y, l, h], `point` de visée)
    pour placer une étiquette, une flèche ou un zoom de caméra du storyboard.

Dans un storyboard `lody.animation/1`, le décor complet se pose comme un asset centré
(`x: 540, y: 960`, ancre 0,5/0,5) dans le premier calque ; il sert aussi de repli `fallback.asset`.

## Règles de dessin

Formes simples à coordonnées absolues, aucun `transform`, aucun texte, aucune marque, aucun dégradé, aucun
script ni ressource externe. Connecteurs dessinés selon leur forme normalisée (HDMI type A à pans coupés,
DisplayPort à un seul coin coupé, VGA D-Sub 15 contacts sur 3 rangées avec vis, BNC). Convention
schématique : la connectique est ramenée sur une face visible (panneau latéral du téléviseur, tranche de la
box, tour PC vue de dos, tranche inférieure des écrans).

## Régénérer, retoucher

```bash
python scripts/animation/decors_labo.py            # SVG, fiches de scène et catalogue v1
python scripts/animation/planche_decors.py --png   # planche de contrôle (Chromium local)
```

`test/lody/test_decors_labo.py` vérifie que les fichiers versionnés sont ceux du générateur, les couches,
la bande des sous-titres, les places et points d'intérêt, l'absence de contenu actif ou de marque, et
l'import + gel du catalogue. Une retouche à la main se publie en **version 2** (`v2/`) : la v1 reste immuable.
