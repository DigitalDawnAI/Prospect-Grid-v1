import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from src.db import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stripe_session_id = Column(Text, unique=True, nullable=False)
    email = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    progress_percent = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)


class Property(Base):
    __tablename__ = "properties"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False)
    address = Column(Text)
    score = Column(Float)
    status = Column(Text)
    error = Column(Text)
    data = Column(Text)
