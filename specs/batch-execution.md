# Contrat technique du batch coopératif

BATCH exécute un graphe de tâches Codex pour un même compte Linux. Il requiert
Python 3.9 ou ultérieur et les primitives Linux du runner direct. Le runner
transmet les prompts sur stdin et utilise sa configuration Codex habituelle
pour le transport, l’authentification et les permissions.

Le manifeste et l’état n’acceptent que la version 2. Les commandes refusent
les manifestes et états v1 avec un diagnostic explicite et le code 2. Les
anciens fichiers restent en place, sans migration.

## Interface de commande

```text
bootstrap/orchestration/batch.sh run MANIFEST
bootstrap/orchestration/batch.sh status ID
bootstrap/orchestration/batch.sh resume ID
```

Le wrapper charge l’environnement commun, dont NVM s’il est présent, préserve
le répertoire appelant et passe les arguments au CLI Python.

- `run` valide et snapshotte le manifeste, acquiert la réservation, publie
  l’état initial, puis écrit `BATCH_ID=<id>` sur stdout avant le lancement
  des tâches. Cet identifiant est composé de 32 caractères hexadécimaux
  minuscules. Les sorties du runner suivent sur les flux habituels.
- `status` lit l’état et les reçus sans prendre le verrou projet ni modifier
  les fichiers. Sur succès, stdout contient un objet JSON et le code de
  sortie vaut `0` quel que soit le résultat du batch.
- `resume` recharge l’état du batch existant. Il relit l’état sous le
  verrou du batch et vérifie les reçus. Si des tâches restent en attente, il
  acquiert la même réservation de ressources que `run`. La reprise poursuit
  le graphe de tâches, sans reprendre de session Codex.

| Code | Signification |
| --- | --- |
| `0` | Toutes les tâches ont un succès confirmé pour `run` ou `resume`, ou la lecture par `status` a réussi. |
| `2` | Erreur d’arguments, de validation, de lecture de l’état ou autre erreur traitée par le CLI hors publication d’une tentative. |
| `75` | `BUSY` à l’admission lorsque la réservation ou l’exécution est déjà verrouillée. |
| `76` | `UNKNOWN` pour une interruption de la preuve de résultat ou une barrière qui bloque l’admission. |
| `129`, `130`, `143` | Interruption respectivement par HUP, INT ou TERM lorsque le superviseur peut rendre un résultat d’annulation ou s’arrêter entre tâches. |
| Code du reçu | Au premier échec confirmé, `run` et `resume` rendent le code de ce reçu. Un code processus négatif est converti en `128 + numéro du signal`. |

Les codes se recouvrent. Par exemple, Codex peut sortir avec `75` ou `76`
et produire un reçu `kind: "codex"` avec un résultat `failed`. Le diagnostic
d’admission et le résultat structuré précisent le contexte. Un SIGKILL adressé
au batch interrompt le processus sans code choisi ni finalisation par le CLI.

## Manifeste et admission

Le manifeste est un fichier régulier UTF-8 d’au plus 16 Mio. Son objet JSON
contient `version: 2`, un tableau non vide `tasks` et un
`max_parallel` facultatif, entier positif par défaut égal à 1. Chaque
tâche contient `id`, `project`, exactement un des champs `prompt` et
`prompt_file` et éventuellement `depends_on`. Cette liste vide par défaut
contient des identifiants distincts de tâches du même manifeste. L’ordre du
tableau fixe le rang et peut inclure des références vers des tâches de rang
supérieur.

Chaque identifiant est unique et respecte `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`.
Chaque prompt contient du texte UTF-8 avec au moins un caractère hors espace
et sans NUL. Un `prompt_file` doit être un fichier régulier UTF-8 d’au plus
1 Mio. Les clés JSON en double, les dépendances absentes et les cycles
sont refusés avant publication. Les chemins relatifs restent relatifs au répertoire du chemin
fourni pour le manifeste, même si ce chemin est un lien symbolique. Le
snapshot contient les prompts inline, les chemins canoniques, les dépendances
et les ressources par projet. Les changements ultérieurs du manifeste et
des fichiers de prompt sont sans effet sur le snapshot. Le snapshot sérialisé complet est limité à 16 Mio. Le runner vérifie la
configuration et l’identité des ressources avant l’exécution de chaque
tâche. La reprise vérifie aussi l’ensemble des ressources avant de reprendre
un batch avec des tâches en attente. Une dérive constatée par un superviseur
après l’intention produit un échec d’infrastructure, avant tout lancement
Codex de cette tâche.

