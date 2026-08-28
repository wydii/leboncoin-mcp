# leboncoin-mcp

Serveur [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) pour rechercher et consulter les annonces [Leboncoin](https://www.leboncoin.fr/) depuis un assistant IA.

## Crédits

Ce serveur MCP est basé sur la librairie Python **[lbc](https://github.com/etienne-hd/lbc)** par [etienne-hd](https://github.com/etienne-hd) — un client non-officiel pour l'API Leboncoin. Merci à lui pour le travail de reverse-engineering !

## Fonctionnalités

| Outil | Description |
|-------|-------------|
| `search_ads` | Rechercher des annonces (texte, catégorie, localisation, prix, tri…) |
| `get_ad` | Récupérer les détails complets d'une annonce |
| `get_user` | Consulter le profil d'un vendeur |
| `list_categories` | Lister les catégories disponibles |
| `list_regions` | Lister les régions françaises |
| `list_departments` | Lister les départements français |

## Installation

```bash
git clone https://github.com/wydii/leboncoin-mcp.git
cd leboncoin-mcp

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Utilisateurs Nix :** un `shell.nix` est fourni — `nix-shell` configure le `LD_LIBRARY_PATH` et active le venv automatiquement.

## Utilisation

### Transport stdio (par défaut)

```bash
python src/server.py
```

C'est le mode utilisé par les clients MCP comme Cursor, Claude Desktop, etc.

### Transport SSE (HTTP)

```bash
python src/server.py --sse              # port 3001 par défaut
python src/server.py --sse --port=8080  # port personnalisé
```

### Docker

```bash
docker build -t leboncoin-mcp .
docker run -i --rm leboncoin-mcp          # stdio
```

### Docker Compose (transport SSE)

```bash
docker compose up -d --build
```

Le serveur est alors joignable sur `http://localhost:3001/sse`. Arrêt : `docker compose down`.

## Configuration dans Cursor

Ajoutez ceci dans votre configuration MCP (`.cursor/mcp.json`) :

```json
{
  "mcpServers": {
    "leboncoin": {
      "command": "python",
      "args": ["/chemin/vers/leboncoin-mcp/src/server.py"]
    }
  }
}
```

## Exemples d'utilisation

Une fois le serveur connecté à votre assistant IA, vous pouvez lui demander :

- *« Cherche des vélos électriques à moins de 800€ en Île-de-France »*
- *« Montre-moi les appartements 3 pièces à Lyon »*
- *« Donne-moi les détails de l'annonce 2345678901 »*
- *« Quelles sont les catégories disponibles sur Leboncoin ? »*

## Référence des outils

### `search_ads`

| Paramètre | Type | Description |
|-----------|------|-------------|
| `text` | string | Texte de recherche |
| `url` | string | URL Leboncoin complète (remplace text/category/location) |
| `category` | string | Catégorie (`VEHICULES`, `IMMOBILIER`, `ELECTRONIQUE`, `LOISIRS`, `MODE`…) |
| `city` | string | Nom de ville (informatif, utilisé avec lat/lng) |
| `latitude` | float | Latitude |
| `longitude` | float | Longitude |
| `radius` | int | Rayon en mètres (défaut : 30 000 = 30 km) |
| `region` | string | Région (`ILE_DE_FRANCE`, `BRETAGNE`, `PROVENCE_ALPES_COTE_D_AZUR`…) |
| `department` | string | Département (`PARIS`, `GIRONDE`, `BOUCHES_DU_RHONE`…) |
| `price_min` | int | Prix minimum en euros |
| `price_max` | int | Prix maximum en euros |
| `sort` | string | Tri : `NEWEST`, `OLDEST`, `CHEAPEST`, `EXPENSIVE`, `RELEVANCE` |
| `ad_type` | string | `OFFER` ou `DEMAND` |
| `owner_type` | string | `PRO`, `PRIVATE` ou `ALL` |
| `shippable` | bool | Filtrer les articles livrables uniquement |
| `page` | int | Numéro de page (à partir de 1) |
| `limit` | int | Résultats par page (max 35) |

### `get_ad`

| Paramètre | Type | Description |
|-----------|------|-------------|
| `ad_id` | string | ID numérique de l'annonce (depuis l'URL) |

### `get_user`

| Paramètre | Type | Description |
|-----------|------|-------------|
| `user_id` | string | ID utilisateur (format UUID) |

### `list_categories` / `list_regions` / `list_departments`

Aucun paramètre requis.

## Dépendances

- [lbc](https://github.com/etienne-hd/lbc) >= 1.1.2 — Client Python pour Leboncoin
- [FastMCP](https://github.com/jlowin/fastmcp) >= 3.2.0 — Framework pour serveurs MCP en Python

## Licence

[MIT](./LICENSE)
