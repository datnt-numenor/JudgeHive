"""REST tối thiểu của M2 (README mục 3): đăng nhập, phiên SQLite, đề, nộp bài, lịch sử.

`install(app)` gắn route và bộ xử lý lỗi (lúc dựng app); `app.state.rest` (RestState) được dựng trong lifespan, sau khi
dispatcher đã sẵn sàng nhận bài."""

from server.rest import auth_routes, errors, problem_routes, scoreboard_routes, submission_routes


def install(app):
    errors.install_handlers(app)
    # Gắn từng route thẳng vào app thay vì `include_router`: FastAPI mới bọc router con trong một lớp nội bộ, làm
    # `app.routes` không còn liệt kê được route (cần cho `Allow` của 405 và cho test quét route).
    for module in (auth_routes, problem_routes, scoreboard_routes, submission_routes):
        for route in module.router.routes:
            app.add_api_route(route.path, route.endpoint, methods=sorted(route.methods), name=route.name)
