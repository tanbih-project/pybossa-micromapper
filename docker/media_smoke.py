"""Full application regression checks for private media; all fixtures are removed."""
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import wave
from unittest.mock import patch

from PIL import Image
from flask.testing import FlaskClient
from sqlalchemy import text
from pybossa.core import create_app, db
from pybossa.model.auditlog import Auditlog
from pybossa.model.category import Category
from pybossa.model.project import Project
from pybossa.model.result import Result
from pybossa.model.task import Task
from pybossa.model.task_run import TaskRun
from pybossa.model.user import User
from pybossa.model.media import MediaAsset, ProjectMediaStorage
from pybossa import media_storage as storage


def wav_bytes():
    stream = BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\x00\x00' * 1600)
    return stream.getvalue()


def csrf(response, presenter=False):
    pattern = r'id="pybossa-media-csrf"[^>]*value="([^"]+)"' if presenter else r'name="csrf_token"[^>]*value="([^"]+)"'
    match = re.search(pattern, response.get_data(as_text=True))
    assert match, 'CSRF token missing from rendered page (HTTP %s, input names %s)' % (
        response.status_code, re.findall(r'name=[\"\x27]([^\"\x27]+)', response.get_data(as_text=True)))
    return match.group(1)


app = create_app(run_as_server=False)


class FreshContextClient(FlaskClient):
    def open(self, *args, **kwargs):
        # Production requests each get a fresh g; sharing the fixture's outer
        # app context would incorrectly share Flask-WTF token caches across users.
        with app.app_context():
            return super().open(*args, **kwargs)


app.test_client_class = FreshContextClient
from pybossa.core import task_repo, sentinel
base = 'http://' + app.config['SERVER_NAME']
token = uuid.uuid4().hex[:10]
prefix = 'media-check-' + token
root = Path(tempfile.mkdtemp(prefix=prefix, dir=app.config['MEDIA_STORAGE_ROOTS']['default']['path']))
outside = Path(tempfile.mkdtemp(prefix=prefix + '-outside-', dir=root.parent))
app.config['MEDIA_STORAGE_ROOTS'] = {'default': {'path': str(root), 'display_path': str(root)}}
checks = []
project_ids, user_ids = [], []


def check(label, condition):
    assert condition, label
    checks.append(label)


def client_for(user):
    client = app.test_client()
    if user:
        with client.session_transaction() as session:
            session['_user_id'] = user.name
            session['_fresh'] = True
    return client


