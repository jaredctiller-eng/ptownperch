from fastapi import FastAPI

from app.routes import health

app = FastAPI(title="Ptown Perch Ops")
app.include_router(health.router)
