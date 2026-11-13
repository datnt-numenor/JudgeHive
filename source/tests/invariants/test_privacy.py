"""Khóa quyền riêng tư: test ẩn, bài người khác, stderr test ẩn không tới client."""
from _support import fake_grade, login, outcome, submit, system


def test_user_sees_samples_only_and_not_admin_api(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        problem = client.get("/api/problems/1").json()
        assert "tests" not in problem
        assert all(set(sample) == {"input", "output"} for sample in problem["samples"])
        hidden = client.portal.call(lambda: app.state.db.one("SELECT input FROM tests WHERE problem_id=1 AND is_sample=0"))["input"]
        assert hidden not in client.get("/api/problems/1").text
        for path in ("/api/admin/users", "/api/admin/workers", "/api/admin/submissions", "/api/admin/problems/1"):
            assert client.get(path).status_code == 403, path


def test_other_users_submission_hidden_over_rest_and_websocket(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "alice")
        with client.websocket_connect("/ws") as alice_ws:
            login(client, "bob")
            sid = submit(client)
            # Alice chỉ nhận bảng xếp hạng, không nhận sự kiện bài của Bob.
            assert alice_ws.receive_json()["type"] == "scoreboard"
        login(client, "alice")
        assert client.get(f"/api/submissions/{sid}").status_code == 404
        assert all(row["id"] != sid for row in client.get("/api/submissions").json())


def test_hidden_problem_invisible_to_user(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        assert client.patch("/api/admin/problems/1", json={"hidden": True}).status_code == 200
        login(client)
        assert client.get("/api/problems/1").status_code == 404
        assert client.get("/api/problems").json() == []
        assert client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": "pass"}).status_code == 404


def test_runtime_error_message_has_no_program_output(monkeypatch):
    leaked = "SECRET-HIDDEN-INPUT-42"
    result = fake_grade(monkeypatch, "python", outcome(exit=1, stdout=leaked, stderr=leaked, stderr_tail=leaked),
                        tests=[{"input": leaked, "output": "x"}])
    assert result["verdict"] == "RE" and leaked not in result["message"]
    result = fake_grade(monkeypatch, "python", outcome(stdout=leaked), tests=[{"input": leaked, "output": "x"}])
    assert result["verdict"] == "WA" and leaked not in result["message"]


def test_no_problem_editing_endpoints(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        draft = {"title": "T", "statement": "S", "tests": [{"input": "1", "output": "1"}]}
        assert client.post("/api/admin/problems", json=draft).status_code == 405
        assert client.put("/api/admin/problems/1", json=draft).status_code == 405
