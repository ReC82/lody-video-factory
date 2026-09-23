# Export / import de configuration de projet (ticket #44)

Format JSON explicite et versionné (`schema_version`), depuis la page **Paramètres du projet**.
L'import crée **toujours** un nouveau projet (jamais d'écrasement silencieux d'un projet existant :
un nom déjà pris est rendu distinct automatiquement). Voir `webui/lody/project_transfer.py`.

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
   `blank_template()` en dérive automatiquement (voir « Source de vérité unique » ci-dessus) ; une valeur
   d'exemple plus parlante que la valeur par défaut peut être ajoutée à `_PROJECT_EXAMPLE`/
   `_CHARACTER_EXAMPLE_*`/`_LOCATION_EXAMPLE_*` ;
4. **la compatibilité** — un ancien export (sans ce nouveau champ) doit toujours s'importer sans erreur ;
5. **les secrets** — un champ qui ressemble à une clé API, un jeton ou un mot de passe ne doit jamais
   rejoindre `EDITABLE_FIELDS` (voir `lody.secrets_guard`, déjà appliqué à la validation de chaque champ).

Un oubli à l'étape 1 est détecté par un test qui échoue en CI (`test_every_project_field_is_exported_or_
explicitly_excluded_with_a_reason` et ses équivalents pour `Character`/`Location`, dans
`test/lody/test_project_transfer.py`) plutôt que découvert plus tard sous la forme d'un export incomplet.
