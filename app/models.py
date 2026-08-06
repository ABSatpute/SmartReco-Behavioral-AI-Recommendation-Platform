from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils import utcnow_naive


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    session_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: utcnow_naive() + timedelta(days=30)
    )

    user: Mapped[User | None] = relationship(back_populates="sessions")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asin: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), index=True)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    product_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stars: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_best_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    bought_in_last_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class UserEvent(Base):
    __tablename__ = "user_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, index=True
    )


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    narrative: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(String(500), default="")
    trigger_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="auto")  # auto | daily_digest | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["RecommendationItem"]] = relationship(
        back_populates="recommendation", cascade="all, delete-orphan"
    )


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"
    __table_args__ = (
        UniqueConstraint("recommendation_id", "product_id", name="uq_reco_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")

    recommendation: Mapped[Recommendation] = relationship(back_populates="items")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="auto")
    steps: Mapped[dict] = mapped_column(JSONB, default=dict)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class EmailDigest(Base):
    __tablename__ = "email_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | sent | failed
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
