"""OpenAPI 문서 검사. 웹은 이 문서로 타입을 생성하므로(pnpm gen:api) 오류 본문 모양도 문서에 있어야 한다."""

import httpx


def error_schema_ref(spec: dict, path: str, code: str) -> str:
    response = spec["paths"][path]["post"]["responses"][code]
    return response["content"]["application/json"]["schema"]["$ref"]


async def test_approval_error_responses_have_body_model(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()

    for action in ("approve", "reject"):
        path = f"/api/approvals/{{approval_id}}/{action}"
        for code in ("404", "409", "410"):
            assert error_schema_ref(spec, path, code) == "#/components/schemas/ApprovalErrorOut"
    detail = spec["components"]["schemas"]["ApprovalErrorDetail"]
    assert set(detail["required"]) == {"code", "message"}


async def test_reject_409_does_not_mention_mismatch(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()

    reject_409 = spec["paths"]["/api/approvals/{approval_id}/reject"]["post"]["responses"]["409"]["description"]
    approve_409 = spec["paths"]["/api/approvals/{approval_id}/approve"]["post"]["responses"]["409"]["description"]

    assert "mismatch" not in reject_409
    assert "task_not_waiting" in reject_409
    assert "mismatch_confirmation_required" in approve_409


async def test_push_endpoints_are_documented(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()

    assert {"/api/push/public-key", "/api/push/subscribe", "/api/push/unsubscribe"} <= set(spec["paths"])
