# Prompts

Tous les prompts utilisés par les workflows, avec le modèle et les paramètres d'appel.
Les prompts vivent dans les nœuds n8n ; ce fichier en est la copie de référence versionnée.
Toute modification d'un prompt dans n8n doit être reportée ici (et le workflow ré-exporté dans `workflows/`).

## W1 — Classification d'un ticket (`Classify-Ticket`)

| | |
|---|---|
| Workflow | `W1 - Ticket Intake & AI Triage` |
| Nœud | `Classify-Ticket` (Basic LLM Chain) |
| Fournisseur | OpenRouter (credential `OpenRouter account`) |
| Modèle | `nvidia/nemotron-3-ultra-550b-a55b:free` (free tier) |
| Température | `0` — classification : on veut une sortie stable pour une même demande |
| `maxRetries` (modèle) | `3` — retries du client LangChain, avec backoff exponentiel |
| Timeout | `60000 ms` |
| Retry du nœud | `retryOnFail`, 3 essais, 5000 ms entre deux essais |
| En cas d'échec | `onError: continueErrorOutput` → `Set-Fallback-Triage` (catégorie/priorité par défaut, tag `manual_review`, `ai_error` dans AuditLog) |
| Parser | `Parse-Triage-Output` (Structured Output Parser, JSON Schema manuel) |

### Pourquoi une Basic LLM Chain et pas un agent

La classification est une tâche **déterministe à un seul appel** : une demande en entrée, un objet JSON en sortie.
Le modèle n'a besoin d'appeler aucun outil (le collaborateur est déjà chargé avant l'appel, le technicien est choisi
après par une requête Baserow). Un agent ajouterait une boucle de raisonnement, des appels imprévisibles et du coût
sans rien apporter. La chaîne + Structured Output Parser garantit une sortie conforme au schéma ou une erreur exploitable.

### Prompt système

```text
Tu es l'agent de triage du support IT interne de Northwind Industries (PME industrielle de 220 collaborateurs, deux sites et un tiers en télétravail).
Ta seule mission est de classer une demande de support. Tu ne réponds jamais au collaborateur.

Catégories possibles :
- hardware : poste de travail, périphériques, imprimantes, commande de matériel
- access : comptes, mots de passe, MFA, droits d'accès, authentification VPN
- software : installation, licences, bugs applicatifs, messagerie
- network : connectivité, Wi-Fi, réseau du site, connexion VPN instable
- onboarding : arrivée ou départ d'un collaborateur, préparation d'un poste
- other : tout ce qui ne rentre dans aucune catégorie ci-dessus

Priorités :
- P1 : bloque un site, une équipe entière ou la production, ou risque de sécurité
- P2 : bloque une personne sans solution de contournement
- P3 : gênant mais un contournement existe
- P4 : demande d'information, de confort ou planifiable

Règles :
- L'urgence déclarée par le collaborateur est un indice, pas une vérité : base-toi sur l'impact réel décrit.
- required_skill est la compétence technicien nécessaire, choisie parmi les mêmes valeurs que category.
- confidence est ta certitude entre 0 et 1. Baisse-la si la demande est vague, incomplète ou ambiguë.
- needs_human_review vaut true si la demande touche à la sécurité, à des données personnelles, à la direction, ou si elle est ambiguë.
- summary est un résumé neutre en français de 280 caractères maximum.
- N'invente aucune information absente de la demande.
```

### Prompt utilisateur (expression n8n)

```text
Demande à classer :

Sujet : {{ $('Normalize-Request').item.json.subject }}
Description : {{ $('Normalize-Request').item.json.description }}
Urgence déclarée : {{ $('Normalize-Request').item.json.declared_urgency }}

Demandeur : équipe {{ $('Fetch-Collaborator').item.json.team.value }}, rôle {{ $('Fetch-Collaborator').item.json.role.value }}, site {{ $('Fetch-Collaborator').item.json.site.value }}
```

### JSON Schema de sortie

```json
{
  "type": "object",
  "properties": {
    "category": {
      "type": "string",
      "enum": [
        "hardware",
        "access",
        "software",
        "network",
        "onboarding",
        "other"
      ],
      "description": "Catégorie du ticket"
    },
    "priority": {
      "type": "string",
      "enum": [
        "P1",
        "P2",
        "P3",
        "P4"
      ],
      "description": "Priorité, P1 = critique"
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1,
      "description": "Certitude du classement"
    },
    "summary": {
      "type": "string",
      "minLength": 1,
      "maxLength": 280,
      "description": "Résumé en français"
    },
    "required_skill": {
      "type": "string",
      "enum": [
        "hardware",
        "access",
        "software",
        "network",
        "onboarding",
        "other"
      ],
      "description": "Compétence technicien requise"
    },
    "needs_human_review": {
      "type": "boolean",
      "description": "Reprise humaine nécessaire"
    }
  },
  "required": [
    "category",
    "priority",
    "confidence",
    "summary",
    "required_skill",
    "needs_human_review"
  ],
  "additionalProperties": false
}
```

Le seuil d'acceptation de la sortie est `HELPDESK_AI_CONFIDENCE_THRESHOLD` (0.6 par défaut) : en dessous,
le ticket suit la même branche de repli qu'une erreur du modèle.
