from fastapi import APIRouter

from app.api.v1 import (
    admin,
    architecture_advisor,
    auth,
    btp,
    btp_connections,
    cloud_foundry,
    conversations,
    infrastructure_plans,
    knowledge,
    memories,
    service_recommendations,
    sizing,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(admin.router)
api_router.include_router(btp.router)
api_router.include_router(btp_connections.router)
api_router.include_router(cloud_foundry.router)
api_router.include_router(conversations.router)
api_router.include_router(knowledge.router)
api_router.include_router(memories.router)
api_router.include_router(service_recommendations.router)
api_router.include_router(architecture_advisor.router)
api_router.include_router(sizing.router)
api_router.include_router(infrastructure_plans.router)