## Ressources communes aux lanceurs

Le projet principal est RW. Le runner prend en compte tous les liens
symboliques des deux répertoires `~/.codex-starter-kit-repos-ro/` et
`~/.codex-starter-kit-repos-rw/`. Deux liens vers le même chemin canonique
désignent une seule ressource. RO prime entre déclarations externes. Une
déclaration RO du projet principal ou un lien invalide fait échouer
l’admission. Les ressources des batches doivent rester hors des arbres de
contrôle du batch et des verrous projet.

L’admission utilise des flock non bloquants, pris dans l’ordre des chemins
canoniques. Elle est tout-ou-rien pour les réservations. Une courte section
d’admission commune synchronise les vérifications de recouvrement et les
prises de verrous. Les entrées de coordination persistent dans le répertoire
privé de l’UID. RW exclut toute autre réservation de la ressource. RO autorise
des lecteurs coopératifs simultanés. Le runner direct réserve son ensemble
pendant la tâche. Le batch réserve l’union de ses ressources pendant toute
l’exécution, au mode le plus fort demandé pour chaque racine. Les
superviseurs héritent de cette réservation et l’arbitrage interne permet aux tâches dont les ressources sont compatibles de
s’exécuter en parallèle.

Les racines imbriquées dans une même réservation sont refusées. Les
réservations concurrentes de deux arbres qui se recouvrent s’excluent,
y compris lorsque les chemins lexicalement distincts ont une identité Linux
commune. Les chemins sous un autre arbre Git, les métadonnées Git indirectes
telles que les fichiers `.git` des worktrees liés, les montages de sous-arbre
sur le chemin et les points de montage à l’intérieur d’une ressource sont
refusés. Les changements de montage, les redirections Git non déclarées et
les déplacements de racine pendant l’exécution sortent du périmètre
coopératif. Tous les participants doivent employer cette implémentation,
le même répertoire de coordination et une vue commune des montages. Un
ancien marqueur d’incertitude sans racine identifiable est traité de façon
conservatrice jusqu’à son association à une racine ou à un redémarrage.

Chaque exécution publie une barrière durable par ressource avant les sondes
et le lancement. Une barrière RO individuelle peut coexister avec les autres
lecteurs tant que son superviseur détient le verrou de présence. Une
barrière abandonnée, illisible ou invalide fait refuser les admissions
suivantes sur les racines concernées. Un succès Codex ne suffit jamais à
lever une barrière. Le runner exige la preuve de fin des descendants.
Pour le mode d’inspection tmux du runner direct, il exige aussi la preuve
de quiescence du processus de la session. Le superviseur agit comme
sous-récolteur Linux et le constat noyau `ECHILD` établit la fin des
descendants. Une barrière valide d’un démarrage Linux antérieur peut être
levée à l’admission sous verrou. Une tentative dépourvue de reçu concluant
reste ambiguë. Les batches s’exécutent en mode direct. RO est une convention
de coopération. Les permissions du processus et les possibilités d’écriture
hors des ressources déclarées dépendent du runner et de l’environnement.

## Ordonnancement et signaux

Le parent prend les tâches prêtes par rang du manifeste et passe les tâches
dont les ressources sont incompatibles avec une tâche active. Une dépendance
doit avoir un reçu de succès validé avant que sa tâche dépendante puisse
être admise. Les dates de démarrage de Codex et l’ordre des notifications
peuvent différer de l’ordre des admissions.

`CODEX_STARTER_KIT_BATCH_HOST_MAX_PARALLEL` limite le nombre de superviseurs
de tâches simultanés sous le même UID et répertoire de coordination. Il
accepte un entier décimal de 1 à 999999999, avec 1 par défaut. Les batches actifs doivent
utiliser le même plafond, sinon la nouvelle admission renvoie `BUSY`.
Chaque superviseur détient un slot flock numéroté. Le parent attend les
slots occupés par sondage temporisé. Le nombre de tâches actives d’un batch
est aussi limité par son `max_parallel`. Le runner direct, les processus Codex
orphelins et les lanceurs non coopératifs sont hors de ce plafond. Le
runner direct partage les réservations et les barrières de ressources.

