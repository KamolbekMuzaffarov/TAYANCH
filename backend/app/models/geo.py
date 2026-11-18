from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import IdMixin, TimestampMixin


class Region(TimestampMixin, Base):
    __tablename__ = "regions"
    soato: Mapped[str] = mapped_column(String(8), primary_key=True)  # official SOATO/MHOBT code
    iso_code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    name_uz: Mapped[str] = mapped_column(String(120), nullable=False)
    name_uzc: Mapped[str] = mapped_column(String(120), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    geometry: Mapped[dict | None] = mapped_column(JSON)  # GeoJSON (PostGIS geometry column in prod migration)
    geometry_source: Mapped[str | None] = mapped_column(String(200))


class District(TimestampMixin, Base):
    __tablename__ = "districts"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    region_soato: Mapped[str] = mapped_column(ForeignKey("regions.soato"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code_source: Mapped[str] = mapped_column(String(200), nullable=False)
    is_official_code: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    centroid_lon: Mapped[float | None] = mapped_column(Float)
    centroid_lat: Mapped[float | None] = mapped_column(Float)
    geometry: Mapped[dict | None] = mapped_column(JSON)
    previous_codes: Mapped[list | None] = mapped_column(JSON)  # historical mapping


class Facility(IdMixin, TimestampMixin, Base):
    __tablename__ = "facilities"
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    facility_type: Mapped[str] = mapped_column(String(40), nullable=False)  # ovabmu|perinatal_center|maternity|polyclinic|lab
    level: Mapped[str] = mapped_column(String(20), nullable=False)  # republic|regional|district
    region_soato: Mapped[str] = mapped_column(ForeignKey("regions.soato"), nullable=False, index=True)
    district_code: Mapped[str | None] = mapped_column(ForeignKey("districts.code"))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # The ONE facility administrator (docs/AUTH_ADMIN_CONTRACT.md). A single nullable column = at most one admin per
    # facility; the unique index = a person administers at most one facility. Both hold at the database level.
    admin_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
