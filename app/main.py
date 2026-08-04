from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.router import admin, api, auth, pages
from app.scheduler import shutdown as shutdown_scheduler
from app.scheduler import start as start_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(api.router)
app.include_router(admin.router)
