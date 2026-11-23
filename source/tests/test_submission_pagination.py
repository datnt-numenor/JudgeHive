"""Kiểm tra phân trang bài nộp qua HTTP với SQLite tạm của từng test."""

import pytest

from conftest import login, system


USER_FIELDS = {"id", "problem_id", "language", "state", "verdict", "submitted_at"}
ADMIN_FIELDS = USER_FIELDS | {"user_id", "username"}


def seed_submissions(client, app):
    def insert():
        alice_ids, bob_ids = [], []
        with app.state.db.conn:
            for index in range(130):
                alice_ids.append(app.state.db.conn.execute(
                    "INSERT INTO submissions(user_id,problem_id,language,code,state,submitted_at) "
                    "VALUES(2,1,'python','pass','done',?)", (index,)).lastrowid)
                if index % 10 == 0:
                    bob_ids.append(app.state.db.conn.execute(
                        "INSERT INTO submissions(user_id,problem_id,language,code,state,submitted_at) "
                        "VALUES(3,1,'python','pass','done',?)", (index,)).lastrowid)
        return alice_ids, bob_ids

    return client.portal.call(insert)


def older_pages(client, path, first, limit):
    pages = [first]
    while pages[-1]:
        cursor = pages[-1][-1]["id"]
        response = client.get(path, params={"limit": limit, "before_id": cursor})
        assert response.status_code == 200
        page = response.json()
        assert all(row["id"] < cursor for row in page)
        pages.append(page)
    return [row for page in pages for row in page]


def test_user_pages_are_private_complete_and_stable_after_insert(tmp_path):
    with system(tmp_path) as (client, app):
        alice_ids, bob_ids = seed_submissions(client, app)
        login(client)
        default = client.get("/api/submissions")
        assert default.status_code == 200
        assert [row["id"] for row in default.json()] == sorted(alice_ids, reverse=True)[:100]
        assert all(set(row) == USER_FIELDS for row in default.json())

        first = client.get("/api/submissions", params={"limit": 17}).json()
        assert len(first) == 17
        # Bản ghi mới có ID lớn hơn cursor nên không xuất hiện lại ở trang cũ.
        fresh_id = client.portal.call(lambda: app.state.db.execute(
            "INSERT INTO submissions(user_id,problem_id,language,code,state,submitted_at) "
            "VALUES(2,1,'python','pass','done',999)"))
        rows = older_pages(client, "/api/submissions", first, 17)
        ids = [row["id"] for row in rows]
        assert ids == sorted(alice_ids, reverse=True)
        assert len(ids) == len(set(ids)) == 130
        assert fresh_id not in ids and client.get("/api/submissions", params={"limit": 1}).json()[0]["id"] == fresh_id
        assert client.get("/api/submissions", params={"before_id": min(alice_ids)}).json() == []
        assert client.get(f"/api/submissions/{alice_ids[0]}").json()["code"] == "pass"
        assert client.get(f"/api/submissions/{bob_ids[0]}").status_code == 404


def test_admin_pages_include_every_user_and_preserve_fields(tmp_path):
    with system(tmp_path) as (client, app):
        alice_ids, bob_ids = seed_submissions(client, app)
        login(client)
        assert client.get("/api/admin/submissions").status_code == 403
        login(client, "admin")
        default = client.get("/api/admin/submissions")
        expected = sorted(alice_ids + bob_ids, reverse=True)
        assert default.status_code == 200
        assert [row["id"] for row in default.json()] == expected[:100]
        assert all(set(row) == ADMIN_FIELDS for row in default.json())

        first = client.get("/api/admin/submissions", params={"limit": 23}).json()
        rows = older_pages(client, "/api/admin/submissions", first, 23)
        ids = [row["id"] for row in rows]
        assert ids == expected
        assert len(ids) == len(set(ids)) == 143
        assert {row["username"] for row in rows} == {"alice", "bob"}
        assert client.get("/api/admin/submissions", params={"before_id": min(expected)}).json() == []


@pytest.mark.parametrize("path,username", [
    ("/api/submissions", "alice"),
    ("/api/admin/submissions", "admin"),
])
@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": "abc"},
    {"before_id": 0}, {"before_id": -1}, {"before_id": "abc"},
    {"before_id": 9223372036854775808},
])
def test_invalid_cursor_parameters_return_422(tmp_path, path, username, params):
    with system(tmp_path) as (client, _):
        login(client, username)
        assert client.get(path, params=params).status_code == 422
