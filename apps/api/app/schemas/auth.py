from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    # from_attributes: SQLAlchemy 객체(User)에서 바로 만들 수 있게 한다.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
