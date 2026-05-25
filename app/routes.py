from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    abort,
    request,
    make_response,
    Response
)

from app.models import Listing, Model, Specs, ThinkPadModel, PriceHistory, ModelPriceStats
from app import db
from sqlalchemy.orm import joinedload
from sqlalchemy import asc, desc, func
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
import re
from app.route_helpers import *

bp = Blueprint("main", __name__)


@bp.app_errorhandler(404)
def not_found_error(error):
    return render_template("404.html"), 404

@bp.app_errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template("500.html"), 500

@bp.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {request.url_root.rstrip('/')}{url_for('main.sitemap_xml')}",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap_xml():
    today = datetime.now(timezone.utc).date().isoformat()
    pages = []

    # --- Static pages ---
    static_endpoints = [
        ("main.index", {}),
        ("main.about", {}),
        ("main.methodology", {}),
        ("main.privacy", {}),
        ("main.terms", {}),
        ("main.contact", {}),
    ]

    for endpoint, values in static_endpoints:
        pages.append({
            "loc": url_for(endpoint, _external=True, **values),
            "lastmod": today,
            "changefreq": "weekly",
            "priority": "0.8" if endpoint == "main.index" else "0.5",
        })

    # --- Country pages ---
    for country in COUNTRY_FLAGS.keys():
        pages.extend([
            {"loc": url_for("main.deals", country=country, _external=True), "lastmod": today, "changefreq": "daily", "priority": "0.9"},
            {"loc": url_for("main.best_deals", country=country, _external=True), "lastmod": today, "changefreq": "daily", "priority": "0.8"},
            {"loc": url_for("main.price_drops", country=country, _external=True), "lastmod": today, "changefreq": "daily", "priority": "0.7"},
            {"loc": url_for("main.thinkpad_models", country=country, _external=True), "lastmod": today, "changefreq": "weekly", "priority": "0.7"},
        ])

    # --- Model pages ---
    # Include only models that have stats for the marketplace with at least 5 listings
    models = ThinkPadModel.query.order_by(ThinkPadModel.slug.asc()).all()
    for model in models:
        if not model.slug:
            continue
        for country in COUNTRY_FLAGS.keys():
            # Get stats for this model + marketplace
            stats = ModelPriceStats.query.join(ModelPriceStats.model).filter(
                ModelPriceStats.model_id == model.id,
                ModelPriceStats.marketplace == country,
                ModelPriceStats.listing_count >= 5
            ).first()

            if not stats:
                continue  # skip models with too few listings in this country

            lastmod = stats.updated_at.date().isoformat() if stats.updated_at else today

            pages.append({
                "loc": url_for("main.model_price", country=country, slug=model.slug, _external=True),
                "lastmod": lastmod,
                "changefreq": "daily",
                "priority": "0.8",
            })

    xml = render_template("sitemap.xml", pages=pages)
    return Response(
        xml,
        mimetype="application/xml",
        headers={"Cache-Control": "public, max-age=3600"}
    )

DEFAULT_COUNTRY = "us"

@bp.app_context_processor
def inject_site_globals():
    country = request.view_args.get("country") if request.view_args else None

    if not country:
        country = request.cookies.get("country", DEFAULT_COUNTRY)

    country = (country or DEFAULT_COUNTRY).lower()

    currency = CURRENCY_BY_COUNTRY.get(country, "USD")

    return {
        "country": country,
        "country_flags": COUNTRY_FLAGS,
        "currency": currency,
    }


@bp.after_app_request
def persist_country_cookie(response):
    if request.view_args and "country" in request.view_args:
        country = request.view_args["country"].lower()
        response.set_cookie("country", country, max_age=60 * 60 * 24 * 365)
    return response


