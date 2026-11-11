"""The schema. `listings` is the single source of truth for prices.

Every range, chart and alert is computed from raw listings at read time, using current
rates, so correcting a rate or a season date corrects all of history with it.
"""

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {datetime: DateTime(timezone=True)}


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    category: Mapped[str] = mapped_column(String(40))
    diablo2io_path: Mapped[str | None] = mapped_column(String(300))
    traderie_id: Mapped[str | None] = mapped_column(String(40))


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("platform", "mode", "ladder", "game_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    ladder: Mapped[bool] = mapped_column(Boolean)
    game_version: Mapped[str] = mapped_column(String(20))


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    entries: Mapped[list["WatchItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan", passive_deletes=True
    )


class WatchItem(Base):
    __tablename__ = "watch_items"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "item_id", "market_id", "ethereal", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    # None tracks both; ethereal and normal copies of an item are different prices.
    ethereal: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    watchlist: Mapped[Watchlist] = relationship(back_populates="entries")
    item: Mapped[Item] = relationship(lazy="joined")
    market: Mapped[Market] = relationship(lazy="joined")
    rules: Mapped[list["AlertRule"]] = relationship(
        back_populates="watch", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin"
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    watch_item_id: Mapped[int] = mapped_column(ForeignKey("watch_items.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(20))
    threshold_amount: Mapped[float] = mapped_column(Float)
    threshold_currency: Mapped[str] = mapped_column(String(40))
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=12)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    armed: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column()
    last_reason: Mapped[str | None] = mapped_column(String(100))

    watch: Mapped[WatchItem] = relationship(back_populates="rules")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", "kind"),
        Index("ix_listings_item_market_seen", "item_id", "market_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    # [[["ber", 1.0]], [["jah", 1.0], ["ist", 2.0]]]: any one alternative pays in full.
    alternatives: Mapped[list] = mapped_column(JSON)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    ethereal: Mapped[bool | None] = mapped_column(Boolean)
    url: Mapped[str | None] = mapped_column(String(400))
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
    # When the market said this; `last_seen_at` is when a collector last confirmed it.
    observed_at: Mapped[datetime] = mapped_column()
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class CurrencyRate(Base):
    __tablename__ = "currency_rates"
    __table_args__ = (
        UniqueConstraint("market_id", "currency", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL is an operator's rate for every market; a market's own row beats it.
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"))
    currency: Mapped[str] = mapped_column(String(40))
    value_ist: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))
    samples: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class SeasonRow(Base):
    __tablename__ = "seasons"

    number: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    starts_at: Mapped[datetime] = mapped_column()
    ends_at: Mapped[datetime | None] = mapped_column()
    start_source: Mapped[str] = mapped_column(String(20))
    end_source: Mapped[str | None] = mapped_column(String(20))
    url: Mapped[str | None] = mapped_column(String(400))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    # Sending the same thing twice is impossible by construction: this is unique.
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(400))
    delivery: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column()


class SourceRun(Base):
    __tablename__ = "source_runs"
    __table_args__ = (Index("ix_source_runs_job_started", "job", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="running")
    records: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text)
