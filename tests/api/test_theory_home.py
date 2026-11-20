"""GET /v1/home (Plan §7.3): one backend call, no Boost."""

from tests.conftest import Harness
from tests.factories import access_payload, home_payload


async def test_home_returns_the_backend_dashboard(harness: Harness) -> None:
    harness.theory.set("get_home", home_payload())

    response = await harness.client.get(
        "/v1/home", headers={**harness.auth(), "X-Timezone": "Europe/Stockholm"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["firstName"] == "Samira"
    assert body["continue"]["action"] == "START"
    assert body["tabs"]["mockExams"]["locked"] is True
    [call] = harness.theory.calls_to("get_home")
    assert call.kwargs["timezone"] == "Europe/Stockholm"


async def test_home_without_token_is_401(harness: Harness) -> None:
    response = await harness.client.get("/v1/home")

    assert response.status_code == 401
    assert response.json()["errorCode"] == "UNAUTHORIZED"


async def test_home_includes_the_learners_access(harness: Harness) -> None:
    harness.theory.set("get_home", home_payload(access=access_payload(level="FREE", remaining=1)))

    response = await harness.client.get("/v1/home", headers=harness.auth())

    assert response.status_code == 200
    assert response.json()["access"] == {
        "level": "FREE",
        "mockExams": {"available": True, "freeAttemptsLimit": 3, "freeAttemptsRemaining": 1},
    }


async def test_home_access_has_no_attempt_limit_for_a_subscriber(harness: Harness) -> None:
    harness.theory.set(
        "get_home", home_payload(access=access_payload(level="SUBSCRIBED", remaining=None))
    )

    response = await harness.client.get("/v1/home", headers=harness.auth())

    access = response.json()["access"]
    assert access["level"] == "SUBSCRIBED"
    assert access["mockExams"]["freeAttemptsRemaining"] is None
