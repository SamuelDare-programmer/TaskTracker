from fastapi import APIRouter
from db.core import get_db_session
from .schemas import TaskResponse

router = APIRouter(prefix="/tasks")


@router.post("/")
async create_tasks()