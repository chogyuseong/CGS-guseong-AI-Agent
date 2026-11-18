"""로그인 요청과 사용자 응답(app/routers/auth.py). 화면 사용처는 apps/web/lib/auth.ts.

- LoginRequest.email은 형식 검사를 하지 않는다. 형식이 틀려도 422가 아니라 계정 없음과 같은 401이 된다.
  로그인 한도 키는 라우터가 앞뒤 공백 제거·소문자로 맞춘다.
- UserOut은 from_attributes로 User 행에서 바로 만든다. 필드를 더하면 그 값이 그대로 화면에 나가므로
  비밀번호 해시 같은 칸을 넣지 않는다.
고칠 때 주의: 필드를 바꾸면 gen:api, tests/test_auth.py.
"""

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    # from_attributes: SQLAlchemy 객체(User)에서 바로 만들 수 있게 한다.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
