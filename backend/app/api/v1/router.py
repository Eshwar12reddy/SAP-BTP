from fastapi import APIRouter

from app.api.v1 import conversations

api_router = APIRouter()
api_router.include_router(conversations.router)
