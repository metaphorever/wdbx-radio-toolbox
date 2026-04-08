from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import jinja2

from archive_manager.scheduler import start_scheduler, stop_scheduler
from shared.database import create_db_and_tables
from web.routes.archive import router as archive_router
from web.routes.library import router as library_router
from web.routes.onboarding import router as onboarding_router
from web.routes.settings import router as settings_router

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="WDBX Radio Toolbox", version="0.1.0", lifespan=lifespan)
app.include_router(archive_router)
app.include_router(library_router)
app.include_router(onboarding_router)
app.include_router(settings_router)

# Use cache_size=0 to bypass Jinja2's LRUCache, which has a thread-safety
# issue in Python 3.14 free-threaded builds when running under uvicorn reload.
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(BASE_DIR / "templates"),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
)
templates = Jinja2Templates(env=_jinja_env)


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")
