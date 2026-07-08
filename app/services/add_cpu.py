from app import create_app, db
from app.models import CPU

app = create_app()

def add_cpu():
    with app.app_context():
        name = input("CPU name: ").strip()

        cpu_num = input("CPU number (optional): ").strip()
        if cpu_num == "":
            cpu_num = None

        cpu = CPU(
            name=name,
            cpu_num=cpu_num
        )

        db.session.add(cpu)
        db.session.commit()

        print(f"Added: {name}")

if __name__ == "__main__":
    add_cpu()