@bp.app_template_filter("format_capacity")
def format_capacity(value):
    if value is None:
        return "—"
    
    try:
        value = float(value)  
    except (TypeError, ValueError):
        return str(value)

    if value >= 1024:
        tb = value / 1024
        return f"{tb:g}" + "TB"

    if value < 1:
        mb = value * 1024
        return f"{mb:g}" + "MB"

    return f"{value:g}" + "GB"


MARKETPLACE_TO_COUNTRY = {
    "EBAY_US": "us",
    "EBAY_AU": "au",
    "EBAY_DE": "de",
    "EBAY_GB": "gb",
}

@bp.app_context_processor
def inject_helpers():
    def country_for_item(item, current_country):
        if current_country != "all":
            return current_country
        return MARKETPLACE_TO_COUNTRY.get(item.marketplace, "us")

    return {
        "country_for_item": country_for_item
    }

@bp.app_template_filter("timeago")
def timeago(dt):
    if not dt:
        return ""

    # If datetime is naive, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc).astimezone(dt.tzinfo)
    diff = now - dt
    seconds = int(diff.total_seconds())

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} min ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hr ago"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    else:
        return dt.strftime("%d %b %Y")


# -------------------------------------------------
# Root → Redirect to preferred country
# -------------------------------------------------

@bp.route("/")
def index():
    preferred = request.cookies.get("country")
    valid_countries = set(get_enabled_markets().keys())

    if preferred:
        preferred = preferred.lower()
        if preferred in valid_countries:
            return redirect(url_for("main.country_home", country=preferred))

    return redirect(url_for("main.country_home", country="us"))

# -------------------------------------------------
# Country Home Page
# Example: /us/
# -------------------------------------------------


@bp.route("/<country>/")
def country_home(country):
    country, marketplaces, currency = get_country_context_or_404(country)

    sort = request.args.get("sort", "price")
    direction = request.args.get("direction", "asc")

    # Base query
    query = (
        Listing.query
        .join(Listing.model)
        .outerjoin(Listing.specs)
        .options(joinedload(Listing.model), joinedload(Listing.specs))
        .filter(
            Model.canon_model_id.isnot(None),
            Listing.status == "ACTIVE",
            Listing.marketplace.in_(marketplaces),
        )
    )

    # Apply spec filters
    for param, column in SPEC_FILTERS.items():
        value = request.args.get(param)
        if value not in (None, ""):
            # Convert numeric fields to float
            if param in ["ram", "storage"]:
                try:
                    value = float(value)
                except ValueError:
                    continue
            query = query.filter(column == value)

    # Build dropdown filters
    filters = {}
    for name, column in SPEC_FILTERS.items():
        values = (
            query.with_entities(column)
            .distinct()
            .order_by(column.asc().nullslast())
            .all()
        )

        filters[name] = []
        for v in values:
            val = v[0]
            if val is None:
                continue
            
            if name in ["ram", "storage"]:
                label = format_capacity(val)
            elif name == "storage_type":
                # Keep exactly "HDD", "SSD", "NVMe"
                label = str(val)
            else:  # model, cpu, etc.
                label = str(val).title()
            

            filters[name].append({
                "value": val,    # raw numeric or string
                "label": label   # display label
            })

        # Minimal change: sort storage_type dropdown
        if name == "storage_type":
            STORAGE_TYPE_ORDER = {"HDD": 0, "SSD": 1, "NVMe": 2}
            filters[name] = sorted(filters[name], key=lambda x: STORAGE_TYPE_ORDER.get(x["value"], 99))

    # Sorting
    SORT_COLUMNS = {
        "model": func.lower(Model.name),
        "price": Listing.price,
        "cpu": func.lower(Specs.cpu),
        "ram": Specs.ram,
        "storage": Specs.storage,
    }

    order_col = SORT_COLUMNS.get(sort, Listing.price)
    primary_sort = (desc(order_col) if direction == "desc" else asc(order_col)).nullslast()
    query = query.order_by(primary_sort, Listing.price.asc())

    # Pagination
    page = request.args.get("page", 1, type=int)
    per_page = 50
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    listings = pagination.items

    # Order filters consistently
    desired_order = ["model", "cpu", "ram", "storage", "storage_type"]
    filters_ordered = OrderedDict((key, filters.get(key, [])) for key in desired_order)

    return render_template(
        "listings.html",
        listings=listings,
        pagination=pagination,
        sort=sort,
        direction=direction,
        filters=filters_ordered,
    )


