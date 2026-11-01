"""Authorized media streaming and project media management."""
import hmac
import json
import re

from flask import (Blueprint, current_app, flash, redirect,
                   render_template, request, send_file, url_for)
from flask_login import current_user, login_required
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import BadRequest, Forbidden, NotFound, Unauthorized
from wtforms.validators import ValidationError
from itsdangerous import BadSignature

from pybossa.auth import ensure_authorized_to
from pybossa.core import db, project_repo, signer
from pybossa.cookies import CookieHandler
from pybossa.password_manager import ProjectPasswdManager
from pybossa.util import get_user_id_or_ip
from pybossa.model.auditlog import Auditlog
from pybossa.model.media import MediaAsset, ProjectMediaStorage
from pybossa import media_storage as storage

blueprint = Blueprint('media', __name__)


@blueprint.after_request
def private_responses(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def project_access(project):
    if project is None:
        raise NotFound()
    if current_user.is_anonymous:
        raise Unauthorized('Sign in to access project media.')
    ensure_authorized_to('read', project)
    manager = ProjectPasswdManager(CookieHandler(
        request, signer, current_app.config.get('PASSWD_COOKIE_TIMEOUT', 1800)))
    try:
        if manager.password_needed(project, get_user_id_or_ip()):
            raise Forbidden('Enter the project password on its project page first.')
    except BadSignature:
        raise Forbidden('The project password cookie is invalid.')


def project_manager(project):
    return current_user.is_authenticated and (
        current_user.admin or current_user.id in project.owners_ids)


def upload_authentication():
    """Session uploads require CSRF; API clients must supply a valid header key."""
    if current_user.is_anonymous:
        raise Unauthorized('Sign in before uploading media.')
    key = request.headers.get('Authorization')
    if key:
        if not hmac.compare_digest(str(key), str(current_user.api_key)):
            raise Unauthorized('Invalid API authorization.')
        return
    try:
        validate_csrf(request.form.get('csrf_token') or request.headers.get('X-CSRFToken')
                      or request.headers.get('X-CSRF-Token'))
    except ValidationError:
        raise BadRequest('A valid CSRF token is required for media uploads.')


@blueprint.route('/media/<asset_id>', methods=['GET', 'HEAD'])
def download(asset_id):
    if not re.fullmatch(r'[a-f0-9]{32}', asset_id):
        raise NotFound()
    if current_user.is_anonymous:
        raise Unauthorized('Sign in to access project media.')
    asset = db.session.query(MediaAsset).get(asset_id)
    if asset is None:
        raise NotFound()
    project = project_repo.get(asset.project_id)
    project_access(project)
    if asset.kind == 'recording' and not (project_manager(project) or current_user.id == asset.owner_id):
        raise Forbidden('This recording is only available to its author and project managers.')
    try:
        stream, metadata = storage.open_asset_file(asset.root_key, asset.relative_path)
    except BadRequest:
        raise NotFound('Media file is unavailable.')
    try:
        response = send_file(stream, mimetype=asset.mimetype, conditional=False,
                             add_etags=False, last_modified=metadata.st_mtime,
                             cache_timeout=0)
        response.content_length = metadata.st_size
        # Explicit length enables efficient byte ranges for audio seeking.
        response.make_conditional(request.environ, accept_ranges=True,
                                  complete_length=metadata.st_size)
    except Exception:
        stream.close()
        raise
    response.call_on_close(stream.close)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Vary'] = 'Cookie, Authorization'
    return response


@blueprint.route('/project/<short_name>/media', methods=['GET', 'POST'])
@login_required
def manage(short_name):
    project = project_repo.get_by(short_name=short_name)
    project_access(project)
    if not project_manager(project):
        raise Forbidden('Project managers can manage media.')
    error, status = None, 200
    if request.method == 'POST':
        asset = None
        try:
            action = request.form.get('action')
            if action == 'storage':
                if not current_user.admin:
                    raise Forbidden('Only server administrators can change disk locations.')
                old = db.session.query(ProjectMediaStorage).get(project.id)
                before = old.dictize() if old else {}
                root_key = request.form.get('root_key', '')
                source_key = request.form.get('source_root_key', '')
                storage.root_path(root_key, writable=True)
                storage.root_path(source_key)
                directory = storage.relative_path(request.form.get('directory', ''))
                source_dir = storage.relative_path(request.form.get('source_directory', ''))
                setting = old or ProjectMediaStorage(project_id=project.id)
                setting.root_key, setting.directory = root_key, directory
                setting.source_root_key, setting.source_directory = source_key, source_dir
                db.session.add(setting)
                db.session.add(Auditlog(
                    project_id=project.id, project_short_name=project.short_name,
                    user_id=current_user.id, user_name=current_user.name,
                    action='update', caller='web', attribute='media_storage',
                    old_value=json.dumps(before),
                    new_value=json.dumps(setting.dictize())))
                flash('Project media locations saved. Existing media links keep their previous locations.', 'success')
            elif action == 'upload':
                file = request.files.get('file')
                if file is None:
                    raise BadRequest('Select an audio file or image.')
                asset = storage.save_upload(project, current_user, file, 'source')
                flash('Source media uploaded.', 'success')
            elif action == 'register':
                asset = storage.register_source(project, current_user, request.form.get('filename', ''))
                flash('Existing source file registered without copying it.', 'success')
            else:
                raise BadRequest('Unknown media action.')
            db.session.commit()
            return redirect(url_for('.manage', short_name=short_name))
        except BadRequest as exc:
            if asset is not None and asset.uploaded:
                storage.cleanup_failed_upload(asset)
            db.session.rollback()
            error, status = exc.description, 400
        except Exception:
            if asset is not None and asset.uploaded:
                storage.cleanup_failed_upload(asset)
            db.session.rollback()
            raise
    config = storage.locations(project)
    assets = db.session.query(MediaAsset).filter_by(project_id=project.id).order_by(
        MediaAsset.created.desc()).limit(200).all()
    return render_template('media/manage.html', title='Project media', project=project,
        config=config, roots=storage.roots(), assets=assets,
        recording_location=storage.describe_location(config.root_key, config.directory + '/recordings'),
        source_location=storage.describe_location(config.source_root_key, config.source_directory),
        asset_url=storage.asset_url, error=error), status
