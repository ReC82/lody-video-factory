# Contrat de storyboard animé 2D et catalogue d'assets (`lody.animation/1`)

> Ticket #119. Couche de **données** uniquement : aucun rendu, aucune interface, aucun appel payant.
> Le moteur de rendu (worker Remotion prototypé en #118) et le mode de projet « Animation 2D / Hybride »
> arrivent avec #120 ; la réédition ciblée par scène avec #121.

Un projet ou une production **sans animation fonctionne exactement comme avant** : rien du pipeline
MoneyPrinterTurbo ne lit ces données, et `productions.animation` vaut `{}` pour toute production classique.

## Vue d'ensemble

| Élément | Où | Rôle |
|---|---|---|
| Storyboard animé | `webui/lody/animation/contract.py` | Format JSON `lody.animation/1`, validation stricte, empreinte SHA-256 |
| Catalogue d'assets | `webui/lody/animation/assets.py`, tables `animation_assets` / `animation_asset_versions` (migration v9) | Marionnettes, décors, accessoires réutilisables d'un projet, **versions immuables** |
| Gel | `webui/lody/animation/freeze.py` | Remplace « latest » par des versions explicites, vérifie gestes/expressions, calcule l'empreinte |
| Production | colonne `productions.animation` | Storyboard **figé** de cette production, immuable après confirmation, repris tel quel par une V2 |
| Maquette | [`animation/exemple-deux-scenes.json`](animation/exemple-deux-scenes.json), [`animation/exemple-catalogue.json`](animation/exemple-catalogue.json) | Deux scènes (11 s) et leur catalogue, validées par les tests |

## Le storyboard

```jsonc
{
  "schema": "lody.animation/1",
  "canvas": {"fps": 30, "width": 1080, "height": 1920},   // 24/25/30/50/60 fps, dimensions paires
  "scenes": [{
    "id": "accueil",                 // identifiant stable (réédition ciblée #121)
    "duration_frames": 150,          // durée en images ; tous les temps sont relatifs au début de la scène
    "intent": {                      // INTENTION NARRATIVE — jamais interprétée par le moteur
      "narration": "Bonjour ! Je suis Mimo.", "purpose": "Accrocher", "mood": "enjoué", "notes": "…"
    },
    "render": {                      // INSTRUCTIONS TECHNIQUES — déclaratives
      "layers": [                    // ordre = profondeur (premier calque au fond)
        {"id": "personnages", "objects": [{
          "id": "mimo", "type": "asset",
          "asset": {"id": "ast_…", "version": "latest"},          // entier obligatoire une fois figé
          "start": 0, "end": 150,                                   // fenêtre de visibilité (facultative)
          "position": [{"frame": 0, "x": -300, "y": 1500}, {"frame": 24, "x": 540, "y": 1500}],
          "easing": "spring",                                       // trajectoire entre images-clés
          "scale": [{"frame": 0, "value": 0.9}], "rotation": [], "opacity": [],
          "expressions": [{"frame": 30, "name": "joie"}],          // noms déclarés par l'asset
          "gestures": [{"start": 30, "end": 90, "name": "salut"}], // sans chevauchement
          "lipsync": true                                           // bouche pilotée par l'audio (#118)
        }, {
          "id": "titre", "type": "text", "text": {"content": "Bonjour", "style": "title"},
          "position": [{"frame": 6, "x": 540, "y": 360}]
        }]}
      ],
      "camera": {"keyframes": [{"frame": 0, "x": 540, "y": 960, "zoom": 1.0, "rotation": 0}], "easing": "ease_in_out"},
      "speech": {"speaker": "mimo", "cues": [{"text": "Bonjour", "start": 30, "end": 44}]},
      "transition_in": {"type": "fade", "frames": 10},
      "transition_out": {"type": "cut", "frames": 0},
      "fallback": {"type": "still", "frame": 60}     // ou {"type": "asset", "asset": {décor}}
    }
  }]
}
```

Unités : images (entiers), pixels du canevas (une marge d'un canevas hors champ est permise pour les
entrées/sorties), degrés. Entre deux images-clés, l'adaptateur interpole selon `easing`
(`linear`, `ease_in`, `ease_out`, `ease_in_out`, `spring`) — l'équivalent de `keyframes()` et `spring()`
du prototype #118.

**Repli** (`fallback`, obligatoire) : l'image fixe affichée si l'animation de cette scène échoue au rendu
(rendu hybride #120) — soit une image de la scène elle-même (`still`), soit un décor du catalogue (`asset`).

### Règles de sûreté

- **Aucun champ n'est du code.** Courbes = énumération ; gestes/expressions = noms déclarés par la version
  de l'asset ; texte = texte. Aucun JavaScript généré n'est exécuté : l'adaptateur #120 traduit ce JSON
  vers des composants **écrits et relus à l'avance**.
- **Clé inconnue = refus**, à tous les niveaux (on ne peut pas glisser un champ `script` ou `onRender`).
- **Aucune URL ni chemin** dans le storyboard : les fichiers sont dans le catalogue, en chemins relatifs
  au dossier de données (`..`, chemins absolus, schémas d'URL et extensions exécutables refusés).
- Aucune clé secrète (`secrets_guard`), aucun caractère de contrôle, longueurs bornées.
- Erreurs précises et toutes à la fois : `scenes[0].render.layers[1].objects[0].gestures[1].start`.

## Le catalogue d'assets

- Types : `character` (marionnette), `background` (décor), `prop` (accessoire), `overlay` (surcouche).
- Un asset `character` peut être **relié à un personnage** existant du projet, un `background` à un **lieu**.
  `characters.py` et `locations.py` ne changent pas : ils restent des fiches narratives, le catalogue
  porte les calques.
- Chaque **version** (1, 2, 3…) a un **manifeste** : format (`svg`/`png`), dimensions, point d'ancrage,
  parties de la marionnette, expressions et gestes disponibles, fichiers (`role`, chemin relatif,
  SHA-256). Une version publiée est **immuable** (refus applicatif + déclencheur SQLite) ; on en publie
  une nouvelle.
- Portée par projet (isolation identique aux personnages, lieux et templates) ; désactiver un asset
  ne supprime rien.

## Gel d'une production

`freeze_document(repo, project_id, storyboard)` :

1. vérifie que chaque asset existe **dans ce projet**, est actif, et que la version demandée existe ;
2. remplace `"latest"` par le numéro de version courant ;
3. vérifie expressions et gestes contre le manifeste de **cette** version, la synchro labiale (marionnette
   de personnage) et le repli `asset` (décor) ;
4. ajoute `frozen = {at, assets: [{id, version, sha256 du manifeste, kind, name}], sha256 du contenu}`.

`productions.animation` n'accepte qu'un document figé et intact (empreinte recalculée à l'écriture) ;
comme `snapshot`, il ne s'écrit qu'avant confirmation. Une **V2** en hérite tel quel : publier une
nouvelle version d'un asset ne modifie ni la V1 ni la V2. L'export de configuration d'une production
contient `storyboard_anime` (« aucun : production classique » sinon).

## Export / import

- `export_catalog(project_id)` → `lody.animation-assets/1` : assets, versions, manifestes et empreintes ;
  liens exportés par **nom** de personnage/lieu.
- `import_catalog(project_id, data)` → `{ancien id: nouvel id}` + avertissements ; numéros de version et
  manifestes conservés (mêmes empreintes), manifeste altéré refusé, lien vers un personnage/lieu absent
  abandonné avec avertissement.
- `remap_assets(storyboard, id_map)` puis `freeze_document` : le storyboard désigne exactement les mêmes
  versions dans le projet cible.
- Le transfert de projet existant (`lody-project-transfer.md`, schéma 1) est **inchangé** ; y intégrer
  le catalogue et transférer les octets des fichiers relève de l'interface (#120).

## Correspondance avec le prototype Remotion (#118)

| #118 (`props.json` proposé) | Contrat #119 |
|---|---|
| `schema: "lody.animation/1"`, `fps`, `width`, `height` | `schema`, `canvas` |
| `durationInFrames` | somme des `duration_frames` |
| `words: [{text,start,end}]` | `render.speech.cues` (par scène) |
| `envelope` (RMS par image) → `mouthOpen()` | calculé par le worker depuis l'audio pour les objets `lipsync` |
| `keyframes()`, `spring()`, `blink()`, `gaze()` | `position`/`scale`/`rotation`/`opacity` + `easing` ; clignements et regard = comportements de la marionnette (gestes/expressions déclarés) |
| `scenes: [ … ]` | ce contrat |

Le contrat est **indépendant du moteur** : si la licence Remotion était refusée, un autre moteur
(Motion Canvas, SVG → images + ffmpeg) consommerait le même JSON.
