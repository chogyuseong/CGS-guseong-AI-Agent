"""모든 모델의 공통 부분.

- naming_convention: 제약 조건 이름을 규칙대로 붙인다. Alembic이 나중에 제약을 바꾸거나 지울 때 이름이 필요하다.
- TimestampedTable: 명세의 "모든 테이블에 id, created_at, updated_at"을 한 곳에서 정의한다.
  Django 모델의 공통 추상 모델(abstract = True)과 같은 역할이다.
- 상태 값은 Python Enum으로 정의하고, DB에는 문자열 + CHECK 제약으로 저장한다(enum_type).
  PostgreSQL의 ENUM 타입을 쓰지 않는 이유: 값을 추가할 때 마이그레이션이 단순하다.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampedTable(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def enum_type(enum_class: type[StrEnum], name: str) -> Enum:
    """Enum을 VARCHAR + CHECK 제약으로 저장한다. DB에는 Enum의 값(value)이 들어간다."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )
