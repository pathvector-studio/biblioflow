from fastapi import APIRouter

from app.api.v1 import auth, health, papers, tokens, workspaces

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(tokens.router)
api_router.include_router(workspaces.router)
api_router.include_router(papers.router)
