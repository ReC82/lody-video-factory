# Recette contrôlée du rendu 2D parlé — ElevenLabs, cache, réédition (#187)

> Ticket #187, après la fusion de #169 (`main` `0f507ad`). Objectif : vérifier en DEV, **avec un coût borné et
> autorisé par écrit**, la chaîne Docker Remotion + voix ElevenLabs + cache de scène sensible aux repères recalés +
> réédition V2. Ce document **ne lance rien** : aucune commande ci-dessous n'est exécutée par l'orchestrateur.
> Tout ce qui pouvait être vérifié gratuitement l'est par des tests (`test/lody/test_recette_dev_2d.py`).

## 1. Ce qui est déjà vérifié sans appel payant

`test/lody/test_recette_dev_2d.py` rejoue tout le protocole avec un **faux ElevenLabs** qui compte ses appels, un
moteur de scène factice et le vrai worker, le vrai cache de scène, le vrai service de production :

| Vérification | Résultat attendu (test) |
|---|---|
| V1, deux scènes parlées | 2 appels de synthèse ; coût annoncé = caractères des deux répliques |
| V2, réplique de la scène 2 modifiée | **1 seul appel** (la nouvelle réplique) ; scène 1 reprise du cache, scène 2 rendue ; repères et sous-titres de la scène 2 = nouvelle réplique ; empreinte de voix de la scène 1 inchangée |
| V2 : accord sur le coût | l'écran exige la case d'accord (« N caractère(s) à synthétiser ») ; **l'aperçu rapide est refusé** tant qu'une voix est à payer |
| V3, cadrage de la scène 1 modifié | **0 appel** ; coût 0 € ; aperçu permis (gratuit) ; scène 1 rendue, scène 2 reprise ; empreintes de voix identiques à la V2 |
| Cache de voix vidé, plan « sans réplique modifiée » | l'accord est quand même exigé (le coût vient des caractères réellement facturables, pas du plan) |
| Fiche Voltix sans voix ElevenLabs | lancement refusé au préflight, aucun appel |
| Pilote DEV sans moteur joignable (cas du compose livré) | échec clair « Voix ElevenLabs : … injoignable », aucun rendu, aucune vidéo muette |
| Moteur sans clé ElevenLabs (HTTP 400) | échec à la scène 1, un seul appel au moteur, aucun rendu |
| Quota dépassé à la scène 2 | échec clair ; la scène 1 déjà payée reste en cache : le nouvel essai ne paie que la scène 2 |
| Outil `python -m lody.animation.recette verify` | PASS sur les dossiers V1/V2/V3 ; FAIL si les versions sont inversées |

## 2. Prérequis DEV — audit (lecture seule)

