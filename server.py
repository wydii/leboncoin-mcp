import difflib
import json
import threading
import unicodedata
from functools import lru_cache
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen

import lbc
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from lbc.exceptions import DatadomeError, LBCError, NotFoundError

mcp = FastMCP("leboncoin", instructions=(
    "MCP server for searching Leboncoin (French classifieds). "
    "Use search_ads to find listings, get_ad for details on a specific ad, "
    "and get_user for seller info (seller.id is returned with every ad). "
    "Locations can be a French city name (geocoded automatically), explicit "
    "lat/lng/radius, a region, or a department (name or number). "
    "Unknown cities, categories, regions or departments raise an error instead of "
    "being silently ignored. "
    "Categories map to Leboncoin sections (VEHICULES, IMMOBILIER, ELECTRONIQUE, etc.)."
))

# French government geocoders (IGN Geoplateforme, then the older BAN endpoint as a
# fallback): free, no key. Only the city name is sent.
GEOCODER_URLS = (
    "https://data.geopf.fr/geocodage/search",
    "https://api-adresse.data.gouv.fr/search/",
)
DEFAULT_RADIUS_M = 30_000

_client: Optional[lbc.Client] = None
_client_lock = threading.Lock()


def _get_client() -> lbc.Client:
    """Create the Leboncoin client on first use.

    lbc.Client() contacts leboncoin.fr as soon as it is built: doing it lazily lets
    the server start offline and avoids a request to Leboncoin at every session start.
    """
    global _client
    with _client_lock:
        if _client is None:
            try:
                _client = lbc.Client()
            except Exception as e:  # network down, DNS failure...
                raise ToolError(f"Could not reach Leboncoin: {e}") from e
        return _client


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except NotFoundError as e:
        raise ToolError("Not found on Leboncoin (ad removed or wrong id).") from e
    except DatadomeError as e:
        raise ToolError(
            "Blocked by Leboncoin's anti-bot protection (DataDome). "
            "Wait a while before sending more requests."
        ) from e
    except LBCError as e:
        raise ToolError(f"Leboncoin request failed: {e}") from e


def _norm(value: str) -> str:
    """'Haute-Garonne' -> 'HAUTE_GARONNE', 'Île-de-France' -> 'ILE_DE_FRANCE', '01' -> '1'."""
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    s = "".join(ch if ch.isalnum() else "_" for ch in s.upper())
    s = "_".join(filter(None, s.split("_")))
    return str(int(s)) if s.isdigit() else s


CATEGORY_MAP = {item.name: item for item in lbc.Category}
SORT_MAP = {item.name: item for item in lbc.Sort}
AD_TYPE_MAP = {item.name: item for item in lbc.AdType}
OWNER_TYPE_MAP = {item.name: item for item in lbc.OwnerType}
REGION_MAP = {item.name: item for item in lbc.Region}
DEPARTMENT_MAP = {item.name: item for item in lbc.Department}
DEPARTMENT_MAP.update({item.value[2]: item for item in lbc.Department})  # "31" -> HAUTE_GARONNE


def _lookup(kind: str, value: str, mapping: dict, list_tool: Optional[str] = None):
    key = _norm(value)
    if key in mapping:
        return mapping[key]
    names = [k for k in mapping if not k.isdigit()]
    msg = f"Unknown {kind}: {value!r}."
    suggestions = difflib.get_close_matches(key, names, n=3, cutoff=0.6)
    if suggestions:
        msg += f" Did you mean {', '.join(suggestions)}?"
    msg += f" See {list_tool}." if list_tool else f" Allowed: {', '.join(names)}."
    raise ToolError(msg)


@lru_cache(maxsize=256)
def _geocode_city(city: str) -> dict:
    query = urlencode({"q": city, "type": "municipality", "limit": 1})
    features, errors = None, []
    for base in GEOCODER_URLS:
        req = Request(f"{base}?{query}", headers={"User-Agent": "leboncoin-mcp"})
        try:
            with urlopen(req, timeout=5) as resp:
                features = json.load(resp).get("features", [])
            break
        except Exception as e:  # timeout, 5xx... try the next geocoder
            errors.append(str(e))
    if features is None:
        raise ToolError(
            f"Could not geocode city {city!r} ({'; '.join(errors)}). "
            "Pass latitude/longitude instead."
        )
    if not features or features[0]["properties"].get("score", 0) < 0.5:
        raise ToolError(
            f"Unknown French city: {city!r}. Add the postcode "
            "(e.g. 'Saint-Jean 31240') or pass latitude/longitude."
        )
    props = features[0]["properties"]
    lng, lat = features[0]["geometry"]["coordinates"]
    label = f"{props.get('label')} {props.get('postcode', '')} ({props.get('context', '')})"
    return {"label": " ".join(label.split()), "lat": lat, "lng": lng}


