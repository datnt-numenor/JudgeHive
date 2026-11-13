"""Định dạng file đề JSON và nạp đề vào database. Web không tạo/sửa đề; admin nạp bằng server.manage."""
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
    tests: list[Case] = Field(min_length=1, max_length=20)


def parse_problem(text):
    """Trả Problem đã kiểm tra; ném ValueError (gồm pydantic.ValidationError) nếu sai."""
    problem = Problem.model_validate_json(text)
    size = sum(len(t.input.encode()) + len(t.output.encode()) for t in problem.tests)
    if size > MAX_TESTS_BYTES:
        raise ValueError("Tổng input/output của các test vượt 1 MiB")
    return problem


def insert_problem(db, problem):
    with db.conn:
        pid = db.conn.execute(
            "INSERT INTO problems(title,statement,time_limit_ms,memory_limit_kib,hidden) VALUES(?,?,?,?,?)",
            (problem.title, problem.statement, problem.time_limit_ms, problem.memory_limit_kib, problem.hidden),
        ).lastrowid
        db.conn.executemany("INSERT INTO tests(problem_id,position,input,output,is_sample) VALUES(?,?,?,?,?)",
                            [(pid, i, t.input, t.output, t.is_sample) for i, t in enumerate(problem.tests)])
    return pid