with app.app_context():
    public_link = None
    try:
        admin = db.session.query(User).filter_by(name='admin').one()
        owner = User(name=prefix, fullname='Media test owner', email_addr=prefix + '@example.invalid', valid_email=True)
        other = User(name=prefix + '-other', fullname='Media test contributor', email_addr=prefix + '-other@example.invalid', valid_email=True)
        db.session.add_all([owner, other])
        db.session.commit()
        user_ids = [owner.id, other.id]
        category = db.session.query(Category).first()
        project = Project(name=prefix, short_name=prefix, owner_id=owner.id,
                          owners_ids=[owner.id], category_id=category.id,
                          description='Temporary private-media checks', published=True,
                          info={'task_presenter': '<p>Private media check</p>'})
        db.session.add(project)
        db.session.commit()
        project_ids.append(project.id)
        manager, contributor, anonymous = client_for(owner), client_for(other), client_for(None)
        administrator = client_for(admin)
        manage = '/project/%s/media' % project.short_name
        page = manager.get(manage, base_url=base)
        check('manager page renders', page.status_code == 200)
        csrf_token = csrf(page)
        check('management requires CSRF', manager.post(manage, base_url=base, data={'action': 'register', 'filename': 'x.wav'}).status_code == 400)
        check('contributors cannot manage media', contributor.get(manage, base_url=base).status_code == 403)
        check('owners cannot select arbitrary disk locations', manager.post(manage, base_url=base, data={
            'csrf_token': csrf_token, 'action': 'storage', 'root_key': 'default', 'directory': 'elsewhere',
            'source_root_key': 'default', 'source_directory': 'elsewhere/sources'}).status_code == 403)
        audio = wav_bytes()
        response = manager.post(manage, base_url=base, data={'action': 'upload', 'csrf_token': csrf_token,
            'file': (BytesIO(audio), 'speech.wav')}, content_type='multipart/form-data')
        check('source audio upload', response.status_code == 302)
        source = db.session.query(MediaAsset).filter_by(project_id=project.id, kind='source').one()
        source_url = storage.asset_url(source)
        check('source stored outside public uploads', storage.root_path(source.root_key) in (root / source.relative_path).parents)
        response = anonymous.get(source_url, base_url=base)
        check('anonymous source requests denied (HTTP %s, %s)' % (response.status_code, source_url), response.status_code == 401)
        response = contributor.get(source_url, base_url=base)
        check('signed-in project access can load audio', response.status_code == 200 and response.data == audio)
        check('private cache and MIME headers', response.headers['Cache-Control'] == 'private, no-store'
              and response.headers['X-Content-Type-Options'] == 'nosniff' and response.mimetype == 'audio/wav')
        response = contributor.get(source_url, base_url=base, headers={'Range': 'bytes=0-15'})
        check('audio seeking via byte ranges', response.status_code == 206 and response.data == audio[:16])
        response = contributor.head(source_url, base_url=base)
        check('HEAD supports audio length', response.status_code == 200 and response.content_length == len(audio) and not response.data)
        check('invalid ranges rejected', contributor.get(source_url, base_url=base, headers={'Range': 'bytes=999999-'}).status_code == 416)
        public_link = Path(app.config['UPLOAD_FOLDER']) / (prefix + '.wav')
        public_link.symlink_to(root / source.relative_path)
        check('public uploads cannot follow private symlinks', anonymous.get('/uploads/' + public_link.name, base_url=base).status_code == 404)

        image = BytesIO()
        Image.new('RGB', (4, 4), (20, 40, 60)).save(image, format='PNG')
        response = manager.post(manage, base_url=base, data={'action': 'upload', 'csrf_token': csrf_token,
            'file': (BytesIO(image.getvalue()), 'test.png')}, content_type='multipart/form-data')
        check('source image upload', response.status_code == 302)
        picture = db.session.query(MediaAsset).filter_by(project_id=project.id, mimetype='image/png').one()
        response = contributor.get(storage.asset_url(picture), base_url=base)
        check('image annotation media retrieval', response.status_code == 200 and response.data == image.getvalue())
        for name, payload in [('fake.wav', b'<html><script>alert(1)</script></html>'), ('bad.svg', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')]:
            response = manager.post(manage, base_url=base, data={'action': 'upload', 'csrf_token': csrf_token,
                'file': (BytesIO(payload), name)}, content_type='multipart/form-data')
            check('reject active or fake media: ' + name, response.status_code == 400)

        config = storage.locations(project)
        sources = root / config.source_directory
        shared = root / 'shared'
        shared.mkdir()
        (shared / 'existing.wav').write_bytes(audio)
        (sources / 'linked.wav').symlink_to(shared / 'existing.wav')
        response = manager.post(manage, base_url=base, data={'action': 'register', 'csrf_token': csrf_token, 'filename': 'linked.wav'})
        check('register approved symlink without copying', response.status_code == 302)
        linked = db.session.query(MediaAsset).filter_by(project_id=project.id, uploaded=False).one()
        check('symlink URL reads canonical file', contributor.get(storage.asset_url(linked), base_url=base).data == audio)
        command_env = dict(os.environ, PYBOSSA_MEDIA_ROOTS_JSON=json.dumps(app.config['MEDIA_STORAGE_ROOTS']))
        registered = subprocess.run(['python', '-m', 'docker.media', prefix, 'linked.wav'],
            env=command_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        check('bulk registration CLI produces task-ready URLs', registered.returncode == 0
              and 'filename,url,media_type' in registered.stdout
              and storage.asset_url(linked) in registered.stdout)
        (outside / 'private.wav').write_bytes(audio)
        (sources / 'escape.wav').symlink_to(outside / 'private.wav')
        response = manager.post(manage, base_url=base, data={'action': 'register', 'csrf_token': csrf_token, 'filename': 'escape.wav'})
        check('reject symlink outside approved roots', response.status_code == 400)
        for path in ('../shared/existing.wav', '/etc/passwd', '.env', 'folder/../../secret', 'a\\b'):
            response = manager.post(manage, base_url=base, data={'action': 'register', 'csrf_token': csrf_token, 'filename': path})
            check('reject unsafe path ' + path, response.status_code == 400)

        project = db.session.query(Project).get(project_ids[0])
        project.set_password('temporary-media-check-password')
        db.session.commit()
        check('project password applies to streams', contributor.get(source_url, base_url=base).status_code == 403)
        password_page = contributor.get('/project/%s/password' % project.short_name, base_url=base)
        unlocked = contributor.post('/project/%s/password?next=%s' % (project.short_name, source_url), base_url=base,
                         data={'csrf_token': csrf(password_page), 'password': 'temporary-media-check-password'})
        check('project password form submits (HTTP %s)' % unlocked.status_code, unlocked.status_code == 302)
        response = contributor.get(source_url, base_url=base)
        check('password-authorized contributor can stream (HTTP %s)' % response.status_code, response.status_code == 200)
        project = db.session.query(Project).get(project_ids[0])
        project.published = False
        db.session.commit()
        check('draft project access applies', contributor.get(source_url, base_url=base).status_code == 403)
        project = db.session.query(Project).get(project_ids[0])
        project.published = True
        db.session.commit()

        task = Task(project_id=project.id, n_answers=1, info={'text': 'Record a short answer'})
        task_repo.save(task)
        presenter = manager.get('/project/%s/task/%s' % (project.short_name, task.id), base_url=base)
        recording_token = csrf(presenter, presenter=True)
        payload = {'project_id': str(project.id), 'task_id': str(task.id), 'info': '{}',
                   'file': (BytesIO(audio), 'blob')}
        response = manager.post('/api/taskrun', base_url=base, data=payload, content_type='multipart/form-data')
        check('recording API requires CSRF for session auth', response.status_code == 400)
        response = manager.post('/api/taskrun', base_url=base, data={
            'project_id': str(project.id), 'task_id': str(task.id), 'info': '{}',
            'csrf_token': recording_token, 'file': (BytesIO(audio), 'blob')}, content_type='multipart/form-data')
        check('existing recording API stores privately', response.status_code == 200)
        saved = json.loads(response.data)
        recording = db.session.query(MediaAsset).filter_by(project_id=project.id, kind='recording').one()
        check('recording linked to annotation atomically', recording.task_run_id == saved['id'])
        check('recording author can listen', manager.get(saved['media_url'], base_url=base).data == audio)
        check('other contributors cannot read recordings', contributor.get(saved['media_url'], base_url=base).status_code == 403)
        check('administrator can review recordings', administrator.get(saved['media_url'], base_url=base).status_code == 200)
        check('recording not exposed through uploads', anonymous.get('/uploads/' + recording.relative_path, base_url=base).status_code == 404)

        admin_token = csrf(administrator.get(manage, base_url=base))
        other_drive = outside / 'approved-drive'
        other_drive.mkdir()
        app.config['MEDIA_STORAGE_ROOTS']['other'] = {'path': str(other_drive)}
        response = administrator.post(manage, base_url=base, data={
            'csrf_token': admin_token, 'action': 'storage', 'root_key': 'other', 'directory': 'changed-recordings',
            'source_root_key': 'other', 'source_directory': 'changed-sources'})
        check('per-project storage changes', response.status_code == 302)
        check('source links survive location changes', contributor.get(source_url, base_url=base).status_code == 200)
        check('recording links survive location changes', manager.get(saved['media_url'], base_url=base).status_code == 200)
        check('storage change is audited', db.session.query(Auditlog).filter_by(project_id=project.id, attribute='media_storage').count() == 1)

        # A browser MediaRecorder commonly sends a WebM blob without a useful name.
        (root / 'input.wav').write_bytes(audio)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(root / 'input.wav'),
                        '-c:a', 'libopus', '-f', 'webm', str(root / 'input.webm')], check=True)
        webm = (root / 'input.webm').read_bytes()
        next_task = Task(project_id=project.id, n_answers=1, info={'text': 'WebM recording'})
        task_repo.save(next_task)
        next_task_id = next_task.id
        page = manager.get('/project/%s/task/%s' % (project.short_name, next_task_id), base_url=base)
        response = manager.post('/api/taskrun', base_url=base, data={
            'project_id': str(project.id), 'task_id': str(next_task_id), 'info': '{}',
            'csrf_token': csrf(page, presenter=True), 'file': (BytesIO(webm), 'blob')}, content_type='multipart/form-data')
        check('browser WebM blob recording', response.status_code == 200)
        webm_info = json.loads(response.data)
        webm_asset = db.session.query(MediaAsset).get(webm_info['info']['media_asset_id'])
        check('new recording uses selected project drive', webm_asset.root_key == 'other'
              and (other_drive / webm_asset.relative_path).is_file())
        check('blob extension inferred from audio content', webm_asset.mimetype == 'audio/webm'
              and webm_asset.relative_path.endswith('.webm'))
        check('WebM playback returns uploaded bytes', manager.get(webm_info['media_url'], base_url=base).data == webm)

        failing_task = Task(project_id=project.id, n_answers=1, info={'text': 'Rollback test'})
        task_repo.save(failing_task)
        failing_id = failing_task.id
        page = manager.get('/project/%s/task/%s' % (project.short_name, failing_id), base_url=base)
        before_files = set(path for path in other_drive.rglob('*') if path.is_file())
        before_assets = db.session.query(MediaAsset).filter_by(project_id=project.id).count()
        with patch.object(task_repo, 'save', side_effect=RuntimeError('Smoke-check forced rollback')):
            response = manager.post('/api/taskrun', base_url=base, data={
                'project_id': str(project.id), 'task_id': str(failing_id), 'info': '{}',
                'csrf_token': csrf(page, presenter=True), 'file': (BytesIO(audio), 'retry.wav')}, content_type='multipart/form-data')
        check('failed annotation save reported', response.status_code == 500)
        check('failed annotation removes uploaded file', set(path for path in other_drive.rglob('*') if path.is_file()) == before_files)
        check('failed annotation rolls back media registry', db.session.query(MediaAsset).filter_by(project_id=project.id).count() == before_assets)
        response = manager.post('/api/taskrun', base_url=base, headers={'Authorization': 'invalid-test-key'}, data={
            'project_id': str(project.id), 'task_id': str(failing_id), 'info': '{}',
            'file': (BytesIO(audio), 'invalid.wav')}, content_type='multipart/form-data')
        check('fake API header cannot bypass CSRF', response.status_code == 401)
        api_key = db.session.query(User).filter_by(name=prefix).one().api_key
        api_client = app.test_client()
        api_client.get('/project/%s/task/%s' % (project.short_name, failing_id), base_url=base,
                       headers={'Authorization': api_key})
        response = api_client.post('/api/taskrun', base_url=base, headers={'Authorization': api_key}, data={
            'project_id': str(project.id), 'task_id': str(failing_id), 'info': '{}',
            'file': (BytesIO(audio), 'api.wav')}, content_type='multipart/form-data')
        check('valid API-key header uploads without session CSRF', response.status_code == 200)

        limit = app.config['MEDIA_MAX_FILE_BYTES']
        app.config['MEDIA_MAX_FILE_BYTES'] = 4
        response = manager.post(manage, base_url=base, data={'action': 'upload', 'csrf_token': csrf_token,
            'file': (BytesIO(audio), 'large.wav')}, content_type='multipart/form-data')
        check('size limits enforced', response.status_code == 413)
        app.config['MEDIA_MAX_FILE_BYTES'] = limit
        check('failed uploads leave no staging files', not list(root.rglob('*.pending')))
        print('PASS: %s private-media integration checks.' % len(checks))
    finally:
        db.session.rollback()
        if public_link is not None:
            public_link.unlink(missing_ok=True)
        for project_id in project_ids:
            db.session.query(MediaAsset).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(ProjectMediaStorage).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(Result).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(Auditlog).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(TaskRun).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(Task).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.execute(text('DELETE FROM project_stats WHERE project_id=:pid'), {'pid': project_id})
            db.session.query(Project).filter_by(id=project_id).delete(synchronize_session=False)
        if user_ids:
            db.session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.session.commit()
        for member in sentinel.master.zrange('pybossa_feed', 0, -1):
            import pickle
            item = pickle.loads(member)
            if item.get('short_name') == prefix or item.get('project_short_name') == prefix or item.get('name', '').startswith(prefix):
                sentinel.master.zrem('pybossa_feed', member)
        shutil.rmtree(root)
        shutil.rmtree(outside)
        print('Removed all private-media check fixtures.')
