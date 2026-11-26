"""Định dạng đề (file JSON của server.manage và form Thêm/Sửa đề của admin) và ghi đề vào database."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_TESTS_BYTES = 1024 * 1024


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Case(Model):
    input: str
    output: str
    is_sample: bool = False


class Problem(Model):
    title: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=20000)
    time_limit_ms: int = Field(default=2000, ge=100, le=10000)
    memory_limit_kib: int = Field(default=262144, ge=65536, le=524288)
    hidden: bool = False
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    tests: list[Case] = Field(min_length=1, max_length=20)


def parse_problem(text):
    """Trả Problem đã kiểm tra; ném ValueError (gồm pydantic.ValidationError) nếu sai."""
    return check_size(Problem.model_validate_json(text))


def check_size(problem):
    """Tổng test ≤ 1 MiB để JOB gửi worker luôn dưới giới hạn 8 MiB, kể cả khi JSON escape."""
    size = sum(len(t.input.encode()) + len(t.output.encode()) for t in problem.tests)
    if size > MAX_TESTS_BYTES:
        raise ValueError("Tổng input/output của các test vượt 1 MiB")
    return problem


def insert_problem(db, problem, audit_callback=None):
    with db.conn:
        pid = db.conn.execute(
            "INSERT INTO problems(title,statement,time_limit_ms,memory_limit_kib,hidden,difficulty) VALUES(?,?,?,?,?,?)",
            (problem.title, problem.statement, problem.time_limit_ms, problem.memory_limit_kib, problem.hidden,
             problem.difficulty),
        ).lastrowid
        db.conn.executemany("INSERT INTO tests(problem_id,position,input,output,is_sample) VALUES(?,?,?,?,?)",
                            [(pid, i, t.input, t.output, t.is_sample) for i, t in enumerate(problem.tests)])
        # Audit chạy trước commit để lỗi ghi log cũng hoàn tác đề và toàn bộ testcases.
        if audit_callback is not None:
            audit_callback(pid)
    return pid


def update_problem(db, pid, problem, audit_callback=None):
    """Thay toàn bộ nội dung và bộ test của đề trong một transaction."""
    with db.conn:
        db.conn.execute("UPDATE problems SET title=?,statement=?,time_limit_ms=?,memory_limit_kib=?,hidden=?,difficulty=? "
                        "WHERE id=?", (problem.title, problem.statement, problem.time_limit_ms, problem.memory_limit_kib,
                                       problem.hidden, problem.difficulty, pid))
        db.conn.execute("DELETE FROM tests WHERE problem_id=?", (pid,))
        db.conn.executemany("INSERT INTO tests(problem_id,position,input,output,is_sample) VALUES(?,?,?,?,?)",
                            [(pid, i, t.input, t.output, t.is_sample) for i, t in enumerate(problem.tests)])
        if audit_callback is not None:
            audit_callback(pid)
