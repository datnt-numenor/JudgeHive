"""Gọi Docker bằng danh sách argv, không ghép mã người dùng vào shell command.

Runner chạy ở một thread. Event cancel được vòng đọc TCP đặt khi mất server.
Mỗi test một container: Docker cung cấp giới hạn bộ nhớ và bằng chứng OOM.
"""
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import uuid

IMAGE = os.environ.get("JH_RUNNER_IMAGE", "judgehive-runner:local")
OUTPUT_CAP = 1024 * 1024
SOURCES = {"python": "main.py", "cpp": "main.cpp", "java": "Main.java"}
# JVM in chữ ký này ở cuối stderr khi OutOfMemoryError không được bắt.
JAVA_OOM = "java.lang.OutOfMemoryError"
# Thời gian chương trình lấy theo mốc StartedAt/FinishedAt của Docker, không theo wall của
# `docker start --attach`. Watchdog cho thêm khoảng này để bù CLI attach và khởi động container.
START_SLACK = 1.0
DOCKER_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)")


class Cancelled(Exception):
    pass


class DockerError(Exception):
    pass


def docker_time(text):
    """RFC 3339 của Docker có tới 9 chữ số thập phân; datetime chỉ giữ micro giây."""
    match = DOCKER_TIME.fullmatch(text) if isinstance(text, str) else None
    if not match:
        raise DockerError(f"Mốc thời gian Docker không hợp lệ: {text!r}")
    fraction = (match.group(2) or "").ljust(6, "0")[:6]
    zone = "+00:00" if match.group(3) == "Z" else match.group(3)
    return datetime.fromisoformat(f"{match.group(1)}.{fraction}{zone}")


def container_ms(state):
    """Thời gian chạy của tiến trình trong container, do daemon Docker ghi."""
    started, finished = docker_time(state.get("StartedAt")), docker_time(state.get("FinishedAt"))
    if started.year < 2000 or finished < started:
        raise DockerError("Container chưa chạy xong hoặc mốc thời gian sai")
    return (finished - started) // timedelta(milliseconds=1)


