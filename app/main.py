from fastapi import FastAPI

from app.routes import health, ical

app = FastAPI(title="Ptown Perch Ops")
app.include_router(health.router)
app.include_router(ical.router)
