"""`GET /api/scoreboard` (README mục 3.3, D9, D12)."""

from fastapi import APIRouter, Depends, Request

from server.rest import sessions
from server.rest.errors import ApiJSONResponse

router = APIRouter()


@router.get("/api/scoreboard")
async def get_scoreboard(request: Request, user: sessions.SessionUser = Depends(sessions.current_user)):
    # Chỉ đọc bản đã tính gần nhất trong bộ nhớ (bất biến), nên làm trên event loop được và không đụng SQLite. Endpoint
    # này không bao giờ bắt server tính lại bảng. `current_user` (tra phiên bằng SQLite) vẫn chạy trong threadpool.
    return ApiJSONResponse(request.app.state.scoreboard.latest().as_json())
