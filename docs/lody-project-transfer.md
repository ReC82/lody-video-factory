# Export / import de configuration de projet (tickets #44, #57 et #66)

Format JSON explicite et versionné (`schema_version`).

- **Complet** (page **Paramètres du projet**, `webui/lody/project_transfer.py`) : exporte/importe un projet
  entier (paramètres + personnages + lieux), avec **deux modes explicites**, choisis par l'utilisateur dans
  l'interface (jamais déduits automatiquement du contenu du fichier) :
  - **Créer un nouveau projet** (#44, `preview_import`/`commit_import`) : crée **toujours** un nouveau
    projet (jamais d'écrasement silencieux d'un projet existant : un nom déjà pris est rendu distinct
    automatiquement, ex. `PNJ (import)`).
  - **Mettre à jour un projet existant** (#66, `preview_update`/`commit_update`) : applique le fichier à un
    projet **choisi explicitement** par l'utilisateur — voir « Mode mise à jour » ci-dessous.
- **Partiel** (#57, pages **Personnages** et **Lieux**, `webui/lody/resource_transfer.py`) : exporte/importe
  UNIQUEMENT des personnages ou UNIQUEMENT des lieux, dans le projet **déjà existant** actuellement ouvert
  (jamais de nouveau projet créé, jamais un autre paramètre du projet touché — inchangé par #66). Enveloppe
  distincte (`{schema_version, resource_type: "characters"|"locations", characters|locations: [...]}`,
  vérifiée : un fichier de personnages ne peut jamais être accepté comme fichier de lieux) mais **réutilise
  à l'identique** les tuples de champs, `example_item()`, `load_json()` et `validate_items()` de
  `project_transfer.py` — donc tout ce qui suit dans ce document s'applique aux trois modes. Politique de
  collision d'un import PARTIEL : un nom déjà présent dans le projet cible est un conflit **bloquant** (pas
  de fusion silencieuse) — différent du mode Mise à jour ci-dessous, où un nom déjà présent est précisément
  ce qui déclenche une correspondance (mise à jour), pas un conflit.

## Mode mise à jour (#66)

Permet d'exporter un projet, modifier sa configuration/ses personnages/ses lieux dans le JSON, puis
réimporter ce fichier pour appliquer volontairement ces changements **au projet d'origine** (utile pour
tester une nouvelle génération sans recréer le projet).

- **Identifiant et historique conservés** : le projet cible garde son `id` ; aucune ligne `productions`
  (snapshots, traces, historique de génération) n'est jamais touchée.
- **Champs projet** : remplacés par ceux du fichier (y compris `settings`, donc le `brief` et les réglages
  de génération — jamais ignorés, voir `test_settings_and_brief_are_never_ignored_by_the_round_trip`).
- **Correspondance des personnages/lieux** : par **nom normalisé** (`casefold()`), à l'intérieur du projet
  cible uniquement — `id` n'est jamais exporté (voir « Toute PR... » ci-dessous), donc aucun identifiant
  stable n'est disponible pour une correspondance plus fine. Un nom du fichier qui correspond à un
  personnage/lieu déjà présent le **met à jour** ; un nom nouveau en **crée** un ; un existant absent du
  fichier est **désactivé** (jamais supprimé physiquement — cohérent avec `characters.deactivate`/
  `locations.deactivate`). Un doublon de nom À L'INTÉRIEUR du fichier reste bloqué comme pour les deux
  autres modes (`validate_items`) : aucune fusion silencieuse, aucune ambiguïté.
- **Nom du projet** : peut changer (c'est un champ éditable comme un autre), mais jamais renommé
  automatiquement en cas de collision comme en création — si un AUTRE projet porte déjà ce nom, c'est une
  erreur bloquante.
- **Confirmation explicite** : l'interface demande de taper le nom du projet cible avant d'activer le
  bouton de confirmation (jamais une simple case à cocher) ; annuler (ne pas confirmer, changer de page)
  ne modifie rien, car `preview_update` seul n'écrit jamais.
- **Atomicité et isolation** : `commit_update` applique tout dans **une seule transaction** SQLite, bornée
  par `WHERE project_id = <cible>` sur chaque écriture — jamais un autre projet touché, tout ou rien en cas
  d'erreur (voir `test_atomic_rollback_on_concurrent_name_collision_leaves_nothing_partial`).
- **Hors périmètre** : fusion interactive champ par champ, restauration automatique d'une ancienne version,
  suppression physique d'assets, modification des snapshots historiques (voir le ticket #66).

## Source de vérité unique

`project_transfer.PROJECT_FIELDS` / `CHARACTER_FIELDS` / `LOCATION_FIELDS` **SONT** les tuples
`EDITABLE_FIELDS` de `lody.projects` / `lody.characters` / `lody.locations` — pas une copie. L'export,
l'import et le modèle JSON téléchargeable (`blank_template()`) itèrent tous les trois ces mêmes tuples :
aucun n'entretient sa propre liste de champs.

## Règle pour toute PR qui ajoute un champ persistant

**Toute PR qui ajoute (ou retire) un champ persistant à `Project`, `Character` ou `Location` doit
vérifier son impact sur :**

1. **l'export** — le champ doit apparaître dans `EDITABLE_FIELDS` du modèle concerné (il rejoint alors
   automatiquement l'export/l'import/le modèle téléchargeable), **ou** être ajouté à la liste d'exclusion
   documentée (`PROJECT_FIELDS_EXCLUDED`/`CHARACTER_FIELDS_EXCLUDED`/`LOCATION_FIELDS_EXCLUDED` dans
   `test/lody/test_project_transfer.py`) avec une raison explicite ;
2. **l'import** — une valeur par défaut raisonnable si le champ est optionnel (les anciens fichiers,
   sans ce champ, doivent rester importables) ;
3. **le modèle JSON téléchargeable** — rien à faire manuellement s'il est ajouté à `EDITABLE_FIELDS` :
   `blank_template()` (#44) et `character_template()`/`location_template()` (#57) en dérivent
   automatiquement (voir « Source de vérité unique » ci-dessus) ; une valeur d'exemple plus parlante que la
   valeur par défaut peut être ajoutée à `_PROJECT_EXAMPLE` (projet, #44 seulement) ou à
   `CHARACTER_EXAMPLE_*`/`LOCATION_EXAMPLE_*` (publiques dans `project_transfer.py` : réutilisées telles
   quelles par `resource_transfer.py` pour #57) ;
4. **la compatibilité** — un ancien export (sans ce nouveau champ) doit toujours s'importer sans erreur ;
5. **les secrets** — un champ qui ressemble à une clé API, un jeton ou un mot de passe ne doit jamais
   rejoindre `EDITABLE_FIELDS` (voir `lody.secrets_guard`, déjà appliqué à la validation de chaque champ).

Un oubli à l'étape 1 est détecté par un test qui échoue en CI (`test_every_project_field_is_exported_or_
explicitly_excluded_with_a_reason` et ses équivalents pour `Character`/`Location`, dans
`test/lody/test_project_transfer.py`) plutôt que découvert plus tard sous la forme d'un export incomplet.
Ce garde-fou couvre aussi le format partiel de #57 : `resource_transfer.CHARACTER_FIELDS`/`LOCATION_FIELDS`
**sont** (identité d'objet, pas une copie — voir `test_resource_transfer_uses_the_exact_same_field_tuples_
as_project_transfer` dans `test/lody/test_resource_transfer.py`) les tuples `EDITABLE_FIELDS` déjà couverts.
