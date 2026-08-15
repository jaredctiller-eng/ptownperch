from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, MetaData, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    airbnb_ical_url: Mapped[str] = mapped_column(String(512), default="")


DIRECT_BLOCKING_STATUSES = ("hold", "confirmed")


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("property_id", "external_uid"),
        CheckConstraint("check_out > check_in", name="valid_date_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    source: Mapped[str] = mapped_column(String(16))  # 'airbnb_ical' | 'direct'
    status: Mapped[str] = mapped_column(String(16))  # inquiry|hold|confirmed|checked_out|canceled
    external_uid: Mapped[str | None] = mapped_column(String(255), default=None)
    guest_name: Mapped[str | None] = mapped_column(String(128), default=None)
    guest_email: Mapped[str | None] = mapped_column(String(255), default=None)
    guest_phone: Mapped[str | None] = mapped_column(String(32), default=None)
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)  # exclusive (checkout day)


class CalendarBlock(Base):
    __tablename__ = "calendar_blocks"
    __table_args__ = (CheckConstraint("end_date > start_date", name="valid_date_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)  # exclusive
    reason: Mapped[str] = mapped_column(String(128), default="")