def _price(ad: lbc.Ad):
    if ad.price is not None:
        return ad.price
    # Some categories (e.g. holiday rentals) have no price_cents, only price: [n]
    raw = ad.raw.get("price")
    if isinstance(raw, list):
        return raw[0] if raw else None
    return raw if isinstance(raw, (int, float)) else None


def _ad_to_dict(ad: lbc.Ad) -> dict:
    attrs = {}
    for a in ad.attributes.values():
        label = a.key_label or a.key
        attrs[label] = a.value_label or a.value

    loc = ad.location
    owner = ad.raw.get("owner") or {}
    return {
        "id": ad.id,
        "title": ad.subject,
        "price": _price(ad),
        "url": ad.url,
        "category": ad.category_name,
        "ad_type": ad.ad_type,
        "body": ad.body,
        "images": ad.images or [],
        "first_publication_date": ad.first_publication_date,
        "location": {
            "city": loc.city_label,
            "zipcode": loc.zipcode,
            "department": loc.department_name,
            "region": loc.region_name,
            "lat": loc.lat,
            "lng": loc.lng,
        },
        "seller": {
            "id": owner.get("user_id"),
            "name": owner.get("name"),
            "type": owner.get("type"),
        },
        "attributes": attrs,
        "has_phone": ad.has_phone,
    }


def _user_to_dict(user: lbc.User) -> dict:
    result = {
        "id": user.id,
        "name": user.name,
        "is_pro": user.is_pro,
        "account_type": user.account_type,
        "registered_at": user.registered_at,
        "total_ads": user.total_ads,
        "description": user.description,
        "profile_picture": user.profile_picture,
    }
    if user.feedback and user.feedback.overall_score:
        result["feedback_score"] = user.feedback.score
        result["feedback_count"] = user.feedback.received_count
    if user.pro:
        result["pro_info"] = {
            "store_name": user.pro.online_store_name,
            "activity_sector": user.pro.activity_sector,
            "siren": user.pro.siren,
            "website": user.pro.website_url,
            "slogan": user.pro.slogan,
        }
    return result


def _build_location(
    city: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
    radius: Optional[int],
    region: Optional[str],
    department: Optional[str],
) -> tuple[list | None, dict | None]:
    """Return the lbc locations and a description of what was actually applied."""
    locations, applied = [], {}
    if (latitude is None) != (longitude is None):
        raise ToolError("latitude and longitude must be given together.")
    if latitude is None and city:
        geo = _geocode_city(city.strip())
        latitude, longitude = geo["lat"], geo["lng"]
        applied["city"] = geo["label"]
    if latitude is not None:
        r = radius or DEFAULT_RADIUS_M
        locations.append(lbc.City(lat=latitude, lng=longitude, radius=r, city=city or ""))
        applied.update(lat=latitude, lng=longitude, radius_m=r)
    elif radius is not None:
        raise ToolError("radius needs a city or latitude/longitude.")
    if region:
        reg = _lookup("region", region, REGION_MAP, "list_regions")
        locations.append(reg)
        applied["region"] = reg.name
    if department:
        dep = _lookup("department", department, DEPARTMENT_MAP, "list_departments")
        locations.append(dep)
        applied["department"] = dep.name
    return locations or None, applied or None


def _clean_search_url(url: str) -> str:
    """Decode a search URL copied from the browser before handing it to lbc.

    lbc splits the query string on '&' and '=' without URL-decoding it, so
    'text=v%C3%A9lo%20%C3%A9lectrique' returned 0 results, and it turns a
    'page' parameter into a bogus filter.
    """
    parts = urlsplit(url.strip())
    if not parts.netloc.endswith("leboncoin.fr") or not parts.query:
        raise ToolError(
            "url must be a Leboncoin search URL with parameters "
            "(https://www.leboncoin.fr/recherche?...)."
        )
    pairs = [
        (k, v.replace("&", " ").replace("=", " "))
        for k, v in parse_qsl(parts.query)
        if k != "page" and v != ""
    ]
    if not pairs:
        raise ToolError("The Leboncoin URL has no usable search parameter.")
    pairs.sort(key=lambda kv: kv[0] != "text")  # lbc needs 'text' before 'search_in'
    return "https://www.leboncoin.fr/recherche?" + "&".join(f"{k}={v}" for k, v in pairs)