| Point | État constaté | Ce qu'il faut |
|---|---|---|
| Image et compose `lody-dev-2d` (#154/#156) | Livrés ; volume `lody-dev-2d-data`, port `127.0.0.1:8611`, `LODY_REMOTION_EVALUATION=1` | Construire les deux images (§ 4) |
| Licence Remotion | Vide → rendu permis en **évaluation DEV** seulement | Rien pour la recette ; décision à prendre avant toute production |
| **Accès au moteur qui détient la clé ElevenLabs** | **Absent** : le pilote ne rejoint pas le réseau `moneyprinterturbo_default` et n'a pas `LODY_ENGINE_URL` ; la synthèse passe par `POST /api/v1/voice_preview` du moteur | **Décision du propriétaire** : surcharge temporaire, non versionnée (§ 4.2). Sans elle, la recette s'arrête proprement à « moteur injoignable » (testé) |
| Clé ElevenLabs | Dans le `config.toml` du moteur, jamais dans Lody ni dans le pilote | Rien à copier. Ne rien ajouter au pilote |
| Fiche « Voltix » avec voix ElevenLabs | Le volume DEV est vierge : aucune fiche | Créer la fiche **avant** d'installer la démo (§ 4.3) |
| Lien asset « Voltix — marionnette » → fiche | Fait **uniquement à l'import du catalogue**, si une fiche « Voltix » existe déjà ; aucun écran ne permet de le refaire après | Respecter l'ordre du § 4.3, sinon la vidéo est annoncée muette (« Voix : désactivée ») |
| Assets du jeu de test | Ceux de la démo #150, présents dans l'image | « Installer la démo Voltix #150 » puis importer `storyboard-v1.json` |
| Tarif pour l'estimation en € | `pricing.toml` absent du volume DEV → total « partiel » | Facultatif : déposer `/data/pricing.toml` (§ 4.4) ; l'estimation en **caractères** est toujours affichée |
| Préflight du worker | Ne contrôle ni la voix ni le moteur | La voix est contrôlée par « Préparer la génération » (ligne « Voix ») |

## 3. Coût prévisionnel, plafond et autorisation

**Estimation** (unité de facturation ElevenLabs = caractère du texte envoyé ; calculée sans appel par
`python -m lody.animation.recette estimate`) :

| Étape | Appels | Caractères | Textes |
|---|---|---|---|
| V1 | 2 | 128 | « Salut, moi c'est Voltix ! Aujourd'hui : le câble SDI. » ; « Le signal vidéo voyage dans un câble coaxial… … d'une impédance de 75 ohms. » |
| V2 | 1 | 78 | « Le signal vidéo voyage dans un câble coaxial… … et son impédance vaut 75 ohms. » |
| V3 | 0 | 0 | — |
| **Total** | **3** | **206** | |

- **Pire cas : 618 caractères.** Le moteur relance jusqu'à 3 fois un appel en échec (5xx, exception) et une
  réponse 200 dont l'audio serait illisible côté moteur est facturée puis relancée (`app/services/voice.py`).
- Un 401/403/422 n'est pas relancé ; un quota dépassé arrête tout à la première scène en erreur.
- **Plafond proposé : 1 000 caractères pour toute la recette** (≈ 1 000 crédits avec `eleven_multilingual_v2`,
  1 crédit/caractère). En euros : le prix du crédit dépend de l'abonnement ElevenLabs du propriétaire — ordre de
  grandeur **< 0,50 €** au tarif public des offres payantes, **à confirmer sur le tableau de bord**. C'est une
  estimation, jamais une facture.
- **Facturation réelle** : seul le tableau de bord ElevenLabs (*Usage* / historique) fait foi. Relever le solde de
  crédits avant et après chaque étape.
- Les aperçus de casting (#153) ne font pas partie de la recette : la voix de Voltix est **recopiée** depuis la
  fiche de production (aucun appel).

**Étape d'autorisation (avant tout appel réel)** : le propriétaire commente le ticket avec, mot pour mot ou
équivalent : *« J'autorise la recette #187 : plafond 1 000 caractères ElevenLabs, voix `<voice_id>` de Voltix,
pilote DEV relié au moteur pour la durée du test. »* Sans ce commentaire, ne pas appliquer le § 4.2. Si le compteur
dépasse le plafond, arrêter (§ 6) et ne rien relancer.

## 4. Commandes du propriétaire (DEV uniquement)

### 4.1 Construire et vérifier le pilote (gratuit)

```bash
git switch main && git pull
docker build -f Dockerfile.lody -t lody-video-factory:local .
docker compose -f docker-compose.lody-dev-2d.yml build
docker compose -f docker-compose.lody-dev-2d.yml run --rm lody-dev-2d python -m lody.animation.preflight
# attendu : aucun ERREUR ; licence en « évaluation DEV » ; isolement OK
```

### 4.2 Relier le pilote au moteur — seulement après l'autorisation écrite

Fichier **local, non versionné**, à supprimer après la recette : `docker-compose.lody-dev-2d.voice.yml`

```yaml
services:
  lody-dev-2d:
    environment:
      LODY_ENGINE_URL: http://moneyprinterturbo-api:8080
    networks: [default, engine]
networks:
  engine:
    name: moneyprinterturbo_default
    external: true
```

```bash
docker compose -f docker-compose.lody-dev-2d.yml -f docker-compose.lody-dev-2d.voice.yml up -d
ssh -L 8611:127.0.0.1:8611 <hôte>       # puis http://127.0.0.1:8611
```

Effet : le pilote peut demander des synthèses **payantes** au moteur de production (seul l'endpoint
`voice_preview` est utilisé par le rendu 2D). Aucune clé n'entre dans le pilote ; la production n'est ni modifiée ni
redémarrée. Le préflight reste « Aucun secret visible ».

### 4.3 Préparer le projet de recette (gratuit, dans cet ordre)

1. Créer un projet « Recette 187 » ; **Paramètres → Général → Mode de rendu : Animation 2D**.
2. **Personnages → Nouveau** : nom exactement `Voltix`, voix **ElevenLabs**, `voice_id` recopié de la fiche Voltix
   de production (saisie manuelle — aucun appel), mêmes réglages de direction de voix.
3. **Nouvelle production → Atelier 2D → « Installer la démo Voltix #150 »** : le message ne doit **pas** contenir
   « personnage « Voltix » absent du projet, lien non repris ».
4. Importer `docs/animation/recette-187/storyboard-v1.json` comme storyboard (2 scènes : `accueil`, `coaxial`).
5. Sans pricing : facultatif § 4.4. Puis passer au protocole.

### 4.4 Tarif pour l'estimation en euros (facultatif, gratuit)

```bash
docker compose -f docker-compose.lody-dev-2d.yml exec lody-dev-2d sh -c \
  'printf "currency = \"EUR\"\n[voice]\nelevenlabs = <prix pour 1000 caractères>\n" > /data/pricing.toml'
```

## 5. Protocole et critères PASS / FAIL

Avant chaque étape : relever le solde ElevenLabs. Après chaque étape : le relever à nouveau.

| Étape | Action | PASS | FAIL |
|---|---|---|---|
| V1 | « Préparer la génération » | Ligne **Voix : prête** — « 2 scène(s) parlée(s) : 0 déjà en cache, 128 caractère(s) à synthétiser » (± quelques caractères si un lexique #163 est actif) | Voix « désactivée » ou « à configurer » → revoir § 4.3 |
| V1 | Confirmer, attendre la fin (≈ 2 min) | Vidéo parlée par la voix de Voltix ; bouche qui bouge pendant la parole ; sous-titres = répliques ; solde −128 à −384 | Vidéo muette, autre voix, échec |
| V2 | Suivi V1 → « Créer une nouvelle version » → scène 2 « Modifier » → *Texte dit* de la 2ᵉ réplique : `… et son impédance vaut 75 ohms.` → Enregistrer | Plan : scène 1 **réutilisée**, scène 2 **à recalculer**, voix « à régénérer » ; bandeau « 78 caractère(s) à synthétiser » et **case d'accord** ; « Générer la V2 » grisé tant qu'elle n'est pas cochée ; « Aperçu rapide » **refusé** (« n'appelle jamais un service payant ») | Bandeau « 0 € » ou aucune case → arrêter |
| V2 | Cocher, « Générer la V2 » | Nouvelle réplique dite et sous-titrée ; scène 1 identique ; bloc « Réédition » : 1 scène réutilisée ; solde −78 à −234 | Scène 1 re-voisée, solde −206 ou plus |
| V3 | Depuis la V2 : « Créer une nouvelle version » → scène 1 → *Cadrage* : Plan serré → Enregistrer | Plan : scène 1 à recalculer, scène 2 réutilisée, voix « conservée » ; bandeau « Coût : 0 € », pas de case ; « Aperçu rapide » permis | Case d'accord ou caractères à synthétiser |
| V3 | « Générer la V3 » | Gros plan sur la scène 1, même voix ; scène 2 identique ; **solde ElevenLabs inchangé** | Toute consommation de crédits |

Contrôle automatique, gratuit, sur les fichiers produits (identifiants des trois versions dans l'adresse de leur
page de suivi) :

```bash
docker compose -f docker-compose.lody-dev-2d.yml exec lody-dev-2d \
  python -m lody.animation.recette verify --productions <prd_V1> <prd_V2> <prd_V3>
```

Attendu : uniquement des `[PASS]` (rendu terminé, voix ElevenLabs réelle, piste voix de la durée de la vidéo,
repères recalés et sous-titres, bouche dans la scène, contrôle qualité sans erreur ; V2 : seule la voix de la
scène 2 change et seule la scène 2 est recalculée ; V3 : empreintes de voix identiques à la V2, seule la scène 1
recalculée). Code de sortie 0 = PASS global.

## 6. Arrêt et retour à l'état initial

```bash
docker compose -f docker-compose.lody-dev-2d.yml -f docker-compose.lody-dev-2d.voice.yml down
rm docker-compose.lody-dev-2d.voice.yml        # le pilote redevient incapable d'appeler un fournisseur payant
```

Le volume `lody-dev-2d-data` garde les rendus et le cache de voix (un nouvel essai identique ne repaie rien).

## 7. `VOICE_REGENERATION_AVAILABLE` après #169

- **Fait vérifié** : la V2 re-voise réellement une réplique modifiée. La synthèse a lieu au lancement
  (`AnimationConnector.submit`), uniquement pour les textes parlés absents du cache de voix ; le worker ne fait
  aucun appel. Le drapeau vaut désormais `True`.
- **Corrigé par #187** : avant, l'écran V2 annonçait « PAS re-voisées… aucun coût » sans case d'accord, alors que
  « Générer la V2 » payait la synthèse ; et « Aperçu rapide », présenté comme gratuit, passait aussi par `submit`
  et pouvait payer. Désormais : accord exigé dès qu'un caractère est facturable (calcul réel, cache compris) ;
  aperçu refusé tant qu'une voix reste à payer.
- **Ce qui n'existe pas** : pas de bouton « re-voiser » séparé ; pas d'aperçu muet d'une réplique modifiée ;
  `manifest.audit.external_calls` reste à 0 (il ne compte que le worker, pas la synthèse faite au lancement).

## 8. Écarts relevés (non corrigés ici)

- Diagnostic : un refus du moteur (ex. « ElevenLabs is not configured », HTTP 400) s'affiche « paramètres
  invalides » ; la cause exacte n'est que dans le journal de Lody.
- Le préflight du worker ne vérifie ni l'accès au moteur ni la voix ; un pilote non relié échoue au lancement
  (proprement, sans rien payer).
- Aucun écran ne relie après coup un asset personnage à une fiche (seulement à l'import du catalogue).
- Le délai client de 60 s vers `voice_preview` est plus court que les 3 essais × 60 s possibles du moteur :
  un essai interrompu côté Lody peut encore être facturé côté moteur (couvert par le pire cas ×3).
- Aucun plafond de dépense n'est appliqué par le code : le plafond de la recette est une règle de procédure
  (estimation + autorisation écrite + relevé du solde).
