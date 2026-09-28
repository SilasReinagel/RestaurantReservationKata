"""Start the app: `python run.py` (add `--reset` to wipe and re-seed the demo database)."""

import argparse
import logging

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Bella Vista reservation agent")
    parser.add_argument("--reset", action="store_true", help="delete the database and re-seed demo data")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from app.config import Settings
    from app.main import create_app

    settings = Settings.from_env()
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            settings.db_path.with_name(settings.db_path.name + suffix).unlink(missing_ok=True)
        logging.info("Database reset: %s", settings.db_path)

    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