# -------------------------------------------------
# Model Page
# Example: /us/thinkpad-t480/
# -------------------------------------------------

@bp.route("/<country>/<model_slug>/")
def model_page(country, model_slug):
    country, marketplaces, currency = get_country_context_or_404(country)

    model = get_model_by_slug(model_slug)
    if not model:
        return render_template(
            "search.html",
            query=model_slug,
        )

    stats = canonical_model_stats(model.id, marketplaces)

    sort = request.args.get("sort", "price")
    direction = request.args.get("direction", "asc")
    
    query = active_listings_query_for_model(model.id, marketplaces)

    if sort == "price":
        order_col = Listing.price
    elif sort == "cpu":
        order_col = func.lower(Specs.cpu)
    elif sort == "ram":
        order_col = Specs.ram
    elif sort == "storage":
        order_col = Specs.storage
    else:
        order_col = Listing.price

    if direction == "desc":
        query = query.order_by(order_col.desc())
    else:
        query = query.order_by(order_col.asc())
        direction = "asc"

    
    page = request.args.get("page", 1, type=int)
    per_page = 50

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    listings = pagination.items

    if not listings:
        return render_template(
            "search.html",
            query=model.name,
        )

    return render_template(
        "model.html",
        listings=listings,
        pagination=pagination,
        sort=sort,
        direction=direction,
        model_slug=model.slug,
        model_name=model.name,
        stats=stats,
    )

# -------------------------------------------------
# Set Preferred Country Cookie
# -------------------------------------------------

@bp.route("/set-country/<country>/")
def set_country(country):
    country = country.lower()
    valid_countries = set(get_enabled_markets().keys())

    if country not in valid_countries:
        abort(404)

    response = make_response(
        redirect(url_for("main.country_home", country=country))
    )
    response.set_cookie("preferred_country", country, max_age=60 * 60 * 24 * 30)

    return response

# -----------------------------
# /<country>/deals
# -----------------------------

