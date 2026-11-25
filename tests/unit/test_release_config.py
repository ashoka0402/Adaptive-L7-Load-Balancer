import pytest
from pydantic import ValidationError

from app.config import AdaptiveConfig, BackendConfig, HealthCheckConfig, RetryConfig


@pytest.mark.parametrize("host", ["a/b", "a@b", "http://host", "host:80", "host?x"])
def test_invalid_backend_host_is_rejected(host):
    with pytest.raises(ValidationError):
        BackendConfig(id="test", host=host, port=80)


@pytest.mark.parametrize("path", ["//host", "/health?token=bad", "http://host", "/white space"])
def test_invalid_health_path_is_rejected(path):
    with pytest.raises(ValidationError):
        HealthCheckConfig(path=path)


def test_nonfinite_configuration_and_unbounded_retries_rejected():
    with pytest.raises(ValidationError):
        AdaptiveConfig(interval=float("inf"))
    with pytest.raises(ValidationError):
        RetryConfig(max_attempts=100)
