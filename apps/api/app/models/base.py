"""모든 모델의 공통 부분.

- naming_convention: 제약 조건 이름을 규칙대로 붙인다. Alembic이 나중에 제약을 바꾸거나 지울 때 이름이 필요하다.
- TimestampedTable: 명세의 "모든 테이블에 id, created_at, updated_at"을 한 곳에서 정의한다.
  Django 모델의 공통 추상 모델(abstract = True)과 같은 역할이다.
- 상태 값은 Python Enum으로 정의하고, DB에는 문자열 + CHECK 제약으로 저장한다(enum_type).
  PostgreSQL의 ENUM 타입을 쓰지 않는 이유: 값을 추가할 때 마이그레이션이 단순하다(DECISIONS 2026-11-16).

고칠 때 주의:
- NAMING_CONVENTION을 바꾸면 이미 만들어진 제약 이름과 코드가 기대하는 이름이 달라진다. 옛 마이그레이션이
  이름으로 제약을 지우거나 바꾸므로(예: step_status CHECK 교체 fdef064b5c87) 바꾸지 않는다.
- enum 값을 더하면 CHECK 제약이 바뀌어야 한다. autogenerate는 CHECK 값 변화를 감지하지 못하므로 제약을 지웠다
  다시 만드는 마이그레이션을 손으로 쓴다(fdef064b5c87 머리 설명과 op.f 사용 참고).
- length=32: enum 값 문자열의 최대 길이. 이보다 긴 값을 더하면 컬럼 길이 마이그레이션도 필요하다.
- updated_at은 그 행이 바뀔 때만 갱신된다. 메시지를 더해도 대화(conversations)의 updated_at은 그대로라, 대화 목록
  정렬에 필요한 곳은 직접 넣는다(예: cancel.close_for_cancel).
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
    """모든 테이블의 metadata. Alembic env.py가 이 metadata로 autogenerate한다."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampedTable(Base):
    """id·created_at·updated_at을 가진 추상 테이블. 시각은 모두 timezone 있는 값(DB now())이다."""

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
