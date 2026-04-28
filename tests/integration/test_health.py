import pytest
from fastapi.testclient import TestClient

from app.main import app, validate_required_configuration


class FakeBackend:
    def __init__(self, healthy: bool | BaseException) -> None:
        self.healthy = healthy

    async def health_check(self) -> bool:
        if isinstance(self.healthy, BaseException):
            raise self.healthy
        return self.healthy


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "dify-key")
    monkeypatch.setenv("FEISHU_APP_ID", "feishu-app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "feishu-app-secret")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "feishu-token")


@pytest.fixture
def health_backends():
    previous = getattr(app.state, "health_backends", None)
    had_previous = hasattr(app.state, "health_backends")
    try:
        yield
    finally:
        if had_previous:
            app.state.health_backends = previous
        elif hasattr(app.state, "health_backends"):
            del app.state.health_backends


def test_health_returns_200_when_backend_is_healthy(
    test_client: TestClient,
    health_backends,
) -> None:
    app.state.health_backends = {"dify": FakeBackend(True)}

    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "backends": {"dify": True}}


def test_health_returns_503_when_backend_is_unhealthy(
    test_client: TestClient,
    health_backends,
) -> None:
    app.state.health_backends = {"dify": FakeBackend(False)}

    response = test_client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"ok": False, "backends": {"dify": False}}


def test_health_returns_503_when_backend_health_check_raises(
    test_client: TestClient,
    health_backends,
) -> None:
    app.state.health_backends = {"dify": FakeBackend(RuntimeError("backend down"))}

    response = test_client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"ok": False, "backends": {"dify": False}}


def test_required_configuration_passes_when_all_required_env_vars_exist(
    required_env,
) -> None:
    validate_required_configuration()


def test_required_configuration_raises_when_required_env_vars_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "DIFY_API_KEY",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_VERIFICATION_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(
        RuntimeError,
        match=(
            "missing required environment variables: "
            "DIFY_API_KEY, FEISHU_APP_ID, FEISHU_APP_SECRET, "
            "FEISHU_VERIFICATION_TOKEN"
        ),
    ):
        validate_required_configuration()


def test_startup_fails_fast_when_required_configuration_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "DIFY_API_KEY",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_VERIFICATION_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="missing required environment variables"):
        with TestClient(app):
            pass
