"""요청·응답 Pydantic 모델. Django REST framework의 serializer에 해당한다.

파일 하나가 라우터 하나(app/routers/<같은 이름>.py)를 맡는다. 화면은 이 모델들로 만든 OpenAPI에서 타입을 생성한다
(`pnpm --dir apps/web gen:api` → apps/web/lib/api/schema.d.ts, 생성 파일은 손으로 고치지 않는다).

고칠 때 주의:
- 필드 이름·타입·필수 여부, Literal 값, 클래스 이름, 클래스 docstring, Field(description=...)는 모두 OpenAPI에 드러난다.
  바꾸면 gen:api를 다시 돌리고 apps/web의 사용처를 고친다. tests/test_openapi.py도 돌린다.
- 필드 설명은 클래스 본문의 `#` 주석으로 쓴다(Pydantic은 `#` 주석을 OpenAPI에 넣지 않는다).
- 응답 모델에 토큰·비밀번호·메일 본문 같은 민감 값을 넣지 않는다(docs/DATA_POLICY.md).
"""
