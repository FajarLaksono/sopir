from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routes import seasons, auth

app = FastAPI(title="Sopir API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # your frontend dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(seasons.router)


@app.get("/health")
def health():
    return {"status": "ok"}