@mcp.tool()
def search_ads(
    text: Optional[str] = None,
    url: Optional[str] = None,
    category: Optional[str] = None,
    city: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius: Optional[int] = None,
    region: Optional[str] = None,
    department: Optional[str] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    sort: str = "RELEVANCE",
    ad_type: str = "OFFER",
    owner_type: Optional[str] = None,
    shippable: Optional[bool] = None,
    page: int = 1,
    limit: int = 10,
) -> dict:
    """Search for ads on Leboncoin.

    Args:
        text: Search query (e.g. "vélo électrique", "appartement 3 pièces").
        url: Full Leboncoin search URL, as copied from the browser. Overrides every
            other filter (text, category, location, price, sort...); only page and
            limit still apply.
        category: Category name like VEHICULES, IMMOBILIER, ELECTRONIQUE, LOISIRS, MODE, etc.
            Main ones: TOUTES_CATEGORIES, EMPLOI, VEHICULES, VEHICULES_VOITURES, VEHICULES_MOTOS,
            VEHICULES_VELOS, IMMOBILIER, IMMOBILIER_VENTES_IMMOBILIERES, IMMOBILIER_LOCATIONS,
            ELECTRONIQUE, MAISON_ET_JARDIN, MODE, LOISIRS, ANIMAUX, SERVICES, DONS, DIVERS.
            Full list with list_categories. Unknown names raise an error.
        city: French city name, optionally with its postcode ("Toulouse", "Saint-Jean 31240").
            Geocoded automatically when latitude/longitude are not given; the resolved
            place is reported in applied_location.
        latitude: Latitude for location search (takes precedence over city geocoding).
        longitude: Longitude for location search.
        radius: Search radius in meters around the city or lat/lng (default 30000 = 30km).
        region: Region name (e.g. ILE_DE_FRANCE, OCCITANIE, "Île-de-France"). See list_regions.
        department: Department name or number (e.g. PARIS, HAUTE_GARONNE, "Haute-Garonne", "31").
            Several location filters (city, region, department) are combined with OR.
        price_min: Minimum price in euros.
        price_max: Maximum price in euros.
        sort: Sort order: RELEVANCE (default), NEWEST, OLDEST, CHEAPEST, EXPENSIVE.
        ad_type: OFFER or DEMAND.
        owner_type: PRO, PRIVATE, or ALL.
        shippable: Filter for shippable items only.
        page: Page number (starts at 1).
        limit: Results per page (1 to 35).
    """
    kwargs = {}
    applied_location = None

    if url:
        kwargs["url"] = _clean_search_url(url)
    else:
        if text:
            kwargs["text"] = text
        if category:
            kwargs["category"] = _lookup("category", category, CATEGORY_MAP, "list_categories")

        locations, applied_location = _build_location(
            city, latitude, longitude, radius, region, department
        )
        if locations:
            kwargs["locations"] = locations

        if price_min is not None or price_max is not None:
            kwargs["price"] = [
                price_min if price_min is not None else 0,
                price_max if price_max is not None else 999_999_999,
            ]

        kwargs["sort"] = _lookup("sort", sort, SORT_MAP)
        kwargs["ad_type"] = _lookup("ad_type", ad_type, AD_TYPE_MAP)
        if owner_type:
            kwargs["owner_type"] = _lookup("owner_type", owner_type, OWNER_TYPE_MAP)
        if shippable is not None:
            kwargs["shippable"] = shippable
        kwargs["limit_alu"] = 0

    page = max(1, page)
    kwargs["page"] = page
    kwargs["limit"] = max(1, min(limit, 35))

    result = _run(_get_client().search, **kwargs)

    response = {
        "total": result.total,
        "total_pro": result.total_pro,
        "total_private": result.total_private,
        "max_pages": result.max_pages,
        "page": page,
    }
    if applied_location:
        response["applied_location"] = applied_location
    response["ads"] = [_ad_to_dict(ad) for ad in result.ads]
    return response


@mcp.tool()
def get_ad(ad_id: str) -> dict:
    """Get detailed information about a specific Leboncoin ad.

    Args:
        ad_id: The Leboncoin ad ID (numeric string from the ad URL).
    """
    ad = _run(_get_client().get_ad, ad_id.strip())
    result = _ad_to_dict(ad)
    result["favorites"] = ad.favorites
    return result


@mcp.tool()
def get_user(user_id: str) -> dict:
    """Get information about a Leboncoin user/seller.

    Args:
        user_id: The seller's UUID, as returned in seller.id by search_ads and get_ad.
    """
    user = _run(_get_client().get_user, user_id.strip())
    return _user_to_dict(user)


@mcp.tool()
def list_categories() -> dict:
    """List all available Leboncoin categories and their names."""
    return {item.name: item.value for item in lbc.Category}


@mcp.tool()
def list_regions() -> list[str]:
    """List all available French regions for location filtering."""
    return [item.name for item in lbc.Region]


@mcp.tool()
def list_departments() -> list[str]:
    """List all available French departments for location filtering."""
    return [item.name for item in lbc.Department]


if __name__ == "__main__":
    import sys

    if "--sse" in sys.argv:
        port = 3001
        for arg in sys.argv:
            if arg.startswith("--port="):
                port = int(arg.split("=", 1)[1])
        # Local only: the SSE endpoint has no authentication.
        mcp.run(transport="sse", host="127.0.0.1", port=port)
    else:
        mcp.run()
