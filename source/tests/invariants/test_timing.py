"""Khóa đo thời gian: theo mốc StartedAt/FinishedAt của Docker, không theo wall của CLI."""
import pytest

from worker.runner import START_SLACK, DockerError, container_ms
from _support import docker_state, fake_run


def test_container_ms_parses_docker_timestamps():
    assert container_ms(docker_state("2026-11-13T01:02:03.123456789Z", "2026-11-13T01:02:03.373456789Z")) == 250
    assert container_ms(docker_state("2026-11-13T23:59:59.9Z", "2026-11-14T00:00:01.1Z")) == 1200
    assert container_ms(docker_state("2026-11-13T08:00:00+07:00", "2026-11-13T01:00:00.5Z")) == 500


@pytest.mark.parametrize("finished", ["0001-01-01T00:00:00Z", None, "hôm nay", "2026-11-13T01:00:00Z"])
def test_container_ms_rejects_unfinished_or_bad(finished):
    with pytest.raises(DockerError):
        container_ms(docker_state("2026-11-13T01:00:01Z", finished))


def test_time_comes_from_container_not_cli(monkeypatch):
    result = fake_run(monkeypatch, docker_state("2026-11-13T01:00:00Z", "2026-11-13T01:00:01.9Z"))
    assert result["reason"] is None and result["time_ms"] == 1900


def test_tle_is_container_time_over_limit(monkeypatch):
    result = fake_run(monkeypatch, docker_state("2026-11-13T01:00:00Z", "2026-11-13T01:00:02.001Z"))
    assert result["reason"] == "timeout"


def test_start_slack_is_bounded():
    assert 0 < START_SLACK <= 2


def test_watchdog_kill_after_slack_is_tle_when_container_time_over_limit(monkeypatch):
    result = fake_run(monkeypatch, docker_state("2026-11-13T01:00:00Z", "2026-11-13T01:00:02.900Z"), still_running=True)
    assert result["reason"] == "timeout"


def test_watchdog_kill_before_program_used_its_limit_is_not_tle(monkeypatch):
    # Docker khởi động chậm hơn START_SLACK: chương trình mới chạy 1,5 giây khi bị kill.
    with pytest.raises(DockerError):
        fake_run(monkeypatch, docker_state("2026-11-13T01:00:00Z", "2026-11-13T01:00:01.500Z"), still_running=True)
