"""Cấu hình ba ngôn ngữ chấm: lệnh biên dịch/chạy, hệ số thời gian, chữ ký OOM (PLAN mục 4, D13, D16)."""

from dataclasses import dataclass

from common import protocol

# Giới hạn chạy chương trình thí sinh. [ĐỀ XUẤT]
# NOFILE: spike PR 2 (results/20261023T070621Z-bdba35532b3f/summary.md, mục "Ngưỡng tối thiểu") cho hello-world chạy
# được ở cả ba ngôn ngữ với RLIMIT_NOFILE = 8 (giá trị thấp nhất đã thử); lấy 32 = 4 lần ngưỡng đó làm biên.
RUN_NOFILE = 32
RUN_FSIZE = 64 * 1024 * 1024
# Biên dịch: các trình biên dịch mở nhiều file hơn; chưa đo ở spike nên đặt rộng, test verdict kiểm chứng.
COMPILE_NOFILE = 256
COMPILE_FSIZE = 64 * 1024 * 1024
COMPILE_AS_BYTES_CPP = 1024 * 1024 * 1024  # g++; javac giới hạn bằng -J-Xmx (RLIMIT_AS làm JVM không khởi động)
COMPILE_JAVAC_XMX = "-J-Xmx512m"

# Hệ số theo README mục RESULT; chính sách do chủ repo chốt sau spike PR 2.
TIME_FACTORS = {"cpp": 1, "java": 2, "python": 5}
COMPILE_TIMEOUT_S = 15  # ≤ COMPILE_TIMEOUT_MAX_S


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Language:
    name: str
    source_file: str
    compile_cmd: tuple | None  # None: không biên dịch
    compile_timeout_s: float
    compile_as_bytes: int | None
    time_factor: int
    oom_signature: str  # chữ ký OOM trong đoạn cuối stderr (D13)
    rss_rule: bool  # D13: vế "RSS ≥ 90% giới hạn" chỉ áp cho Python/C++, không áp cho Java
    as_limit: bool  # RLIMIT_AS = memory_limit_kib (Python/C++); Java dùng -Xmx

    def run_cmd(self, memory_limit_kib):
        if self.name == "python":
            return ["python3", self.source_file]
        if self.name == "cpp":
            return ["./main"]
        xmx_mib = max(16, memory_limit_kib // 1024)
        return ["java", f"-Xmx{xmx_mib}m", "-XX:+UseSerialGC", "-Xshare:auto", "-XX:-UsePerfData",
                "-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8", "Main"]

    def run_limits(self, memory_limit_kib):
        limits = {"nofile": RUN_NOFILE, "fsize": RUN_FSIZE}
        if self.as_limit:
            limits["as"] = memory_limit_kib * 1024
        return limits

    def compile_limits(self):
        limits = {"nofile": COMPILE_NOFILE, "fsize": COMPILE_FSIZE}
        if self.compile_as_bytes:
            limits["as"] = self.compile_as_bytes
        return limits


LANGUAGES = {
    "python": Language("python", "Main.py", None, 0, None, TIME_FACTORS["python"], "MemoryError", True, True),
    "cpp": Language("cpp", "Main.cpp", ("g++", "-O2", "-std=c++17", "-o", "main", "Main.cpp"), COMPILE_TIMEOUT_S,
                    COMPILE_AS_BYTES_CPP, TIME_FACTORS["cpp"], "std::bad_alloc", True, True),
    "java": Language("java", "Main.java", ("javac", COMPILE_JAVAC_XMX, "Main.java"), COMPILE_TIMEOUT_S, None,
                     TIME_FACTORS["java"], "java.lang.OutOfMemoryError", False, False),
}


def validate(languages=None, *, max_time_factor=protocol.MAX_TIME_FACTOR,
             compile_timeout_max_s=protocol.COMPILE_TIMEOUT_MAX_S):
    """D16: mọi hệ số ≤ MAX_TIME_FACTOR và mọi timeout biên dịch ≤ COMPILE_TIMEOUT_MAX. Vi phạm thì ConfigError."""
    problems = []
    for lang in (languages or LANGUAGES).values():
        if lang.time_factor > max_time_factor:
            problems.append(f"{lang.name}: hệ số thời gian {lang.time_factor} > MAX_TIME_FACTOR {max_time_factor}")
        if lang.time_factor < 1:
            problems.append(f"{lang.name}: hệ số thời gian {lang.time_factor} < 1")
        if lang.compile_cmd and lang.compile_timeout_s > compile_timeout_max_s:
            problems.append(f"{lang.name}: timeout biên dịch {lang.compile_timeout_s} s > "
                            f"COMPILE_TIMEOUT_MAX {compile_timeout_max_s} s")
    if problems:
        raise ConfigError("; ".join(problems))