Chaque tâche admise démarre un processus Python superviseur distinct via
`subprocess`, avec son propre groupe de processus. Le runner direct reste
responsable des descendants et des notifications. Au premier échec observé,
le parent arrête les admissions et attend les tâches actives. Si plusieurs
résultats sont observables lors du même contrôle, ils sont traités dans
l’ordre d’admission. Le premier échec observé fixe le code de sortie. Sur
HUP, INT ou TERM, le parent cesse les admissions et transmet le signal reçu
à tous les superviseurs actifs. Un arrêt pendant l’initialisation peut laisser
une intention sans reçu. Après un SIGKILL du parent, les superviseurs déjà
lancés peuvent continuer et publier leurs reçus. Ils conservent le slot,
la réservation complète et le verrou du batch jusqu’à leur sortie. Un
superviseur tué peut laisser des descendants, une barrière et un reçu
manquant. Aucun processus Codex ne reçoit les verrous du batch.

## Preuves, status et reprise

L’état est stocké dans `~/.codex-starter-kit-batches/<id>/`. Les répertoires
privés ont le mode `0700`, les fichiers réguliers le mode `0600`. Tous
appartiennent à l’UID courant.

`state.json` contient `batch_id`, `manifest` et `resources` et reste
immuable. L’attestation `<batch_id>-<rang>.json.started` est l’intention
durable, publiée avant le lancement du superviseur. Le rang commence à 1.
Attestation et reçu immuables portent la version 2, l’identifiant de
tentative, l’identifiant de tâche et le projet canonique. Le reçu complet
n’est publié qu’après retour du runner. Le parent synchronise le répertoire
avant d’utiliser le reçu d’un superviseur terminé. La reprise le synchronise
aussi avant de prendre une décision. Chaque publication synchronise un
fichier temporaire du même répertoire, crée un lien atomique sans écraser
une destination existante, puis synchronise le répertoire. Le reçu comporte
un objet `result` avec `kind`, `exit_code`, `process_exit`, `signal`,
`stage` et `capture_failed`. Un succès exige un reçu concordant avec
l’attestation et un résultat `kind: "codex"` avec `exit_code: 0`.

Si le flux JSON Codex fournit un identifiant de session, le parseur peut
le conserver dans un fichier immuable
`<batch_id>-<rang>.json.session`. Cette métadonnée peut être visible même
sur une tentative `unknown` et ne participe jamais à la preuve du résultat.

`status` lit sans mutation et rend `version: 2`, `batch_id`, `status`
et les lignes `tasks` en ordre de manifeste, avec `id`, `project`,
`depends_on` et `status`. Les lignes avec intention ou preuve de tentative
ajoutent `attempt_id` et, lorsqu’ils sont validés et disponibles, `result`
et `session_id`. Le statut global prend le premier état présent dans
l’ordre `unknown`, `failed`, `pending`, `succeeded`. Une lecture peut
refléter des publications en cours. Les tâches actives apparaissent
`unknown` jusqu’à leur reçu concluant.

`resume` exclut les exécutions concurrentes du même batch. Il refuse toute
reprise en présence d’un résultat `unknown` (76), d’un échec confirmé (code
du reçu) ou d’une preuve de tentative incohérente avec ses dépendances (76).
Une intention sans reçu valide reste `unknown` même si aucun processus
Codex n’a démarré. Les succès sont sautés. Seules les tâches encore en
attente sont admissibles. Aucun résultat terminal et aucune tentative ambiguë
ne sont automatiquement rejoués.

## Notifications et périmètre des garanties

Le runner direct publie START et finalise END ou ERROR. Les tâches déjà
réussies et sautées à la reprise ne produisent aucune notification. La
livraison reste au mieux, notamment lors d’un crash. Une notification, le
marqueur de sortie sur stdout ou un fichier produit dans le projet ne peuvent
pas remplacer un reçu. Voir les [notifications](notifications.md) et le
[handoff](handoff.md).

Les garanties couvrent les lanceurs coopératifs et les descendants suivis par
le superviseur. Les écritures déléguées à des processus externes et celles
d’autres comptes ou hôtes échappent à cette coordination.

Les [tests CLI hors ligne](../tests/test_batch_cli.py) et les
[tests de persistance](../tests/test_batch_state.py) utilisent des états
temporaires et un exécutable Codex simulé pour les lancements.
