import os

from db_manager import DBManager


def main() -> int:
    retention_days = max(7, int(os.getenv("RETENTION_DAYS", "180")))
    db = DBManager()
    db.purge_old_data(retention_days=retention_days)
    print(f"DB maintenance complete. retention_days={retention_days}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
