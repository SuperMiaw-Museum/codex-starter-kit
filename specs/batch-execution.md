# Contrat technique du batch coopératif

Le batch exécute une suite finie de tâches Codex dans l’ordre du manifeste, sur
une seule racine de projet et sous un même compte Linux. Le processus Python
du batch héberge le superviseur direct. Il transmet les prompts à Codex sur
stdin et conserve le transport, l’authentification et les permissions Codex
configurés pour le runner direct.

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
- `resume` recharge l’état du batch existant. Il acquiert la même réservation
  que `run`, puis relit l’état sous cette réservation avant de prendre une
  décision. La reprise porte sur la suite de tâches, sans reprise de session
  Codex.

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

## Manifeste et snapshot v1

Le manifeste est un fichier régulier encodé en UTF-8. L’objet JSON contient
exactement `version` (entier `1`), `project` (chemin non vide) et `tasks`
(tableau ordonné non vide). Les clés JSON en double sont refusées.

Chaque tâche contient exactement `id` et l’une des clés `prompt` ou
`prompt_file`. L’identifiant est unique dans le manifeste et respecte
`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. Un prompt inline ou lu depuis un fichier
doit contenir du texte UTF-8 encodable, comporter un caractère hors espace
et être dépourvu de NUL. Un `prompt_file` désigne un fichier régulier UTF-8
d’au plus 1 Mio. La lecture refuse notamment les FIFO. Le manifeste d’entrée
est limité à 16 Mio et l’état initial sérialisé est borné avant admission.

Un argument `MANIFEST` relatif est interprété depuis le répertoire appelant.
Les chemins relatifs dans le manifeste partent du répertoire du chemin fourni
pour `MANIFEST`, y compris lorsque celui-ci désigne un lien symbolique. Le
projet est résolu strictement en répertoire canonique, avec suivi des liens
symboliques. Les recouvrements avec les répertoires d’état batch et de
verrouillage projet sont refusés lors de l’admission. Le snapshot enregistre le
chemin canonique et tous les prompts sous forme inline, dans leur ordre
initial. Une modification ultérieure du manifeste ou des fichiers de prompt
reste sans effet sur la reprise. Le contenu du projet reste celui du système
de fichiers au moment de l’exécution.

## Réservation, descendants et barrière

Le verrou est partagé par les lanceurs coopératifs du même UID qui utilisent
le même répertoire de coordination. La clé est le SHA-256 du chemin canonique
exact. Les fichiers `.lock` et `.active` restent sur disque dans le répertoire
privé `/tmp/codex-starter-kit-project-locks-UID`. L’admission utilise
`flock(LOCK_EX | LOCK_NB)`. Deux alias symboliques de la même racine utilisent
la même clé.

`run` et `resume` détiennent la réservation `.lock` depuis l’admission
jusqu’à la sortie de la section réservée, y compris pendant les publications
de reçus et les intervalles entre tâches. Pour chaque tâche, le superviseur
vérifie le descripteur existant auprès de Linux et acquiert le verrou
d’exécution `.active` sans ouvrir une nouvelle réservation exclusive.
Codex et ses descendants ne reçoivent pas ces descripteurs dans le chemin
batch. La fermeture du descripteur de réservation libère le verrou.

Avant les sondes et le lancement Codex, le superviseur direct publie et
synchronise un fichier `.unknown` contenant l’identifiant du démarrage Linux.
Le superviseur agit comme sous-récolteur Linux et attend de façon bornée la
disparition de ses enfants, y compris les descendants orphelins qu’il adopte.
Le constat noyau `ECHILD` permet la suppression de la barrière. Si la
quiescence reste incertaine ou que le nettoyage échoue dans les cas traités
par le superviseur, la tentative renvoie `UNKNOWN`. Le PID observé et le code
de sortie de Codex sont insuffisants pour lever la barrière.

Une barrière du démarrage courant, illisible ou invalide bloque toute nouvelle
admission coopérative sur le projet. Une barrière valide d’un démarrage Linux
antérieur est levée à l’admission sous verrou. Cette levée laisse intact
l’état d’une tentative dépourvue de reçu concluant. La reprise ne comporte
aucun mécanisme de contournement de la barrière.

## Persistance et décisions de reprise

L’état par batch est stocké dans `~/.codex-starter-kit-batches/<id>/`. Les
répertoires privés appartiennent à l’UID courant et ont le mode `0700`. Les
fichiers d’état et de tentative sont des fichiers réguliers de mode `0600`
du même propriétaire. `state.json` contient `batch_id`, le snapshot
`manifest` et le nombre `started` de tâches pour lesquelles une intention
a été enregistrée.

Pour chaque tâche encore en attente, sous réservation continue, le batch
suit cet ordre.

1. Incrémenter et publier `started`.
2. Publier l’attestation de démarrage `<attempt_id>.json.started`. Elle
   identifie la tentative par sa version `1`, son `attempt_id` et son
   `project` canonique. L’identifiant de tentative combine l’identifiant du
   batch et le rang de la tâche à partir de 1, séparés par un tiret.
3. Appeler le runner direct, qui finalise sa tentative de notification et
   de vérification des descendants avant de revenir.
4. Publier le reçu `<attempt_id>.json` avec la même identité et un objet
   `result` contenant `kind`, `exit_code`, `process_exit`, `signal`,
   `stage` et `capture_failed`. Les champs de détail facultatifs peuvent
   valoir `null`. Un échec de capture est distinct du code Codex.
5. Relire le résultat validé avant de passer à la tâche suivante.

Chaque publication écrit un temporaire du même répertoire et synchronise
son contenu. Le fichier d’état courant est remplacé atomiquement. Les
attestations et reçus sont publiés par lien atomique sans écraser une
destination existante. Le répertoire est synchronisé après publication
réussie. Ces opérations rendent chaque fichier individuellement publiable
de façon atomique. Les écritures de Codex dans le projet, la notification,
l’état et le reçu restent des opérations distinctes.

`status` renvoie `version: 1`, `batch_id`, `project`, `status` et
`tasks`. Chaque ligne de tâche contient `id` et `status`. Une intention
enregistrée ajoute `attempt_id` et un reçu exploitable ajoute `result`.

| État d’une tâche | Critère et décision de reprise |
| --- | --- |
| `pending` | Rang au-delà de `started`. Le batch peut lancer la tâche après les succès précédents. |
| `succeeded` | Attestation et reçu valides et concordants, avec `kind: "codex"` et `exit_code: 0`. La reprise saute la tâche. |
| `failed` | Reçu valide et concluant avec un autre résultat que le succès. Le batch s’arrête et le même batch ne réessaie pas cette tâche, y compris après une annulation confirmée. |
| `unknown` | Intention enregistrée et reçu absent, illisible, invalide, sans attestation correspondante ou indiquant lui-même `unknown`. La reprise s’arrête avec `76` et ne relance pas cette tâche. |

L’état global est celui de la première tâche différente de `succeeded`, ou
`succeeded` si toutes le sont. Une tâche active dont le reçu reste à produire
apparaît donc `unknown`. Le rapport est une lecture sans réservation et peut
refléter une publication en cours. Si l’état principal est invalide ou si le
projet snapshoté ne se résout plus à son chemin canonique enregistré, le CLI
échoue avec `2`.

Une interruption avant la publication de l’intention laisse la tâche en
attente. Une interruption après cette publication et avant un reçu concluant
rend la tâche `unknown`, même avant le lancement effectif ou après la sortie
réussie de Codex. La reprise ne déduit aucun succès d’un fichier produit, d’une
notification ou du marqueur de sortie affiché sur stdout. Un reçu de succès
concluant permet de poursuivre sans rejouer la tâche. Il n’existe pas de
commande pour valider manuellement une tentative ambiguë ou réessayer une
tâche échouée dans le même batch.

## Signaux, notifications et périmètre des garanties

Le superviseur mémorise le premier HUP, INT ou TERM. Entre deux tâches, il
s’arrête au prochain contrôle sans lancer la suivante. Pendant une tâche, il
tente de transmettre le signal au groupe de processus Codex, puis envoie
SIGKILL au groupe après un délai de grâce si nécessaire. Un descendant qui
quitte ce groupe peut survivre à cette transmission. La vérification finale
de quiescence s’applique et `UNKNOWN` prime sur une annulation si la fin des
descendants reste incertaine. Un SIGKILL du superviseur peut laisser la
barrière, une intention sans reçu et une notification END manquante.

Le runner direct est seul responsable de START et de la finalisation
END ou ERROR. Le batch ne duplique pas ces notifications et n’en émet pas
pour une tâche sautée à la reprise. La livraison reste au mieux, notamment
en cas d’échec du registre ou de crash. Voir le
[contrat des notifications](notifications.md) pour les flux, le registre et
les métadonnées, et le [contrat de handoff](handoff.md) pour le runner direct.

La garantie d’exclusion suppose des lanceurs coopératifs, des chemins de
coordination intacts et des écritures effectuées dans l’arbre de processus
supervisé. Elle ne couvre ni les écritures déléguées à des processus externes,
ni les agents malveillants du même UID, ni les autres comptes ou hôtes. Les
worktrees et racines imbriquées ne bénéficient d’aucune coordination commune
entre chemins canoniques distincts. Les tâches sont toutes admises en mode
exclusif. Le batch ne prend en charge ni tmux, ni les lectures concurrentes,
ni les dérogations au verrou. Bubblewrap et le relais de modèle expérimental
sont hors du chemin MVP.

Les [tests CLI hors ligne](../tests/test_batch_cli.py) vérifient l’ordre, le
verrou entre tâches, les alias symboliques, le snapshot, les notifications et
les fenêtres de crash avant reçu. Les
[tests de persistance](../tests/test_batch_state.py) vérifient l’écriture
atomique, l’identité des reçus et les décisions en cas de reçu ambigu.