@bp.route("/<country>/deals")
def deals(country):
    country, marketplaces, currency = get_market_context(country)
    sort = request.args.get("sort", "price")

    # =========================================================
    # 1) BASE: active listings joined to parsed models
    #    Grain = one row per listing (assuming one Model row per listing)
    # =========================================================
    base_q = (
        db.session.query(
            Model.canon_model_id.label("canon_model_id"),
            Listing.id.label("listing_id"),
            Listing.ebay_item_id.label("ebay_item_id"),
            Listing.price.label("price"),
            Listing.first_seen.label("first_seen"),
        )
        .join(Listing, Listing.id == Model.listing_id)
        .filter(
            Model.canon_model_id.isnot(None),
            Listing.status == "ACTIVE",
            Listing.marketplace.in_(marketplaces),
        )
    ).subquery()

    # =========================================================
    # 2) MODEL SUMMARY: one row per canonical model
    #    Grain = one row per canonical model
    # =========================================================
    model_summary = (
        db.session.query(
            base_q.c.canon_model_id,
            func.min(base_q.c.price).label("cheapest_price"),
            func.count(func.distinct(base_q.c.listing_id)).label("listing_count"),
            func.max(base_q.c.first_seen).label("newest_listing"),
        )
        .group_by(base_q.c.canon_model_id)
        .subquery()
    )

    # =========================================================
    # 3) CHEAPEST ITEM ID per canonical model
    #    Correlated subquery: returns one ebay_item_id per model
    # =========================================================
    cheapest_item_subq = (
        db.session.query(base_q.c.ebay_item_id)
        .filter(base_q.c.canon_model_id == model_summary.c.canon_model_id)
        .order_by(base_q.c.price.asc(), base_q.c.first_seen.desc())
        .limit(1)
        .correlate(model_summary)
        .scalar_subquery()
    )

    # =========================================================
    # 4) MAIN QUERY: one row per canonical model
    # =========================================================
    query = (
        db.session.query(
            ThinkPadModel.id.label("canon_model_id"),
            ThinkPadModel.name.label("model_name"),
            ThinkPadModel.slug.label("slug"),
            model_summary.c.cheapest_price,
            model_summary.c.listing_count,
            model_summary.c.newest_listing,
            cheapest_item_subq.label("cheapest_item"),
        )
        .select_from(model_summary)
        .join(ThinkPadModel, ThinkPadModel.id == model_summary.c.canon_model_id)
    )

    # =========================================================
    # 5) BEST DEAL MODELS
    #    A model is a "best deal" if any active listing is < 75% of that
    #    model's current cheapest tracked baseline
    # =========================================================
    avg_subq = (
        db.session.query(
            base_q.c.canon_model_id,
            func.avg(base_q.c.price).label("avg_price"),
        )
        .group_by(base_q.c.canon_model_id)
        .subquery()
    )

    best_deal_rows = (
        db.session.query(base_q.c.canon_model_id)
        .join(avg_subq, avg_subq.c.canon_model_id == base_q.c.canon_model_id)
        .filter(base_q.c.price < avg_subq.c.avg_price * 0.75)
        .distinct()
        .all()
    )
    best_deal_model_ids = {row[0] for row in best_deal_rows}

    # =========================================================
    # 6) Price Drop logic for badges
    #==========================================================

    # after your base_q or main query
    price_history_max = (
        db.session.query(
            PriceHistory.listing_id.label("listing_id"),
            func.max(PriceHistory.price).label("historical_max_price"),
        )
        .group_by(PriceHistory.listing_id)
        .subquery()
    )

    price_drop_rows = (
        db.session.query(Model.canon_model_id)
        .join(Listing, Listing.id == Model.listing_id)
        .join(price_history_max, price_history_max.c.listing_id == Listing.id)
        .filter(
            Model.canon_model_id.isnot(None),
            Listing.status == "ACTIVE",
            Listing.marketplace.in_(marketplaces),
            Listing.price < price_history_max.c.historical_max_price,
        )
        .distinct()
        .all()
    )

    price_drop_model_ids = {row[0] for row in price_drop_rows}
    
    # =========================================================
    # 7) DEBUG (optional - remove later)
    # =========================================================
    # for row in rows:
    #     print(
    #         "[DEALS DEBUG]",
    #         row.canon_model_id,
    #         row.model_name,
    #         row.cheapest_price,
    #         row.listing_count,
    #         row.cheapest_item,
    #     )

    sort = request.args.get("sort", "cheapest_price")
    direction = request.args.get("direction", "asc")

    if direction not in ("asc", "desc"):
        direction = "asc"

    if sort == "model_name":
        sort_col = func.lower(ThinkPadModel.name)
    elif sort == "listing_count":
        sort_col = model_summary.c.listing_count
    else:  # default
        sort = "cheapest_price"
        sort_col = model_summary.c.cheapest_price

    if direction == "desc":
        query = query.order_by(desc(sort_col))
    else:
        query = query.order_by(asc(sort_col))

    # optional stable secondary sort
    #query = query.order_by(func.lower(ThinkPadModel.name))

    
    page = request.args.get("page", 1, type=int)
    per_page = 50

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = pagination.items

    return render_template(
        "deals.html",
        rows=rows,
        pagination=pagination,
        sort=sort,
        direction=direction,
        best_deal_model_ids=best_deal_model_ids,
        price_drop_model_ids=price_drop_model_ids,
    )