class Runner:
    def __init__(self, cancel):
        self.cancel = cancel
        self.prefix = "judgehive-run-" + uuid.uuid4().hex
        self.volume = self.prefix + "-files"
        self.containers = set()

    def command(self, *args, check=True):
        try:
            result = subprocess.run(["docker", *args], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerError("Không gọi được Docker trong 10 giây") from exc
        if check and result.returncode:
            raise DockerError(result.stderr.decode(errors="replace")[-2000:])
        return result.stdout

    def create(self, suffix, memory_kib, command, readonly=False):
        if self.cancel.is_set():
            raise Cancelled()
        name = self.prefix + "-" + suffix
        # Ghi nhận TRƯỚC create để cleanup cả khi CLI timeout nhưng daemon đã tạo.
        self.containers.add(name)
        mount = f"type=volume,source={self.volume},target=/work" + (",readonly" if readonly else "")
        self.command("create", "--name", name, "--interactive", "--network", "none",
                     "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "--memory", f"{memory_kib}k", "--memory-swap", f"{memory_kib}k",
                     "--pids-limit", "128", "--cpus", "1", "--read-only",
                     "--ulimit", "fsize=33554432:33554432", "--ulimit", "nofile=128:128",
                     "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,nr_inodes=2048,mode=1777",
                     "--mount", mount, "--workdir", "/work", IMAGE, *command)
        return name

    def run(self, name, stdin, timeout):
        with tempfile.TemporaryDirectory(prefix="judgehive-io-") as directory:
            root = Path(directory)
            input_path, output_path, error_path = root / "in", root / "out", root / "err"
            input_path.write_bytes(stdin.encode())
            with input_path.open("rb") as inp, output_path.open("wb") as out, error_path.open("wb") as err:
                proc = subprocess.Popen(["docker", "start", "--attach", "--interactive", name],
                                        stdin=inp, stdout=out, stderr=err)
                started = time.monotonic()
                reason = None
                try:
                    while proc.poll() is None:
                        elapsed = time.monotonic() - started
                        if self.cancel.is_set():
                            raise Cancelled()
                        if output_path.stat().st_size + error_path.stat().st_size > OUTPUT_CAP:
                            reason = "output"
                            break
                        if elapsed >= timeout + START_SLACK:
                            reason = "timeout"
                            break
                        # Đây là giám sát tiến trình cục bộ, không phát gói mạng cho server.
                        self.cancel.wait(0.02)
                finally:
                    if proc.poll() is None:
                        # Đóng cả container và CLI attach, tránh để chương trình mồ côi.
                        try:
                            self.command("kill", name, check=False)
                            # Chờ container dừng hẳn để FinishedAt có giá trị.
                            self.command("wait", name, check=False)
                        finally:
                            proc.kill()
                            proc.wait()
            state = json.loads(self.command("inspect", "--format", "{{json .State}}", name))
            if state.get("Running"):
                self.command("wait", name, check=False)
                state = json.loads(self.command("inspect", "--format", "{{json .State}}", name))
            if not state.get("StartedAt") or state["StartedAt"].startswith("0001-") or state.get("Error"):
                raise DockerError("Docker không khởi động được container: " + str(state.get("Error")))
            if self.cancel.is_set():
                raise Cancelled()
            time_ms = container_ms(state)
            if output_path.stat().st_size + error_path.stat().st_size > OUTPUT_CAP:
                reason = "output"
            if reason is None and time_ms > timeout * 1000:
                reason = "timeout"
            with output_path.open("rb") as stream:
                stdout = stream.read(OUTPUT_CAP).decode("utf-8", errors="replace")
            with error_path.open("rb") as stream:
                stderr = stream.read(4096).decode("utf-8", errors="replace")
                # Lỗi biên dịch đọc ở đầu; chữ ký lỗi lúc chạy nằm ở cuối.
                stream.seek(max(0, error_path.stat().st_size - 4096))
                stderr_tail = stream.read(4096).decode("utf-8", errors="replace")
            return {"reason": reason, "oom": state.get("OOMKilled", False), "exit": state["ExitCode"],
                    "stdout": stdout, "stderr": stderr, "stderr_tail": stderr_tail, "time_ms": time_ms}

    def remove(self, name):
        self.command("rm", "--force", name)
        self.containers.discard(name)

    def cleanup(self):
        errors = []
        for name in list(self.containers):
            try:
                self.remove(name)
            except DockerError as exc:
                errors.append(str(exc))
        try:
            self.command("volume", "rm", "--force", self.volume)
        except DockerError as exc:
            errors.append(str(exc))
        return errors

    def grade(self, job, progress):
        language = job["language"]
        source = SOURCES[language]
        compile_command = {"python": ["true"], "cpp": ["g++", "main.cpp", "-O2", "-std=c++17", "-o", "main"],
                           "java": ["javac", "-J-Xmx256m", "-J-XX:ActiveProcessorCount=1", "Main.java"]}[language]
        max_ms = 0  # thời gian của test chậm nhất

        def result(verdict, message=""):
            return {"verdict": verdict, "message": message[:2000], "time_ms": max_ms}

        # Docker copy-up từ /work của image tạo volume với owner 1000.
        self.command("volume", "create", self.volume)
        compiler = self.create("compile", 524288, compile_command)
        with tempfile.TemporaryDirectory(prefix="judgehive-source-") as directory:
            path = Path(directory) / source
            path.write_text(job["code"], encoding="utf-8")
            self.command("cp", str(path), f"{compiler}:/work/{source}")
        if language != "python":
            compiled = self.run(compiler, "", 20)
            if compiled["exit"] != 0 or compiled["reason"] or compiled["oom"]:
                return result("CE", "Biên dịch thất bại: " + compiled["stderr"])
        self.remove(compiler)
        memory_mib = job["memory_limit_kib"] // 1024
        run_command = {"python": ["python3", "-B", "main.py"], "cpp": ["./main"],
                       "java": ["java", "-XX:ActiveProcessorCount=1", "-XX:+UseSerialGC",
                                f"-Xmx{max(16, memory_mib // 2)}m", "-Xss512k",
                                "-XX:MaxMetaspaceSize=48m", "-XX:ReservedCodeCacheSize=16m", "Main"]}[language]
        for index, case in enumerate(job["tests"], 1):
            container = self.create(f"test-{index}", job["memory_limit_kib"], run_command, readonly=True)
            outcome = self.run(container, case["input"], job["time_limit_ms"] / 1000)
            max_ms = max(max_ms, outcome["time_ms"])
            self.remove(container)
            if outcome["oom"]:
                return result("MLE", f"Test {index}: Docker xác nhận OOM")
            if outcome["reason"] == "timeout":
                return result("TLE", f"Test {index}: quá thời gian")
            if outcome["reason"] == "output":
                return result("WA", f"Test {index}: output vượt 1 MiB")
            if language == "java" and outcome["exit"] != 0 and JAVA_OOM in outcome["stderr_tail"]:
                return result("MLE", f"Test {index}: Java hết bộ nhớ heap")
            if outcome["exit"] != 0:
                # Không gửi stderr của test ẩn: chương trình có thể echo input vào stderr.
                return result("RE", f"Test {index}: chương trình thoát mã {outcome['exit']}")
            if outcome["stdout"].split() != case["output"].split():
                return result("WA", f"Test {index}: sai kết quả")
            progress(index)
        return result("AC")


def grade(job, cancel, progress):
    runner = Runner(cancel)
    result = None
    try:
        result = runner.grade(job, progress)
    except Cancelled:
        pass
    except Exception as exc:
        result = {"verdict": "SE", "message": "Lỗi máy chấm: " + str(exc)[:1500], "time_ms": 0}
    finally:
        errors = runner.cleanup()
        for error in errors:
            logging.getLogger(__name__).error("Dọn Docker thất bại: %s", error)
    if cancel.is_set():
        return None
    if errors:
        return {"verdict": "SE", "message": "Không dọn được tài nguyên Docker; kiểm tra log worker", "time_ms": 0}
    return result
