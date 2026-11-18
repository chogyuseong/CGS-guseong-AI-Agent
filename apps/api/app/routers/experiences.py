"""내 경험 API(생활 비서 D 첫 단계, 설정 화면 '내 경험').

    GET    /api/experiences        → 200 ExperienceListOut: 제목·글자 수 목록(본문 없음), 한도, 사용 가능 여부
    GET    /api/experiences/{id}   → 200 ExperienceOut(본문 포함) / 404 / 키 없음 503
    POST   /api/experiences        → 201 ExperienceSummaryOut / 연락처·주소 400 / 한도 409 / 키 없음 503
    DELETE /api/experiences/{id}   → 204 / 404

- 본문은 PAYLOAD_ENCRYPTION_KEY로 암호화해서만 저장한다(app/tools/experiences.py). 키가 없으면 등록을 막는다:
  평문으로라도 저장하면 2026-11-18 사용자 결정(원문 암호화)에 어긋난다.
- 모든 조회·삭제에 user_id 조건. 남의 경험은 없는 것과 같이 404.
- 상태 변경(POST·DELETE)은 앱 전체 Origin 검사 미들웨어를 거친다(app/security.py).
- 경험은 채팅으로 만들지 않는다(원문이 messages에 평문으로 남기 때문). 모델은 list_experiences로 읽기만 한다.

고칠 때 주의:
- 경로·응답 모양을 바꾸면 gen:api 후 apps/web/lib/experiences.ts를 고친다. 테스트: tests/test_experiences_api.py.
- 새로 저장하는 값이 생기면 docs/DATA_POLICY.md "내 경험" 줄을 고친다.

변경 이력:
- 2026-11-18 처음 작성.
"""

from typing import Annotated

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import Settings
from app.db import DbSession
from app.models import Experience
from app.schemas.experiences import ExperienceIn, ExperienceListOut, ExperienceOut, ExperienceSummaryOut
from app.security import CurrentUser
from app.tools import experiences as store

router = APIRouter(prefix="/experiences", tags=["experiences"])


def app_settings(request: Request) -> Settings:
    """create_app이 넣은 설정(테스트는 .env 없는 설정). connections 라우터와 같은 출처."""
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(app_settings)]


def summary_of(experience: Experience, length: int) -> ExperienceSummaryOut:
    return ExperienceSummaryOut(
        id=experience.id, title=experience.title, length=length, created_at=experience.created_at
    )


def require_cipher(settings: Settings) -> Fernet:
    cipher = store.cipher_for(settings)
    if cipher is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=store.DISABLED_MESSAGE)
    return cipher


# 목록. 글자 수는 복호화해서 잰다(키가 없거나 풀 수 없으면 0). 본문은 응답에 넣지 않는다.
@router.get("")
async def list_all(user: CurrentUser, session: DbSession, settings: AppSettings) -> ExperienceListOut:
    cipher = store.cipher_for(settings)
    rows = await store.select_experiences(session, user.id)
    items = [summary_of(row, len((cipher and store.open_body(cipher, row)) or "")) for row in rows]
    return ExperienceListOut(
        experiences=items, limit=store.MAX_EXPERIENCES, max_body_chars=store.MAX_BODY_CHARS, enabled=cipher is not None
    )


@router.get("/{experience_id}", responses={404: {"description": "내 경험 중에 그 id가 없다"}})
async def get_one(experience_id: int, user: CurrentUser, session: DbSession, settings: AppSettings) -> ExperienceOut:
    cipher = require_cipher(settings)
    row = await store.find_experience(session, user.id, experience_id)
    body = store.open_body(cipher, row) if row else None
    if row is None or body is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="경험을 찾을 수 없습니다.")
    return ExperienceOut(id=row.id, title=row.title, length=len(body), created_at=row.created_at, body=body)


# 등록. 순서: 키 → 연락처·주소 → 한도 → 암호화 저장(커밋).
@router.post("", status_code=status.HTTP_201_CREATED, responses={400: {}, 409: {}, 503: {}})
async def create(
    data: ExperienceIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ExperienceSummaryOut:
    cipher = require_cipher(settings)
    if problem := store.contact_problem(data.title, data.body):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=problem)
    if await store.count_experiences(session, user.id) >= store.MAX_EXPERIENCES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"경험은 {store.MAX_EXPERIENCES}개까지 저장할 수 있습니다. 하나를 지운 뒤 등록해 주세요.",
        )
    row = await store.save_experience(session, cipher, user.id, data.title, data.body)
    return summary_of(row, len(data.body))


@router.delete("/{experience_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: {}})
async def delete_one(experience_id: int, user: CurrentUser, session: DbSession) -> Response:
    if not await store.delete_experience(session, user.id, experience_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="경험을 찾을 수 없습니다.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
