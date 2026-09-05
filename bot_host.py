"""Совместимый entrypoint (один релиз): вся логика в app.py. Удаляется отдельной задачей."""
from app import main

if __name__ == "__main__":
    main()
