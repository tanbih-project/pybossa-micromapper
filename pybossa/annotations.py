"""Edit existing contributions without changing their identity or task counts."""
import copy
import hashlib
import json

from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound, Unauthorized

from pybossa.model.auditlog import Auditlog
from pybossa.model.result import Result
from pybossa.model.task import Task
from pybossa.model.task_run import TaskRun


def annotation_version(info):
    """An optimistic concurrency token for the saved annotation."""
    encoded = json.dumps(info, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def load_annotation(session, project_id, annotation_id, user):
    if user.is_anonymous:
        raise Unauthorized('Sign in to edit an annotation.')
    annotation = session.query(TaskRun).filter_by(
        id=annotation_id, project_id=project_id).first()
    if annotation is None:
        raise NotFound('Annotation not found in this project.')
    if not (user.admin or annotation.user_id == user.id):
        raise Forbidden('You can only edit your own annotations.')
    return annotation


def save_annotation(session, project, annotation_id, user, info, version):
    """Save the answer, audit entry and result version in one transaction.

    A task lock serializes edits to different annotations of the same task.
    No new TaskRun is inserted, and scheduling/counters are left untouched.
    """
    annotation_version(info)  # Reject values that PostgreSQL JSON cannot store.
    try:
        annotation = load_annotation(session, project.id, annotation_id, user)
        task = session.query(Task).filter_by(
            id=annotation.task_id, project_id=project.id).with_for_update().first()
        if task is None:
            raise NotFound('The annotation task was not found in this project.')
        annotation = session.query(TaskRun).filter_by(
            id=annotation_id, project_id=project.id).populate_existing().with_for_update().one()
        if not (user.admin or annotation.user_id == user.id):
            raise Forbidden('You can only edit your own annotations.')
        if not version:
            raise BadRequest('Load the annotation before saving it.')
        if version != annotation_version(annotation.info):
            raise Conflict('This annotation changed. Reload it before saving again.')
        if annotation_version(info) == annotation_version(annotation.info):
            session.rollback()
            return annotation, None, False

        old_info = copy.deepcopy(annotation.info)
        # Uploaded-file metadata is managed by the upload API, not this editor.
        if isinstance(old_info, dict):
            for key in ('container', 'file_name', 'media_asset_id', 'media_storage'):
                if key in old_info and (not isinstance(info, dict) or
                                         info.get(key) != old_info[key]):
                    raise BadRequest('Uploaded-file metadata must be preserved.')
        annotation.info = copy.deepcopy(info)
        session.add(Auditlog(
            project_id=project.id, project_short_name=project.short_name,
            user_id=user.id, user_name=user.name, action='update', caller='web',
            attribute='task_run:%s:info' % annotation.id,
            old_value=json.dumps(old_info, ensure_ascii=False, allow_nan=False),
            new_value=json.dumps(info, ensure_ascii=False, allow_nan=False)))

        result = None
        latest = session.query(Result).filter_by(
            project_id=project.id, task_id=task.id, last_version=True).all()
        if any(annotation.id in item.task_run_ids for item in latest):
            for item in latest:
                item.last_version = False
            ids = [row.id for row in session.query(TaskRun).filter_by(
                project_id=project.id, task_id=task.id).order_by(TaskRun.id).all()]
            # Derived consensus must be recalculated; never copy stale result.info.
            result = Result(project_id=project.id, task_id=task.id,
                            task_run_ids=ids, last_version=True, info=None)
            session.add(result)
        session.commit()
        return annotation, result, True
    except Exception:
        session.rollback()
        raise
