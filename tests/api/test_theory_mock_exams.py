"""Mock exams: browsing (list + preview) and sessions (start, submit, result, history)."""

import uuid

import respx

from fordona_connect.learning.errors import (
    MockExamAttemptsExhaustedError,
    MockExamNotFoundError,
    MockExamNotSubmittedError,
    MockExamSessionNotFoundError,
    MockExamTimedOutError,
)
from tests.conftest import BOOST, Harness
from tests.factories import mock_exam_result_payload, mock_exam_session_payload, question_payload


def _mock_exam_summary(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "mockExamId": "1",
        "name": "Exam A",
        "description": "Covers unit 1",
        "questionCount": 2,
        "complete": True,
    }
    payload.update(overrides)
    return payload


async def test_list_mock_exams_returns_the_backend_summaries(harness: Harness) -> None:
    harness.theory.set(
        "list_mock_exams",
        [_mock_exam_summary(), _mock_exam_summary(mockExamId="2", name="Exam B")],
    )

    response = await harness.client.get("/v1/mock-exams", headers=harness.auth())

    assert response.status_code == 200
    body = response.json()
    assert [e["mockExamId"] for e in body] == ["1", "2"]
    assert body[0]["name"] == "Exam A"
    assert body[0]["questionCount"] == 2


@respx.mock
async def test_get_mock_exam_hydrates_each_question_from_boost(harness: Harness) -> None:
    harness.theory.set(
        "get_mock_exam",
        {**_mock_exam_summary(), "items": [{"questionId": "100", "version": "5"}]},
    )
    harness.boost.get(f"{BOOST}/v1/delivery/questions/100/revisions/5").respond(
        json=question_payload(question_id="100")
    )

    response = await harness.client.get("/v1/mock-exams/1", headers=harness.auth())

    assert response.status_code == 200
    body = response.json()
    assert body["mockExamId"] == "1"
    assert len(body["questions"]) == 1
    [question] = body["questions"]
    assert question["prompt"][0]["markdown"] == "Vad betyder skylten?"
    assert [opt["key"] for opt in question["options"]] == ["a", "b", "c"]
    # Never leaks internal ids — same invariant as attempt items.
    assert "questionId" not in body["questions"][0]


async def test_get_mock_exam_unknown_id_is_404(harness: Harness) -> None:
    harness.theory.set("get_mock_exam", MockExamNotFoundError("Mock exam not found: 999"))

    response = await harness.client.get("/v1/mock-exams/999", headers=harness.auth())

    assert response.status_code == 404
    assert response.json()["errorCode"] == "THEORY_MOCK_EXAM_NOT_FOUND"


# --- Sessions --------------------------------------------------------------------------

SESSION = "33333333-3333-3333-3333-333333333333"


def _no_internal_ids(value: object) -> bool:
    if isinstance(value, dict):
        return all(
            key not in {"questionId", "version", "question_id"} and _no_internal_ids(v)
            for key, v in value.items()
        )
    if isinstance(value, list):
        return all(_no_internal_ids(v) for v in value)
    return True


