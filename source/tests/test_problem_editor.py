"""Admin thêm/sửa đề trên web: quyền, kiểm tra dữ liệu, không sửa đề đã có bài nộp."""
from conftest import login, submit, system

DRAFT = {"title": "Tổng đoạn con lớn nhất", "statement": "Cho dãy n số...", "time_limit_ms": 1000,
         "memory_limit_kib": 262144, "difficulty": "medium", "hidden": False,
         "tests": [{"input": "3\n1 -2 3\n", "output": "3\n", "is_sample": True},
                   {"input": "1\n-5\n", "output": "-5\n", "is_sample": False}]}


def test_admin_creates_problem_and_user_sees_only_samples(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        pid = client.post("/api/admin/problems", json=DRAFT).json()["id"]
        detail = client.get(f"/api/admin/problems/{pid}").json()
        assert detail["title"] == DRAFT["title"] and detail["difficulty"] == "medium" and detail["locked"] is False
        assert detail["tests"] == DRAFT["tests"]
        login(client)
        problem = client.get(f"/api/problems/{pid}").json()
        assert problem["samples"] == [{"input": "3\n1 -2 3\n", "output": "3\n"}]
        assert "-5" not in client.get(f"/api/problems/{pid}").text


def test_admin_edits_problem_without_submissions(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        pid = client.post("/api/admin/problems", json=DRAFT).json()["id"]
        changed = {**DRAFT, "title": "Đã sửa", "difficulty": "hard", "hidden": True,
                   "tests": [{"input": "1\n7\n", "output": "7\n", "is_sample": True}]}
        assert client.put(f"/api/admin/problems/{pid}", json=changed).status_code == 200
        detail = client.get(f"/api/admin/problems/{pid}").json()
        assert (detail["title"], detail["difficulty"], detail["hidden"]) == ("Đã sửa", "hard", 1)
        assert detail["tests"] == changed["tests"]


def test_problem_with_submissions_cannot_be_edited_but_can_be_hidden(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        submit(client)  # bài nộp vào đề 1
        login(client, "admin")
        assert client.get("/api/admin/problems/1").json()["locked"] is True
        assert client.put("/api/admin/problems/1", json=DRAFT).status_code == 409
        assert client.get("/api/admin/problems/1").json()["title"] == "A + B"
        assert client.patch("/api/admin/problems/1", json={"hidden": True}).status_code == 200


def test_only_admin_can_create_or_edit(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        assert client.post("/api/admin/problems", json=DRAFT).status_code == 403
        assert client.put("/api/admin/problems/1", json=DRAFT).status_code == 403
        assert client.get("/api/admin/problems/1").status_code == 403
        assert client.post("/api/admin/problems", json=DRAFT, headers={"Origin": "http://evil.test"}).status_code == 403


def test_invalid_problem_is_rejected(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        bad = [{**DRAFT, "tests": []}, {**DRAFT, "tests": DRAFT["tests"] * 11}, {**DRAFT, "title": ""},
               {**DRAFT, "difficulty": "expert"}, {**DRAFT, "time_limit_ms": 50}, {**DRAFT, "memory_limit_kib": 1024},
               {**DRAFT, "extra": 1}]
        for body in bad:
            assert client.post("/api/admin/problems", json=body).status_code == 422, body
        big = {**DRAFT, "tests": [{"input": "a" * 600_000, "output": ""}, {"input": "b" * 600_000, "output": ""}]}
        assert client.post("/api/admin/problems", json=big).status_code == 400
        assert client.put("/api/admin/problems/999", json=DRAFT).status_code == 404
        assert len(client.get("/api/problems").json()) == 1
