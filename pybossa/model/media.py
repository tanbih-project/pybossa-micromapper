"""Project storage settings and private audio/image file references."""
import uuid
from sqlalchemy import Boolean, Integer, String, Text, BigInteger
from sqlalchemy.schema import Column, ForeignKey
from pybossa.core import db
from pybossa.model import DomainObject, make_timestamp


class ProjectMediaStorage(db.Model, DomainObject):
    __tablename__ = 'project_media_storage'
    project_id = Column(Integer, ForeignKey('project.id', ondelete='CASCADE'), primary_key=True)
    root_key = Column(Text, nullable=False)
    directory = Column(Text, nullable=False)
    source_root_key = Column(Text, nullable=False)
    source_directory = Column(Text, nullable=False)


class MediaAsset(db.Model, DomainObject):
    __tablename__ = 'media_asset'
    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    project_id = Column(Integer, ForeignKey('project.id', ondelete='CASCADE'), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    task_run_id = Column(Integer, ForeignKey('task_run.id', ondelete='CASCADE'), nullable=True, index=True)
    kind = Column(String(16), nullable=False)
    root_key = Column(Text, nullable=False)
    relative_path = Column(Text, nullable=False)
    original_name = Column(Text, nullable=False)
    mimetype = Column(Text, nullable=False)
    size = Column(BigInteger, nullable=False)
    uploaded = Column(Boolean, nullable=False, default=True)
    created = Column(Text, nullable=False, default=make_timestamp)
