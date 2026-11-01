"""Project-scoped, CSRF-protected editor for existing task-run annotations."""
import json

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from werkzeug.exceptions import BadRequest, Conflict

from pybossa.annotations import annotation_version, load_annotation, save_annotation
from pybossa.core import db, project_repo, task_repo

blueprint = Blueprint('annotations', __name__)


def _project(short_name):
    # Reuse the project's existing draft authorization and password checks.
    from pybossa.auth import ensure_authorized_to
    from pybossa.view.projects import _check_if_redirect_to_password
    project = project_repo.get_by(short_name=short_name)
    if project is None:
        abort(404)
    ensure_authorized_to('read', project)
    if project.needs_password():
        response = _check_if_redirect_to_password(project)
        if response:
            return project, response
    return project, None


def _reject_constant(value):
    raise ValueError('Invalid JSON value: %s' % value)


@blueprint.route('/<short_name>/annotations/edit')
@login_required
def find_annotation(short_name):
    project, response = _project(short_name)
    if response:
        return response
    error = None
    raw_id = request.args.get('annotation_id', '')
    if raw_id:
        try:
            annotation_id = int(raw_id)
            if annotation_id < 1:
                raise ValueError()
        except ValueError:
            error = 'Enter a positive annotation ID.'
        else:
            return redirect(url_for('.edit_annotation', short_name=short_name,
                                    annotation_id=annotation_id))
    return render_template('annotations/editor.html', project=project,
                           annotation=None, error=error,
                           raw_id=raw_id, title='Edit annotation'), 400 if error else 200


@blueprint.route('/<short_name>/annotations/<int:annotation_id>/edit',
                 methods=['GET', 'POST'])
@login_required
def edit_annotation(short_name, annotation_id):
    project, response = _project(short_name)
    if response:
        return response
    annotation = load_annotation(db.session, project.id, annotation_id, current_user)
    info_text = json.dumps(annotation.info, ensure_ascii=False, indent=2)
    version = annotation_version(annotation.info)
    error, status = None, 200
    if request.method == 'POST':
        info_text = request.form.get('info', '')
        version = request.form.get('version', '')
        try:
            info = json.loads(info_text, parse_constant=_reject_constant)
            # Also catches overflowing JSON floats (e.g. 1e999).
            annotation_version(info)
        except (ValueError, TypeError, OverflowError):
            error, status = 'Enter a valid JSON annotation.', 400
        else:
            try:
                annotation, result, changed = save_annotation(
                    db.session, project, annotation_id, current_user, info, version)
            except (BadRequest, Conflict) as exc:
                error, status = exc.description, exc.code
            else:
                if changed:
                    _refresh_outputs(project, annotation, result)
                flash('Annotation saved.' if changed else 'No changes to save.', 'success')
                return redirect(url_for('.edit_annotation', short_name=short_name,
                                        annotation_id=annotation_id))
    return render_template('annotations/editor.html', project=project,
                           annotation=annotation, info_text=info_text,
                           version=version, error=error, title='Edit annotation'), status


def _refresh_outputs(project, annotation, result):
    from pybossa.cache.projects import clean_project
    from pybossa.model.event_listeners import push_webhook
    # The database transaction has succeeded. Report downstream failures without
    # suggesting that a successfully persisted edit was rolled back.
    try:
        clean_project(project.id)
        task_repo._delete_zip_files_from_store(project)
    except Exception:
        current_app.logger.exception('Could not refresh exports after annotation edit')
        flash('Saved. Cached exports could not be cleared; contact the administrator.', 'warning')
    if result is not None:
        try:
            push_webhook(project.dictize(), annotation.task_id, result.id)
        except Exception:
            current_app.logger.exception('Could not enqueue the revised-result webhook')
            flash('Saved. Result processing could not be queued; contact the administrator.', 'warning')
