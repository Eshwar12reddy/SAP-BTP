from fastapi import APIRouter

from app.api.v1 import admin, auth, btp, conversations, knowledge, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(admin.router)
api_router.include_router(btp.router)
api_router.include_router(conversations.router)
api_router.include_router(knowledge.router)
