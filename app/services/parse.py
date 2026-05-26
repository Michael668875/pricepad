import re
import os
import logging
from app.models import db, Specs, Model, ThinkPadModel, Listing, Blacklist


STORAGE_MAP = {
    "NVMe": ["nvme", "non-volatile memory express"],
    "SSD": ["ssd", "solid state drive", "solid-state-drive"],
    "HDD": ["hdd", "hard disk drive", "hard-disk-drive", "hard disk"],
}

def normalize_storage_type(raw_value):
    if not raw_value:
        return None

    raw_value = raw_value.strip().lower()

    for normalized, aliases in STORAGE_MAP.items():
        if any(alias in raw_value for alias in aliases):
            return normalized

    return None

def insert_storage_type():
    specs = (
        db.session.query(Specs)
        .filter(Specs.storage_type_processed.is_(False))
        .all()
    )

    for spec in specs:
        spec.storage_type = normalize_storage_type(spec.raw_storage_type)
        spec.storage_type_processed = True

    db.session.commit()


def convert_size_to_gb(raw_value):
    """
    Converts values like:
    '512MB', '16 GB', '1TB', '1.5 TB'
    into float GB.
    Returns None if invalid.
    """
    if not raw_value:
        return None

    text = raw_value.strip().upper()

    if text in {"NONE", "N/A", "UNKNOWN", "NULL", "-"}:
        return None

    match = re.search(r'(\d+(?:\.\d+)?)\s*(MB|GB|TB)\b', text)
    if not match:
        return None

    num = float(match.group(1))
    unit = match.group(2)

    if num <= 0:
        return None

    if unit == "TB":
        return num * 1024
    elif unit == "GB":
        return num
    elif unit == "MB":
        return num / 1024

    return None

def normalize_specs_field(field_raw: str, field_clean: str, processed_flag: str):
    """
    Generic function to normalize a Specs field.
    Arguments:
        field_raw: str = column name of raw field (e.g., "raw_ram")
        field_clean: str = column name of clean field (e.g., "ram")
        processed_flag: str = column name of processed boolean (e.g., "ram_processed")
    """
    from sqlalchemy import and_

    # Get unprocessed rows
    rows = db.session.query(Specs).filter(
        getattr(Specs, field_raw).isnot(None),
        getattr(Specs, processed_flag) == False
    ).all()

    updated_count = 0

    for spec in rows:
        raw_value = getattr(spec, field_raw)
        converted = convert_size_to_gb(raw_value)

        setattr(spec, field_clean, converted)
        setattr(spec, processed_flag, True)
        updated_count += 1

    db.session.commit()
    print(f"Updated {updated_count} rows for {field_clean}")

# how to use it
"""
    # Normalize RAM
normalize_specs_field("raw_ram", "ram", "ram_processed")

# Normalize Storage
normalize_specs_field("raw_storage", "storage", "storage_processed")
"""

# Later in font end, convert gb back to MB/GB/TB
"""
def display_size_gb(value_gb: float) -> str:
    if value_gb is None:
        return "N/A"
    if value_gb >= 1024:
        return f"{value_gb / 1024:.1f}TB"
    elif value_gb < 1:
        return f"{value_gb * 1024:.0f}MB"
    else:
        return f"{value_gb:.0f}GB"
"""
        

         
# possible query logic
"""rows = (
    db.session.query(Model.raw_model)
    .filter(Model.raw_model.isnot(None))
    .filter(Model.raw_model != "")
    .all()
)

for row in rows:
    raw_model = row.raw_model
    print(raw_model)"""



def normalize_text(text):
    """Basic cleanup before matching."""
    if not text:
        return ""
    text = text.strip().upper()

    # optional cleanup
    text = re.sub(r"[^A-Z0-9\s\-]", " ", text)   # keep letters/numbers/space/hyphen
    text = re.sub(r"\s+", " ", text).strip()     # collapse spaces

    return text


def find_best_model(text, canon_names):
    """
    Return the first canonical model found in text.
    Longest names checked first, so 'E14 GEN 2' beats 'E14'.
    """
    text = normalize_text(text)

    # longest first so more specific names win
    sorted_names = sorted(canon_names, key=len, reverse=True)

    for name in sorted_names:
        clean_name = normalize_text(name)

        # whole-word-ish match
        pattern = rf"\b{re.escape(clean_name)}\b"

        if re.search(pattern, text):
            return name   # return original canonical name

    return None

# single processing
def parse_model_for_listing(listing, model_row, canon_names):
    """
    listing = Listing object
    model_row = Model object (the row linked to this listing)
    canon_names = list of ThinkPadModel.name values
    """

    # 1. Parse from aspect/raw_model
    parsed_aspect = find_best_model(model_row.raw_model, canon_names)
    model_row.parsed_aspect = parsed_aspect

    if parsed_aspect:
        model_row.name = parsed_aspect
        return

    # 2. Parse from MPN
    parsed_mpn = find_best_model(model_row.raw_mpn, canon_names)
    model_row.parsed_mpn = parsed_mpn

    if parsed_mpn:
        model_row.name = parsed_mpn
        return

    # 3. Parse from title
    parsed_title = find_best_model(listing.title, canon_names)
    model_row.parsed_title = parsed_title

    if parsed_title:
        model_row.name = parsed_title
        return

    # 4. Nothing matched
    model_row.name = "UNKNOWN"

# batch processing
def parse_all_models():
    # get all canonical models once
    canon_models = {m.name: m.id for m in ThinkPadModel.query.filter(ThinkPadModel.name.isnot(None)).all()}

    model_rows = Model.query.all()

    for model_row in model_rows:
        listing = Listing.query.get(model_row.listing_id)
        if not listing:
            model_row.name = "UNKNOWN"
            model_row.canon_model_id = None
            continue

        # parse_model_for_listing updates model_row.name
        parse_model_for_listing(listing, model_row, list(canon_models.keys()))

        # after parsing, check if the name matches a canonical model
        if model_row.name in canon_models:
            model_row.canon_model_id = canon_models[model_row.name]
        else:
            model_row.canon_model_id = None
            model_row.name = "UNKNOWN"

    db.session.commit()




# blacklist items in wrong category


def load_blacklist():
    """
    Load blacklist phrases from database.
    """
    try:
        return [
            b.phrase.lower()
            for b in Blacklist.query.all()
        ]
    except Exception as e:
        logging.warning(f"Warning: Could not load blacklist from DB: {e}")
        return []
    

# Check if a listing title contains any blacklisted word
def is_blacklisted(title, blacklist):
    title_lower = title.lower()
    return any(word in title_lower for word in blacklist)

def blacklist(listings):
    bl = set(load_blacklist())   
    clean_items = []

    for listing in listings:
        if is_blacklisted(listing["title"], bl):
            # Skip blacklisted item
            continue
        clean_items.append(listing)
    
    return clean_items