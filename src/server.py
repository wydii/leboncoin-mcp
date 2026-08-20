import logging
from typing import Optional

import lbc
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("leboncoin-mcp")

mcp = FastMCP("leboncoin", instructions=(
    "MCP server for searching Leboncoin (French classifieds). "
    "Use search_ads to find listings, get_ad for details on a specific ad, "
    "and get_user for seller info. "
    "Locations can be a city (lat/lng/radius), a region name, or a department name. "
    "Categories map to Leboncoin sections (VEHICULES, IMMOBILIER, ELECTRONIQUE, etc.)."
))

_client = lbc.Client()

CATEGORY_MAP = {item.name: item for item in lbc.Category}
SORT_MAP = {item.name: item for item in lbc.Sort}
AD_TYPE_MAP = {item.name: item for item in lbc.AdType}
OWNER_TYPE_MAP = {item.name: item for item in lbc.OwnerType}
REGION_MAP = {item.name: item for item in lbc.Region}
DEPARTMENT_MAP = {item.name: item for item in lbc.Department}


def _ad_to_dict(ad: lbc.Ad) -> dict:
    attrs = {}
    for a in ad.attributes.values():
        label = a.key_label or a.key
        attrs[label] = a.value_label or a.value

    loc = ad.location
    return {
        "id": ad.id,
        "title": ad.subject,
        "price": ad.price,
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
) -> list | None:
    locations = []
    if latitude is not None and longitude is not None:
        locations.append(lbc.City(
            lat=latitude,
            lng=longitude,
            radius=radius or 30_000,
            city=city or "",
        ))
    if region:
        region_key = region.upper()
        if region_key not in REGION_MAP:
            raise ValueError(f"Unknown region '{region}'. Use list_regions() to see valid options.")
        locations.append(REGION_MAP[region_key])
    if department:
        department_key = department.upper()
        if department_key not in DEPARTMENT_MAP:
            raise ValueError(f"Unknown department '{department}'. Use list_departments() to see valid options.")
        locations.append(DEPARTMENT_MAP[department_key])
    return locations or None


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
    sort: str = "NEWEST",
    ad_type: str = "OFFER",
    owner_type: Optional[str] = None,
    shippable: Optional[bool] = None,
    page: int = 1,
    limit: int = 10,
) -> dict:
    """Search for ads on Leboncoin.

    Args:
        text: Search query (e.g. "vélo électrique", "appartement 3 pièces").
        url: Full Leboncoin search URL. Overrides text/category/location params.
        category: Category name like VEHICULES, IMMOBILIER, ELECTRONIQUE, LOISIRS, MODE, etc.
            Full list: TOUTES_CATEGORIES, EMPLOI, VEHICULES, VEHICULES_VOITURES, VEHICULES_MOTOS,
            IMMOBILIER, IMMOBILIER_VENTES_IMMOBILIERES, IMMOBILIER_LOCATIONS, ELECTRONIQUE,
            MAISON_ET_JARDIN, MODE, LOISIRS, ANIMAUX, SERVICES, DONS, DIVERS.
        city: City name (informational, used with lat/lng).
        latitude: Latitude for location search.
        longitude: Longitude for location search.
        radius: Search radius in meters (default 30000 = 30km).
        region: Region name (e.g. ILE_DE_FRANCE, BRETAGNE, PROVENCE_ALPES_COTE_D_AZUR).
        department: Department name (e.g. PARIS, GIRONDE, BOUCHES_DU_RHONE).
        price_min: Minimum price in euros.
        price_max: Maximum price in euros.
        sort: Sort order: NEWEST, OLDEST, CHEAPEST, EXPENSIVE, RELEVANCE.
        ad_type: OFFER or DEMAND.
        owner_type: PRO, PRIVATE, or ALL.
        shippable: Filter for shippable items only.
        page: Page number (starts at 1).
        limit: Results per page (max 35).
    """
    kwargs = {}

    if url:
        kwargs["url"] = url
    else:
        if text:
            kwargs["text"] = text
        if category:
            category_key = category.upper()
            if category_key not in CATEGORY_MAP:
                raise ValueError(f"Unknown category '{category}'. Use list_categories() to see valid options.")
            kwargs["category"] = CATEGORY_MAP[category_key]

        locations = _build_location(city, latitude, longitude, radius, region, department)
        if locations:
            kwargs["locations"] = locations

    price = None
    if price_min is not None or price_max is not None:
        price = [price_min or 0, price_max or 999_999_999]
    if price:
        kwargs["price"] = price

    sort_key = sort.upper()
    if sort_key not in SORT_MAP:
        raise ValueError(f"Unknown sort '{sort}'. Valid options: {', '.join(SORT_MAP)}.")
    kwargs["sort"] = SORT_MAP[sort_key]

    ad_type_key = ad_type.upper()
    if ad_type_key not in AD_TYPE_MAP:
        raise ValueError(f"Unknown ad_type '{ad_type}'. Valid options: {', '.join(AD_TYPE_MAP)}.")
    kwargs["ad_type"] = AD_TYPE_MAP[ad_type_key]

    if owner_type:
        owner_type_key = owner_type.upper()
        if owner_type_key not in OWNER_TYPE_MAP:
            raise ValueError(f"Unknown owner_type '{owner_type}'. Valid options: {', '.join(OWNER_TYPE_MAP)}.")
        kwargs["owner_type"] = OWNER_TYPE_MAP[owner_type_key]
    if shippable is not None:
        kwargs["shippable"] = shippable
    kwargs["page"] = page
    kwargs["limit"] = min(limit, 35)
    kwargs["limit_alu"] = 0  # désactive les annonces sponsorisées ("à la une")

    try:
        result = _client.search(**kwargs)
    except Exception as exc:
        logger.exception("search_ads failed with kwargs=%s", kwargs)
        raise RuntimeError(f"Leboncoin search failed: {exc}") from exc

    return {
        "total": result.total,
        "total_pro": result.total_pro,
        "total_private": result.total_private,
        "max_pages": result.max_pages,
        "page": page,
        "ads": [_ad_to_dict(ad) for ad in result.ads],
    }


@mcp.tool()
def get_ad(ad_id: str) -> dict:
    """Get detailed information about a specific Leboncoin ad.

    Args:
        ad_id: The Leboncoin ad ID (numeric string from the ad URL).
    """
    try:
        ad = _client.get_ad(ad_id)
    except Exception as exc:
        logger.exception("get_ad failed for ad_id=%s", ad_id)
        raise RuntimeError(f"Could not retrieve ad '{ad_id}': {exc}") from exc
    result = _ad_to_dict(ad)
    result["favorites"] = ad.favorites
    return result


@mcp.tool()
def get_user(user_id: str) -> dict:
    """Get information about a Leboncoin user/seller.

    Args:
        user_id: The Leboncoin user ID (UUID format).
    """
    try:
        user = _client.get_user(user_id)
    except Exception as exc:
        logger.exception("get_user failed for user_id=%s", user_id)
        raise RuntimeError(f"Could not retrieve user '{user_id}': {exc}") from exc
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
        mcp.run(transport="sse", host="0.0.0.0", port=port)
    else:
        mcp.run()
