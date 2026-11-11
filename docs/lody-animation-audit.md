# Audit du rendu 2D — reproductibilité, cache et coût (#168)

Outil **local et gratuit** : il relit ce que le worker de rendu 2D a déjà écrit (`manifest.json`, traces de
scène, `worker.log`, `audio.json`, storyboard figé) et n'appelle jamais aucun fournisseur payant. Il ne modifie
rien, sauf `clean --apply`.

- Code : `webui/lody/animation/render_audit.py` (module isolé, aucun import depuis le worker hors utilitaires
  de verrou) ; CLI : `scripts/animation/audit_render.py` ; tests : `test/lody/test_render_audit.py`.
- Hors périmètre, déjà traité ailleurs : cache de scène par dépendance et plafond du cache (#121, PR #157),
  contrôle qualité image/sous-titres (#166), voix ElevenLabs par scène (#164).

## Commandes

```bash
# Rapport JSON + HTML d'un dossier de rendu (tâche <données>/animation/<projet>/<tâche>/ ou démo docs/animation/…)
uv run --no-sync python scripts/animation/audit_render.py report docs/animation/voltix-150 --out /tmp/audit-150
#   option --previous-storyboard ancien.json : ajoute l'analyse d'invalidation (V1 → V2)
#   option --pricing pricing.toml           : estimations chiffrées (format de docs/lody-generation.md)

# Ce qu'une retouche ferait refaire (clé de cache actuelle vs clé par dépendance)
uv run --no-sync python scripts/animation/audit_render.py diff ancien.json nouveau.json

# Mesurer une étape locale : durée réelle, CPU (processus + enfants), RAM max → metrics.json
uv run --no-sync python scripts/animation/audit_render.py measure --stage voice \
    --metrics <dossier de rendu>/metrics.json -- <commande locale>

# Intermédiaires temporaires des tâches terminées : simulation par défaut, --apply pour supprimer
uv run --no-sync python scripts/animation/audit_render.py clean <dossier de données>
```

`audit.html` est une page autonome (aucune ressource externe) : étapes, scènes, contrôles de seuils, dépenses,
invalidations, recommandations, empreintes.

## Ce que le rapport mesure

| Étape | Source aujourd'hui |
|---|---|
| Script, assets, voix | non instrumentées par le worker → `measure --stage …` ou `StageRecorder` (`metrics.json`) |
| Visèmes | calculés par le moteur Remotion pendant le rendu de la scène (inclus dans « Rendu des scènes ») |
| Rendu des scènes (Remotion) | somme des `render_ms` des scènes **réellement rendues** (les scènes réutilisées ne comptent pas) |
| Mixage + assemblage + sous-titres | `render_ms` total − scènes (ou horodatage de `worker.log`) |

- **Cache** : scènes réutilisées (`cached` de la reprise du worker, `cache: hit` du cache de scène #121) et
  secondes de rendu économisées (`render_ms_saved`).
- **Reproductibilité** : empreinte du storyboard figé, nombre d'assets épinglés, MP4 recalculé et comparé à
  l'empreinte du manifeste (un MP4 retouché n'est plus « validé »).
- **Dépenses** : *connues* = montants déclarés par le fournisseur dans `audio.json` (`cost.amount`,
  `cost.currency`) ou 0 pour une source locale (Piper, musique générée) ; *estimées* = caractères parlés ×
  tarif ElevenLabs de `pricing.toml`. Sans tarif : « non configuré », jamais de montant inventé.
- **Disque** : taille du dossier, espace libre du système de fichiers.

### Seuils (`render_audit.THRESHOLDS`)

| Contrôle | Seuil | Mesuré (#150 / #137 V1 / #137 V2) |
|---|---|---|
| Rendu complet, secondes par seconde de vidéo | 10 | 7,1 / 6,0 / 5,8 |
| Rendu d'une scène, secondes par seconde | 12 | 5,2–7,2 / 4,4–6,4 / 4,1–6,8 |
| Écart durée réelle / attendue d'une scène | 0,1 s | ≤ 0,003 s partout |
| Poids du MP4 final par seconde | 1 Mo | 112 Ko / 85 Ko / 83 Ko |
| RAM max d'une étape mesurée | 2 Gio | 0,46 Go (rendu #150) ; pic 1,49 Go avec l'empaquetage webpack |
| Taille d'un dossier de rendu | 2 Gio | 3 Mo / 5,6 Mo / 5,4 Mo (dossiers de démo) |
| Espace libre minimal avant un rendu | 5 Gio | — |

## Invalidations : actuel vs par dépendance

`diff` compare deux storyboards scène par scène (appariées par `id`, sinon par position) et classe chaque
différence : `screen_text` (texte affiché), `speech_cues` (répliques), `narration`, `intent`, `duration`,
`visual`, `new`.

- **Actuel (`main`)** : la clé d'une scène est le JSON canonique de la scène **entière** (y compris `intent`,
  que le moteur ne lit jamais) + l'empreinte de `voice.wav` **complet**. La voix étant une piste unique,
  modifier une seule réplique la fait resynthétiser en entier et refait le rendu de **toutes** les scènes.
- **Par dépendance (cible)** : rendu refait seulement si le visuel, le texte d'écran, les répliques, la durée ou
  la voix de CETTE scène changent ; voix synthétisée scène par scène (texte = répliques), seulement si son texte
  change. C'est ce que livrent ensemble #121 (PR #157) et #164 (PR #169).

Cas mesurés sur le storyboard figé #150 (3 scènes, 218 caractères parlés) :

| Retouche | Rendus refaits (actuel → dépendance) | Caractères de voix (actuel → par scène) |
|---|---|---|
| Un texte d'écran de la scène 2 (« Isolant » → « Isolant (diélectrique) ») | 1 → 1 | 0 → 0 |
| `intent.purpose` de la scène 1 seulement | 1 → **0** | 0 → 0 |
| Une réplique de la scène 3 + un texte d'écran de la scène 2 | 3 → **2** | 225 → **97** |

Un changement de texte d'écran seul n'invalide donc pas la voix, même aujourd'hui ; en revanche, dès qu'une
réplique change ailleurs, la voix inchangée des autres scènes est refacturée.

## Audit du 2026-11-12 (sans génération)

Sorties analysées : MP4 #150 (`docs/animation/voltix-150`), épisode SDI #137 V1 (PR #158) et V2 (PR #160),
réédition #159 (PR #176). Toutes en voix Piper locale + musique générée : **0 € dépensé**.

| Rendu | Vidéo | Scènes rendues / réutilisées | Rendu des scènes | Mixage + assemblage | MP4 = manifeste |
|---|---|---|---|---|---|
| #150 Voltix | 19,5 s | 3 / 0 | 118,0 s | 21,4 s | oui |
| #137 SDI V1 | 64,5 s | 9 / 0 | 326,9 s | 60,0 s | oui |
| #137 SDI V2 | 63,9 s | 9 / 0 | 310,3 s | 58,8 s | oui |
| #159 V2 d'une scène | 19,5 s | 1 / 2 (170,6 s économisées) | 47,5 s | 39,2 s | (MP4 non versionné) |

Constats :

1. **Aucun seuil dépassé** ; rendu stable autour de 5–7 s par seconde de vidéo ; durées exactes (≤ 3 ms d'écart).
2. **SDI V1 → V2** : réécriture complète (9 scènes, toutes les répliques changent) → 9 rendus et 913 caractères
   refaits dans les deux modèles : **aucune invalidation inutile**, la V2 coûtait réellement un rendu complet.
3. **L'assemblage devient le poste dominant d'une réédition** : ≈ 1 s par seconde de vidéo (ré-encodage
   libx264 de toutes les scènes + mixage + deux mesures `volumedetect`) ; dans la V2 #159 il pèse 39 s sur 87 s.
4. **Voix** : la clé actuelle (piste unique) refacture et refait tout pour une réplique changée ; l'épisode SDI
   représente 836–913 caractères par synthèse complète.
5. **Disque** : les dossiers de rendu restent petits (3–6 Mo), mais les intermédiaires (`*.render.mp4`,
   images de repli, `concat.txt`, `*-input.json`, fichiers `.tmp`) restaient indéfiniment → `clean`.

Recommandations (non appliquées ici pour ne pas entrer en conflit avec les PR ouvertes sur le worker) :

- Fusionner #157 (cache par dépendance, `intent` ignoré) et #169 (voix par scène, en cache) : ce sont eux qui
  suppriment les invalidations inutiles mesurées ci-dessus.
- Assemblage : concaténer les scènes en **copie de flux** (`-c:v copy`) quand elles partagent codec, taille et
  cadence (même moteur, même canevas), et n'encoder que l'audio ; garder le ré-encodage en repli.
- Horodater dans le worker les étapes mixage / assemblage / sous-titres et écrire `metrics.json`
  (`StageRecorder`) pour que le rapport mesure toutes les étapes sans outil externe.
- Renseigner `cost.amount` dans `audio.json` lorsque le fournisseur connaît le coût réel (ElevenLabs).
- Ajouter le tarif ElevenLabs à `pricing.toml` pour des estimations chiffrées.

## Nettoyage et limites de disque

`clean` ne traite qu'une tâche **terminée** (`done`, `failed`, `cancelled`) dont aucun worker ne tient le
verrou, et ne supprime que des intermédiaires régénérables :

`out.partial.mp4`, `.*.tmp`, `.*.tmp.wav`, `scenes/*.render.mp4` (+ `.log`), `scenes/*-still.png` (+ `.log`),
`scenes/concat.txt`, `scenes/*-input.json`.

Jamais : `out.mp4`, `manifest.json`, `job.json`, `state.json`, `assets/`, `scenes/NN.mp4` et `NN.json` (reprise
et réédition), voix / musique / mixage, `subtitles.srt`, `worker.log`, `metrics.json`, ni un lien symbolique.
Les assets versionnés du catalogue et les MP4 validés ne sont jamais dans le périmètre. `disk_status` signale un
espace libre inférieur à 5 Gio (recommandation `clean` dans le rapport).