@bp.route("/<country>/price-drops")
def price_drops(country):
    country, marketplaces, currency = get_country_context_or_404(country)

    # Previous price per listing
    old_price = func.lag(PriceHistory.price).over(
        partition_by=PriceHistory.listing_id,
        order_by=(PriceHistory.recorded_at, PriceHistory.id)
    )

    # Join directly Listing → Model → ThinkPadModel
    price_changes_subq = (
        db.session.query(
            PriceHistory.listing_id.label("listing_id"),
            Listing.ebay_item_id.label("ebay_item_id"),
            Listing.item_url.label("item_url"),
            Listing.affiliate_url.label("affiliate_url"),
            PriceHistory.price.label("new_price"),
            old_price.label("old_price"),
            Listing.currency.label("currency"),
            ThinkPadModel.name.label("model_name"),
            ThinkPadModel.slug.label("slug"),
        )
        .join(Listing, Listing.id == PriceHistory.listing_id)
        .join(Model, Model.listing_id == Listing.id)
        .join(ThinkPadModel, ThinkPadModel.id == Model.canon_model_id)
        .filter(
            Listing.status == "ACTIVE",
            Listing.marketplace.in_(marketplaces),
        )
        .subquery()
    )

    rows = (
        db.session.query(
            price_changes_subq.c.model_name,
            price_changes_subq.c.slug,
            price_changes_subq.c.ebay_item_id,
            price_changes_subq.c.old_price,
            price_changes_subq.c.new_price,
            (price_changes_subq.c.old_price - price_changes_subq.c.new_price).label("drop_amount"),
            ((price_changes_subq.c.old_price - price_changes_subq.c.new_price) / price_changes_subq.c.old_price * 100).label("discount_percent"),
            price_changes_subq.c.currency,
            price_changes_subq.c.item_url,
            price_changes_subq.c.affiliate_url,
        )
        .filter(
            price_changes_subq.c.old_price.isnot(None),
            price_changes_subq.c.new_price < price_changes_subq.c.old_price,
        )
    )

    sort = request.args.get("sort", "lowest_price")
    direction = request.args.get("direction", "desc")
    
    # SQL-level sorting
    if sort == "model_name":
        order_col = price_changes_subq.c.model_name
    elif sort == "old_price":
        order_col = price_changes_subq.c.old_price
    elif sort == "new_price":
        order_col = price_changes_subq.c.new_price
    elif sort == "discount_percent":
        order_col = ((price_changes_subq.c.old_price - price_changes_subq.c.new_price) / price_changes_subq.c.old_price * 100)
    else:
        order_col = ((price_changes_subq.c.old_price - price_changes_subq.c.new_price) / price_changes_subq.c.old_price * 100)

    primary_sort = desc(order_col) if direction == "desc" else asc(order_col)

    rows = rows.order_by(primary_sort, (price_changes_subq.c.old_price - price_changes_subq.c.new_price).asc())
    page = request.args.get("page", 1, type=int)
    per_page = 50

    pagination = rows.paginate(page=page, per_page=per_page, error_out=False)
    rows = pagination.items

    return render_template(
        "price_drops.html",
        rows=rows,
        pagination=pagination,
        sort=sort,
        direction=direction,
    )


