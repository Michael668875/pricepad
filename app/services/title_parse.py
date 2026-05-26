# TITLE PARSING TO REPLACE DETAILED ITEM FETCH.

import re
from app.models import ThinkPadModel, CPU, Model, Listing, Specs
from app import db




def normalize_title(title):
    title = title.lower()
    title = re.sub(r"\s+", " ", title)
    return title.strip()

# find the model in the first four words after "thinkpad"
def find_model_near_thinkpad(title, known_models, sorted_models):
    title = normalize_title(title)

    if "thinkpad" not in title:
        #print("not thinkpad")
        return None, None

    after = title.split("thinkpad", 1)[1].strip()
    window = " ".join(after.split()[:4])  # next 4 tokens

    for model in sorted_models:
        if re.search(rf"\b{re.escape(model.lower())}\b", window):
            canon_id = known_models[model]
            #print(model)
            return model, canon_id

    # use pattern match if no canon model in title
    # fallback regex
    result = find_model_by_pattern(title)

    if result:
        return result, None

    return None, None

def simple_format(name):
    words = name.lower().split()
    return " ".join(word[:1].upper() + word[1:] for word in words)

# use a regex pattern to find the model in the title
def find_model_by_pattern(title):
    title = normalize_title(title)

    # discard titles that don't contain "thinkpad"
    if not re.search(r"\bthinkpad\b", title):
        #print("not thinkpad")
        return None

    patterns = [
    #("base_match", r"\b[a-z]+\d{1,4}[a-z]?(?:-?\d{1,2})?\b"),
    ("simple_match", r"\b(x|t|p|e|l|w|z|a|sl)(\d{1,4}[a-z]?)\b"),   
    ("number_letter_match", r"\b\d{2,3}[a-z](?=\s|$)"),
    ("edge_match", r"\bedge\s*(\d{1,2})\b"),
    ("odd_match", r"\bthinkpad\s*13(?=\s|$)"),
    ("numbers_match", r"\b\d{3}(?=\s|$)"),
    ]
    
    has_carbon = bool(re.search(r"\bcarbon\b", title))
    has_yoga = bool(re.search(r"\byoga\b", title))
    has_tablet = bool(re.search(r"\btablet\b", title))
    has_2in1 = bool(re.search(r"\b2[\s-]?in[\s-]?1\b", title))   
        
    # return first match only
    for name, pattern in patterns:
        match = re.search(pattern, title)
        if match:
            parts = [simple_format(match.group(0))]

            if has_carbon:
                parts.append("Carbon")
            if has_yoga:
                parts.append("Yoga")
            if has_tablet:
                parts.append("Tablet")
            if has_2in1:
                parts.append("2-in-1")
            #if has_gen:
            #    parts.append(f"Gen {has_gen}")

            model_name = " ".join(parts)

            #print(name, model_name)
            return model_name
        
    #print("no match")
    return None
        
#    # return all matches
#    matches = []
#
#    for name, pattern in patterns:
#        match - re.searh(pattern, title)
#        if match:
#            matches.append((name, match.group(0)))
#    print(matches)
#    return matches


def insert_model_from_title(session, listing, known_models, sorted_models):
    model_name, canon_id = find_model_near_thinkpad(listing.title, known_models, sorted_models)

    if not model_name:
        return

    if listing.model:
        # update existing row
        listing.model.name = model_name.strip()
        listing.model.canon_model_id = canon_id
    else:
        # create new row
        model = Model(
            name=model_name.strip(),
            canon_model_id=canon_id,
            listing=listing,
        )
        session.add(model)


def process_title():
    known_models = {m.name: m.id for m in ThinkPadModel.query.all()}

    sorted_models = sorted(known_models, key=len, reverse=True)

    cpu_lookup = build_cpu_lookup()   
    
    for listing in Listing.query.yield_per(500):
        insert_model_from_title(db.session, listing, known_models, sorted_models)

        ram, storage = find_memory(listing.title)
        storage_type = find_storage_type(listing.title)
        cpu = cpu_match(listing.title, cpu_lookup)

        upsert_specs(listing, ram, storage, storage_type, cpu)

  


def upsert_specs(listing, ram, storage, storage_type, cpu):
    if not listing.specs:
        listing.specs = Specs()

    if ram is not None:
        listing.specs.ram = ram

    if storage is not None:
        listing.specs.storage = storage

    if storage_type:
        listing.specs.storage_type = storage_type

    if cpu:
        listing.specs.cpu = cpu


# Parse title for CPU

