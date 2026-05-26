from app import create_app, db
from app.models import Blacklist

BLACKLIST_ITEMS = [
    "trackpoint caps",
    "ideapad",
    "accessory for",
    "adaptor for",
    "thinkbook",
    "kompanio",
    "DC power jack",
    "speaker for",
    "cover for",
    "screen protector",
    "sleeve for",
    "keyboard for lenovo",
    "keyboard for thinkpad",
    "keyboard with pointing for",
    "keyboard, for lenovo",
    "keyboard, for thinkpad",
    "touchpad for lenovo",
    "touchpad for thinkpad",
    "matte screen protector",
    "screen protector for",
    "mediatek",
    "laptop battery",
    "ink cartridge",
    "this listing is only for",
    "sleeve case",
    "laptop case",
    "protective case",
    "keyboard cover",
    "cover hard case only",
    "motherboard",
    "processor p/n",
]

def seed_blacklist():
    app = create_app()

    with app.app_context():
        added = 0

        for phrase in BLACKLIST_ITEMS:
            phrase_clean = phrase.strip().lower()

            exists = Blacklist.query.filter_by(phrase=phrase_clean).first()
            if exists:
                continue

            db.session.add(Blacklist(phrase=phrase_clean))
            added += 1

        db.session.commit()
        print(f"Inserted {added} blacklist items.")

if __name__ == "__main__":
    seed_blacklist()