@bp.route("/<country>/best-deals")
def best_deals(country):
    country, marketplaces, currency = get_market_context(country)

    # =========================================================
    # 1) BASE: active listings with canonical model
    # =========================================================
    base_q = (
        db.session.query(
            Model.canon_model_id.label("canon_model_id"),
            Model.id.label("model_id"),
            Listing.id.label("listing_id"),
            Listing.ebay_item_id.label("ebay_item_id"),
            Listing.title.label("title"),
            Listing.price.label("price"),
            Listing.first_seen.label("first_seen"),
            Listing.item_url.label("item_url"),
            Listing.affiliate_url.label("affiliate_url"),
        )
        .join(Listing, Listing.id == Model.listing_id)
        .filter(
            Model.canon_model_id.isnot(None),
            Listing.status == "ACTIVE",
            Listing.marketplace.in_(marketplaces),
        )
    ).subquery()

    # =========================================================
    # 2) Average price per canonical model
    # =========================================================
    avg_subq = (
        db.session.query(
            base_q.c.canon_model_id,
            func.avg(base_q.c.price).label("avg_price"),
        )
        .group_by(base_q.c.canon_model_id)
        .subquery()
    )

    # =========================================================
    # 3) Build query (DO NOT call .all() yet)
    # =========================================================
    query = (
        db.session.query(
            base_q.c.listing_id,
            base_q.c.ebay_item_id,
            base_q.c.title,
            base_q.c.price,
            base_q.c.item_url,
            base_q.c.affiliate_url,
            avg_subq.c.avg_price,
            ThinkPadModel.id.label("canon_model_id"),
            ThinkPadModel.name.label("model_name"),
            ThinkPadModel.slug.label("slug"),
            ((avg_subq.c.avg_price - base_q.c.price) / avg_subq.c.avg_price).label("discount_ratio"),
            ((avg_subq.c.avg_price - base_q.c.price) / avg_subq.c.avg_price * 100).label("discount_percent"),
        )
        .join(avg_subq, avg_subq.c.canon_model_id == base_q.c.canon_model_id)
        .join(ThinkPadModel, ThinkPadModel.id == base_q.c.canon_model_id)
        .filter(base_q.c.price < avg_subq.c.avg_price * 0.85)
    )

    sort = request.args.get("sort", "discount")
    direction = request.args.get("direction", "desc")

    # SQL-level sorting
    if sort == "model":
        order_col = ThinkPadModel.name
    elif sort == "price":
        order_col = base_q.c.price
    elif sort == "avg_price":
        order_col = avg_subq.c.avg_price
    elif sort == "discount":
        order_col = ((avg_subq.c.avg_price - base_q.c.price) / avg_subq.c.avg_price * 100)
    else:
        order_col = ((avg_subq.c.avg_price - base_q.c.price) / avg_subq.c.avg_price * 100)

    primary_sort = desc(order_col) if direction == "desc" else asc(order_col)

    query = query.order_by(primary_sort, base_q.c.price.asc())

    page = request.args.get("page", 1, type=int)
    per_page = 50

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = pagination.items

    return render_template(
        "best_deals.html",
        rows=rows,
        pagination=pagination,
        sort=sort,
        direction=direction,
    )

@bp.route("/<country>/thinkpad_models")
def thinkpad_models(country):
    country, marketplaces, currency = get_country_context_or_404(country)

    rows = (
        db.session.query(
            ThinkPadModel.name.label("name"),
            ThinkPadModel.slug.label("slug"),
            func.max(Listing.last_seen).label("last_seen"),
        )
        .select_from(Listing)
        .join(Model, Model.listing_id == Listing.id)
        .join(ThinkPadModel, ThinkPadModel.id == Model.canon_model_id)
        .filter(
            Listing.status == "ACTIVE",
            Listing.marketplace.in_(marketplaces),
        )
        .group_by(ThinkPadModel.id, ThinkPadModel.name, ThinkPadModel.slug)
        .order_by(ThinkPadModel.name.asc())
        .all()
    )

    def get_series(name):
        if not name:
            return "Other"

        upper = name.upper()

        # Put X1 before X so it doesn't get caught by X
        if upper.startswith("X1"):
            return "X1"
        elif upper.startswith("T"):
            return "T"
        elif upper.startswith("X"):
            return "X"
        elif upper.startswith("P"):
            return "P"
        elif upper.startswith("L"):
            return "L"
        elif upper.startswith("E"):
            return "E"
        elif upper.startswith("W"):
            return "W"
        else:
            return "Other"

    grouped_models = defaultdict(list)
    for row in rows:
        grouped_models[get_series(row.name)].append(row)

    # Control display order
    series_order = ["T", "X", "X1", "P", "L", "E", "W", "Other"]

    return render_template(
        "thinkpad_models.html",
        grouped_models=grouped_models,
        series_order=series_order,
    )