@respx.mock
async def test_start_mock_exam_hydrates_items_without_internal_ids(harness: Harness) -> None:
    harness.theory.set("start_mock_exam", mock_exam_session_payload())
    harness.boost.get(f"{BOOST}/v1/delivery/questions/100/revisions/5").respond(
        json=question_payload(question_id="100")
    )
    request_id = str(uuid.uuid4())

    response = await harness.client.post(
        "/v1/mock-exams/1/sessions", headers=harness.auth(), json={"requestId": request_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sessionId"] == SESSION
    assert body["status"] == "IN_PROGRESS"
    assert body["timeLimitSeconds"] == 3600
    assert body["questionCount"] == 1
    assert body["freeAttemptsRemaining"] == 2
    [item] = body["items"]
    assert [option["key"] for option in item["options"]] == ["b", "a", "c"]
    assert [option["label"] for option in item["options"]] == ["A", "B", "C"]
    assert _no_internal_ids(body)
    [call] = harness.theory.calls_to("start_mock_exam")
    assert call.args == ("1", request_id)


async def test_start_mock_exam_without_request_id_is_422(harness: Harness) -> None:
    response = await harness.client.post(
        "/v1/mock-exams/1/sessions", headers=harness.auth(), json={}
    )

    assert response.status_code == 422


async def test_start_mock_exam_with_no_free_attempts_left_is_403(harness: Harness) -> None:
    harness.theory.set("start_mock_exam", MockExamAttemptsExhaustedError())

    response = await harness.client.post(
        "/v1/mock-exams/1/sessions", headers=harness.auth(), json={"requestId": str(uuid.uuid4())}
    )

    assert response.status_code == 403
    assert response.json()["errorCode"] == "THEORY_EXAM_ATTEMPTS_EXHAUSTED"


@respx.mock
async def test_submit_mock_exam_passes_answers_by_seq_and_returns_the_graded_result(
    harness: Harness,
) -> None:
    harness.theory.set("submit_mock_exam", mock_exam_result_payload())
    harness.boost.get(f"{BOOST}/v1/delivery/questions/100/revisions/5").respond(
        json=question_payload(question_id="100")
    )

    response = await harness.client.post(
        f"/v1/mock-exams/sessions/{SESSION}/submission",
        headers=harness.auth(),
        json={"answers": [{"seq": 1, "selectedOptionKey": "b"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["correctCount"], body["totalCount"], body["passed"]) == (0, 1, False)
    [item] = body["items"]
    assert item["correctOption"]["key"] == "a"
    assert item["correctOption"]["label"] == "B"
    assert item["explanation"][0]["markdown"] == "The right-hand rule applies."
    assert _no_internal_ids(body)
    [call] = harness.theory.calls_to("submit_mock_exam")
    assert call.kwargs["answers"] == {1: "b"}


async def test_submit_mock_exam_answering_a_seq_twice_is_422(harness: Harness) -> None:
    response = await harness.client.post(
        f"/v1/mock-exams/sessions/{SESSION}/submission",
        headers=harness.auth(),
        json={
            "answers": [
                {"seq": 1, "selectedOptionKey": "a"},
                {"seq": 1, "selectedOptionKey": "b"},
            ]
        },
    )

    assert response.status_code == 422


async def test_submit_mock_exam_after_the_deadline_is_409(harness: Harness) -> None:
    harness.theory.set("submit_mock_exam", MockExamTimedOutError())

    response = await harness.client.post(
        f"/v1/mock-exams/sessions/{SESSION}/submission",
        headers=harness.auth(),
        json={"answers": []},
    )

    assert response.status_code == 409
    assert response.json()["errorCode"] == "THEORY_EXAM_TIMED_OUT"


async def test_mock_exam_history_lists_the_backend_entries(harness: Harness) -> None:
    harness.theory.set(
        "list_mock_exam_history",
        [
            {
                "sessionId": SESSION,
                "mockExamId": "1",
                "name": "Exam A",
                "correctCount": 3,
                "totalCount": 4,
                "scorePercent": 75,
                "passed": True,
                "timeTakenSeconds": 300,
                "completedAt": "2026-11-20T10:05:00Z",
            }
        ],
    )

    response = await harness.client.get("/v1/mock-exams/sessions", headers=harness.auth())

    assert response.status_code == 200
    [entry] = response.json()
    assert entry["sessionId"] == SESSION
    assert entry["passed"] is True


async def test_get_unknown_mock_exam_session_is_404(harness: Harness) -> None:
    harness.theory.set(
        "get_mock_exam_session", MockExamSessionNotFoundError("Mock exam session not found")
    )

    response = await harness.client.get(
        f"/v1/mock-exams/sessions/{SESSION}", headers=harness.auth()
    )

    assert response.status_code == 404
    assert response.json()["errorCode"] == "THEORY_MOCK_EXAM_SESSION_NOT_FOUND"


async def test_mock_exam_result_before_submitting_is_409(harness: Harness) -> None:
    harness.theory.set("get_mock_exam_result", MockExamNotSubmittedError())

    response = await harness.client.get(
        f"/v1/mock-exams/sessions/{SESSION}/result", headers=harness.auth()
    )

    assert response.status_code == 409
    assert response.json()["errorCode"] == "THEORY_MOCK_EXAM_NOT_SUBMITTED"


async def test_start_mock_exam_without_token_is_401(harness: Harness) -> None:
    response = await harness.client.post(
        "/v1/mock-exams/1/sessions", json={"requestId": str(uuid.uuid4())}
    )

    assert response.status_code == 401
