"""Đọc dữ liệu cho bảng xếp hạng từ DB (một câu SELECT duy nhất, nên là một ảnh chụp nhất quán của SQLite)."""

from server.scoreboard.ranking import Entry, compute_rows

# Điều kiện lọc nằm hết trong SQL (README 3.3): chỉ role `user` (admin không bao giờ có hàng), chỉ đề đang hiện, chỉ bài
# đã có verdict. Bài đang chấm lại vẫn giữ verdict cũ (D6) nên vẫn được tính; bài mới chưa chấm có verdict NULL.
# `s.verdict IS NOT NULL` phải viết đúng nguyên văn để SQLite dùng được chỉ mục phủ một phần `idx_submissions_board`.
BOARD_QUERY = (
    "SELECT s.user_id, u.username, s.problem_id, s.id, s.submitted_at, s.verdict "
    "FROM submissions s "
    "JOIN users u ON u.id = s.user_id "
    "JOIN problems p ON p.id = s.problem_id "
    "WHERE u.role = 'user' AND p.hidden = 0 AND s.verdict IS NOT NULL"
)


def load_entries(conn):
    return [Entry(*row) for row in conn.execute(BOARD_QUERY)]


def compute_board(db):
    """Danh sách `Row` hiện tại. Dùng kết nối của thread gọi (`Database.conn()`)."""
    return compute_rows(load_entries(db.conn()))