def build_cpu_lookup():
    cpus = CPU.query.filter(CPU.cpu_num.isnot(None)).all()
    return {cpu.cpu_num.upper(): cpu for cpu in cpus}


def build_cpu_name_list():
    cpus = CPU.query.all()
    return cpus  

def find_cpu_pattern(title): # THIS REGEX NEEDS TO BE IMPROVED
    title = normalize_title(title)

    # Intel full match (i7 1185G7 etc)
    intel_match = re.search(r"\bi[3579][\s\-]?\d{4,5}[a-z]{1,3}\b", title, re.IGNORECASE)
    if intel_match:
        intel = format_cpu_match(intel_match.group(0))
        return intel

    # AMD full match (ryzen 5 5600U etc)
    amd_match = re.search(r"\b\d{4}[a-z]{1,3}\b", title, re.IGNORECASE)
    ryzen_match = re.search(r"\bryzen\b", title, re.IGNORECASE)
    if amd_match or ryzen_match:
        return assemble_amd_name(title)

    # Fallback (family only)
    fallback_match = re.search(r"\b(i3|i5|i7|i9|ryzen 3|ryzen 5|ryzen 7|ryzen 9)\b", title)
    if fallback_match:
        fallback = format_cpu_match(fallback_match.group(0))
        return fallback

    return None



def assemble_amd_name(title):
    title = normalize_title(title)

    cpu_name = []

    ryzen_match = re.search(r"\bryzen[\s\-]?(\d)\b", title, re.IGNORECASE)
    if ryzen_match:
        cpu_name.append(f"Ryzen {ryzen_match.group(1)}")

    amd_match = re.search(r"\b\d{4}[a-z]{1,3}\b", title)
    if amd_match:
        cpu_name.append(amd_match.group(0).upper())

    cpu_name = " ".join(cpu_name)

    return cpu_name



# format the cpu value after regex match so Ryzen is capitalised but i7 is not
def format_cpu_match(value):
    value = value.strip().upper()

    if value.startswith("RYZEN"):
        parts = value.split()
        result = "Ryzen " + " ".join(parts[1:])
    elif value.startswith("I"):
        result = "i" + value[1:]
    else:
        result = value

    # fix PRO casing everywhere except start logic already handled
    result = re.sub(r"\bPRO\b", "Pro", result)

    return result


def extract_cpu_num(title):
    if not title:
        return None

    title = title.upper()

    # Intel full match first
    match = re.search(r"\b(I[3579][\-\s]?\d{4,5}[A-Z]{1,3})\b", title)
    if match:
        return match.group(1).replace(" ", "")

    match = re.search(r"\b(\d{3,5}[A-Z]{1,2}\d?)\b", title)
    if match:
        return match.group(1)

    match = re.search(r"\b([A-Z]\d{3,5}[A-Z]?)\b", title)
    if match:
        return match.group(1)

    return None


def resolve_cpu_from_title(title, cpu_lookup):
    cpu_num = extract_cpu_num(title)
    if cpu_num and cpu_num in cpu_lookup:
        return cpu_lookup[cpu_num]


def cpu_match(title, cpu_lookup):
    # 1. cpu_num lookup (fast dictionary)
    cpu = resolve_cpu_from_title(title, cpu_lookup)
    if cpu:
        return cpu.name

    # 2. regex pattern fallback
    pattern = find_cpu_pattern(title)
    if pattern:
        return pattern    
    
    return None


# Parse title for RAM and Storage values

def find_memory(title):
    title = normalize_title(title)
    matches = re.findall(r"\b(\d+)\s?(mb|gb|tb)\b", title)

    if not matches:
        return None, None
    
    values = []
    for num, unit in matches:
        num = int(num)
        unit = unit.lower()
        if unit == "tb":
            num *= 1024
        elif unit == "mb":
            num /= 1024

        values.append(num)

    values.sort()

    if len(values) == 1:
        # if only one value, call it ram if less than 64 otherwise call it storage
        val = values[0]
        if val <= 64:
            return val, None
        else:
            return None, val
    
    # the smaller value is treated as ram and the larger as storage
    ram = values[0]
    storage = values[-1]

    return ram, storage

# Parse title for STORAGE TYPE

def find_storage_type(title):
    title = normalize_title(title)
    match = re.search(r"\b(hdd|ssd|nvme)\b", title)

    if not match:
        return None

    storage_type = match.group(0)
    
    if storage_type == "hdd":
        storage_type = "HDD"
    elif storage_type == "ssd":
        storage_type = "SSD"
    elif storage_type == "nvme":
        storage_type = "NVMe"

    return storage_type