@bp.route("/about")
def about():
    return render_template("about.html")


@bp.route("/how-it-works")
def methodology():
    return render_template("methodology.html")


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/terms")
def terms():
    return render_template("terms.html")


@bp.route("/contact")
def contact():
    return render_template("contact.html")

@bp.route("/affiliate-disclosure")
def affiliate_disclosure():
    return render_template("affiliate_disclosure.html")

def slugify_model(text):
    """
    Example: 't480' -> 'thinkpad-t480'
             'Lenovo X1 Carbon' -> 'thinkpad-x1-carbon'
    """
    text = text.lower().strip()
    text = text.replace("thinkpad", "").replace("lenovo", "").strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")

    if not text.startswith("thinkpad-"):
        text = f"thinkpad-{text}"

    return text

@bp.route("/search")
def search_model():
    query = request.args.get("q", "").strip()
    country = request.args.get("country")

    if not query or not country:
        return redirect(url_for("main.home"))

    country, _, _ = get_country_context_or_404(country)
    model_slug = slugify_model(query)

    model = get_model_by_slug(model_slug)

    if not model:
        return render_template("search.html", query=query, country=country)

    return redirect(url_for("main.model_page", country=country, model_slug=model.slug))

def deal_score(listing): # add this later
    if listing.median_price:
        return (listing.median_price - listing.price) / listing.median_price
    return 0
# .order_by(Listing.deal_score.desc())

@bp.route("/<country>/best/under-300/")
def best_under_300(country):
    country, marketplaces, currency = get_country_context_or_404(country)
    query = base_listing_query(marketplaces).filter(Listing.price <= 300)

    sort = request.args.get("sort", "price")
    direction = request.args.get("direction", "asc")

    SORT_COLUMNS = {
        "price": Listing.price,
        "ram": Specs.ram,
        "storage": Specs.storage,
    }

    order_col = SORT_COLUMNS.get(sort, Listing.price)

    primary_sort = (
        desc(order_col) if direction == "desc"
        else asc(order_col)
    ).nullslast()

    query = query.order_by(primary_sort, Listing.price.asc())

    page = request.args.get("page", 1, type=int)
    per_page = 50

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    listings = pagination.items

    return render_template(
        "best_under_300.html",  # reuse existing template
        listings=listings,
        pagination=pagination,
        sort=sort,
        direction=direction,
        filters={},  # can improve later
    )

@bp.route("/<country>/compare/t480-vs-t490/")
def compare_t480_t490(country):
    country, marketplaces, currency = get_country_context_or_404(country)

    model_a = get_model_by_slug("thinkpad-t480")
    model_b = get_model_by_slug("thinkpad-t490")

    if not model_a or not model_b:
        abort(404)

    # Reuse your existing helper
    query_a = active_listings_query_for_model(model_a.id, marketplaces)
    query_b = active_listings_query_for_model(model_b.id, marketplaces)

    listings_a = query_a.order_by(Listing.price.asc()).limit(10).all()
    listings_b = query_b.order_by(Listing.price.asc()).limit(10).all()

    stats_a = canonical_model_stats(model_a.id, marketplaces)
    stats_b = canonical_model_stats(model_b.id, marketplaces)

    return render_template(
        "compare.html",
        model_a=model_a,
        model_b=model_b,
        listings_a=listings_a,
        listings_b=listings_b,
        stats_a=stats_a,
        stats_b=stats_b,
    )

@bp.route("/<country>/guides/t-series-vs-x-series/")
def guide_t_vs_x(country):
    country, marketplaces, currency = get_country_context_or_404(country)

    return render_template(
        "guide_t_vs_x.html",
    )