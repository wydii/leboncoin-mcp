import logging
from typing import Optional

import lbc
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("leboncoin-mcp")

mcp = FastMCP("leboncoin", instructions=(
    "MCP server to search and browse listings on Leboncoin, the largest French "
    "classifieds marketplace (second-hand goods, real estate, vehicles, jobs, services).\n\n"
    "Available tools:\n"
    "- search_ads: find listings by keyword, category, location, price and more. "
    "This is the main entry point; it returns a paginated list of ads with their id, "
    "title, price, location and a short body.\n"
    "- get_ad: fetch the full detail of one ad (all attributes, images, favorites) "
    "using an ad id obtained from search_ads.\n"
    "- get_user: fetch a seller's public profile (pro/private, ratings, number of ads).\n"
    "- list_categories / list_regions / list_departments: enumerate the exact enum "
    "values accepted by search_ads. Call these first when unsure about a category, "
    "region or department name.\n\n"
    "Guidance:\n"
    "- Location can be set three ways: a city point (latitude + longitude + radius), "
    "a region name, or a department name. City coordinates and region/department can be "
    "combined; if you only know a city name without coordinates, prefer its department "
    "or region.\n"
    "- Enum-like parameters (category, region, department, sort, ad_type, owner_type) are "
    "case-insensitive but must match a known value, otherwise the call fails with an "
    "explicit error listing the valid options.\n"
    "- Prices are in euros. Results default to newest first."
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
    """Search for classified ads on Leboncoin and return a paginated list of results.

    This is the primary discovery tool. It returns, for each ad, its id (usable with
    get_ad), title, price in euros, location, seller type, a short body and images.
    The response also includes total match counts and the number of available pages.

    Location can be specified in three interchangeable ways:
      - a city point: latitude + longitude (+ optional radius in meters);
      - a region name (see list_regions);
      - a department name (see list_departments).
    City coordinates can be combined with a region or department.

    Args:
        text: Free-text search query (e.g. "vélo électrique", "appartement 3 pièces").
        url: Full Leboncoin search URL copied from the website. When provided, it takes
            precedence over text, category and location parameters.
        category: Category enum name. Case-insensitive. Common values: VEHICULES,
            IMMOBILIER, ELECTRONIQUE, LOISIRS, MODE, MAISON_ET_JARDIN, ANIMAUX, SERVICES.
            Full list: TOUTES_CATEGORIES, EMPLOI, VEHICULES, VEHICULES_VOITURES,
            VEHICULES_MOTOS, IMMOBILIER, IMMOBILIER_VENTES_IMMOBILIERES,
            IMMOBILIER_LOCATIONS, ELECTRONIQUE, MAISON_ET_JARDIN, MODE, LOISIRS, ANIMAUX,
            SERVICES, DONS, DIVERS. Call list_categories() to enumerate every value.
        city: City name. Informational label only; on its own it does NOT filter results
            unless accompanied by latitude/longitude.
        latitude: Latitude of the search center. Must be paired with longitude.
        longitude: Longitude of the search center. Must be paired with latitude.
        radius: Search radius around the city point, in meters (default 30000 = 30 km).
        region: Region enum name, e.g. ILE_DE_FRANCE, BRETAGNE,
            PROVENCE_ALPES_COTE_D_AZUR. Case-insensitive. Call list_regions() for the full list.
        department: Department enum name, e.g. PARIS, GIRONDE, BOUCHES_DU_RHONE.
            Case-insensitive. Call list_departments() for the full list.
        price_min: Minimum price in euros (inclusive).
        price_max: Maximum price in euros (inclusive).
        sort: Result ordering. One of NEWEST (default), OLDEST, CHEAPEST, EXPENSIVE,
            RELEVANCE.
        ad_type: OFFER (someone selling, default) or DEMAND (someone looking to buy).
        owner_type: Restrict by seller type: PRO (professional), PRIVATE (individual),
            or ALL. Defaults to all sellers when omitted.
        shippable: If True, only return items that can be shipped (as opposed to
            pickup-only).
        page: 1-based page number to retrieve.
        limit: Number of results per page (max 35, values above are capped).

    Returns:
        A dict with total, total_pro, total_private, max_pages, the current page and an
        "ads" list. Use an ad's "id" with get_ad for full details.
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
    """Fetch the full detail of a single Leboncoin ad by its id.

    Use this after search_ads to get everything about one listing: title, price,
    complete description body, all category-specific attributes (brand, mileage,
    surface area, etc.), every image URL, location and the number of times it was
    favorited.

    Args:
        ad_id: The numeric Leboncoin ad id (the trailing number in an ad URL, also
            returned as "id" by search_ads).

    Returns:
        A dict describing the ad, including an "attributes" mapping and a "favorites"
        count. Raises if the ad does not exist or is no longer online.
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
    """Fetch a Leboncoin seller's public profile by user id.

    Use this to assess a seller found via search_ads or get_ad: whether they are a
    professional or a private individual, when they registered, how many ads they have
    online, their rating/feedback and, for pros, their store and business details.

    Args:
        user_id: The Leboncoin user id in UUID format (as returned by get_ad).

    Returns:
        A dict with the profile (name, is_pro, account_type, registered_at, total_ads),
        optional feedback_score/feedback_count and, for professionals, a "pro_info"
        block. Raises if the user cannot be found.
    """
    try:
        user = _client.get_user(user_id)
    except Exception as exc:
        logger.exception("get_user failed for user_id=%s", user_id)
        raise RuntimeError(f"Could not retrieve user '{user_id}': {exc}") from exc
    return _user_to_dict(user)


@mcp.tool()
def list_categories() -> dict:
    """List every Leboncoin category accepted by search_ads.

    Call this when you are unsure which category name to pass to search_ads' `category`
    parameter.

    Returns:
        A mapping of category enum name (the value to pass to search_ads) to its numeric
        Leboncoin id.
    """
    return {item.name: item.value for item in lbc.Category}


@mcp.tool()
def list_regions() -> list[str]:
    """List every French region name accepted by search_ads' `region` parameter.

    Call this to resolve a location to a valid region enum value before searching.

    Returns:
        A list of region enum names (e.g. ILE_DE_FRANCE, BRETAGNE).
    """
    return [item.name for item in lbc.Region]


@mcp.tool()
def list_departments() -> list[str]:
    """List every French department name accepted by search_ads' `department` parameter.

    Call this to resolve a location to a valid department enum value before searching.

    Returns:
        A list of department enum names (e.g. PARIS, GIRONDE, BOUCHES_DU_RHONE).
    """
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
