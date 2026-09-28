"""FastAPI app: serves the chat UI and a small JSON API."""

import logging
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, db
from app.agent import Agent, CompleteFn, LLMUnavailable, SessionStore, openai_complete
from app.reservations import ReservationService, seed_demo_data
from app.rules import ReservationError

log = logging.getLogger("bella_vista")
STATIC_DIR = config.ROOT_DIR / "static"


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(
    settings: config.Settings | None = None,
    complete: CompleteFn | None = None,
    clock=None,
) -> FastAPI:
    settings = settings or config.Settings.from_env()
    clock = clock or (lambda: datetime.now(settings.timezone))
    if complete is None and settings.openai_api_key:
        complete = openai_complete(settings.openai_api_key, settings.openai_model, settings.reasoning_effort)
    if complete is None:
        log.warning("OPENAI_API_KEY is not set: the UI will load but chat replies will fail.")

    service = ReservationService(settings.db_path, clock)
    if db.init_db(settings.db_path):
        seed_demo_data(service, include_scenarios=settings.seed_demo_scenarios)
        log.info("Initialized new database at %s with demo data", settings.db_path)

    agent = Agent(service, complete, settings.restaurant_phone)
    sessions = SessionStore()

    app = FastAPI(title="Bella Vista Reservations")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        text = request.message.strip()
        if not text:
            return _error(400, "EMPTY_MESSAGE", "Please type a message.")
        if len(text) > config.MAX_MESSAGE_LENGTH:
            return _error(400, "MESSAGE_TOO_LONG", f"Messages are limited to {config.MAX_MESSAGE_LENGTH} characters.")
        session, was_reset = sessions.get_or_create(request.session_id)
        with session.lock:  # one turn at a time per conversation
            try:
                reply = agent.handle(session, text)
            except LLMUnavailable as exc:
                return _error(
                    503, "LLM_UNAVAILABLE",
                    f"{exc} Please try again, or call us at {settings.restaurant_phone}.",
                )
        return {"session_id": session.id, "reply": reply, "session_reset": was_reset}

    @app.get("/api/reservations")
    def list_reservations(date: str = Query(..., description="YYYY-MM-DD")):
        """Demo/debug view of the data store. Unauthenticated by design (localhost only)."""
        try:
            return service.list_for_date(date)
        except ReservationError as err:
            return _error(400, err.code, err.message)

    return app
