from app import create_app, db
from app.models import Blacklist

def add_phrase(phrase):
    app = create_app()
    with app.app_context():
        db.session.add(Blacklist(phrase=phrase.lower()))
        db.session.commit()

if __name__ == "__main__":
    add_phrase(input("phrase to blacklist: "))