import json
import re
import ast
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import ModuleType
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import Conflict, Forbidden, NotFound, Unauthorized

URL = '/project/sample/annotations/7/edit'


def save(env, info=None, version=None, user=None):
    return env.service.save_annotation(
        env.session, env.project, 7, user or env.user,
        {'label': 'new'} if info is None else info,
        version or env.service.annotation_version(env.annotation.info))


def post(env, text='{"label": "new"}', **kwargs):
    data = {'info': text, 'version': env.service.annotation_version(env.annotation.info)}
    data.update(kwargs)
    return env.client.post(URL, data=data)


def login_as(env, user_id):
    with env.client.session_transaction() as session:
        session['_user_id'] = str(user_id)


def test_edit_preserves_identity_counts_and_timestamps(env):
    before = env.annotation.dictize()
    annotation, result, changed = save(env)
    assert changed and result is None
    assert annotation.info == {'label': 'new'}
    for key in before.keys() - {'info'}:
        assert getattr(annotation, key) == before[key]
    assert env.session.query(env.service.TaskRun).count() == 1
    assert env.task.state == 'ongoing'
    log = env.session.query(env.service.Auditlog).one()
    assert log.attribute == 'task_run:7:info'
    assert json.loads(log.old_value) == before['info']
    assert json.loads(log.new_value) == annotation.info
    assert log.user_id == env.user.id


def test_completed_annotation_versions_result_and_clears_derived_info(env):
    env.task.state = 'completed'
    old = env.service.Result(project_id=1, task_id=1, task_run_ids=[7], info={'consensus': 'old'})
    env.session.add(old)
    env.session.commit()
    _, result, _ = save(env)
    assert not old.last_version
    assert old.info == {'consensus': 'old'}
    assert result.last_version and result.info is None
    assert result.task_run_ids == [7]
    assert env.task.state == 'completed'
    assert env.session.query(env.service.Result).filter_by(last_version=True).count() == 1


def test_other_users_and_anonymous_cannot_edit(env):
    other = env.session.query(env.User).get(2)
    with pytest.raises(Forbidden):
        save(env, user=other)
    from flask_login import AnonymousUserMixin
    with pytest.raises(Unauthorized):
        save(env, user=AnonymousUserMixin())
    assert env.annotation.info == {'label': 'old'}


def test_admin_can_edit_anonymous_annotation(env):
    env.annotation.user_id = None
    env.session.commit()
    admin = env.session.query(env.User).get(3)
    annotation, _, _ = save(env, user=admin)
    assert annotation.user_id is None
    assert env.session.query(env.service.Auditlog).one().user_id == 3


def test_project_mismatch_is_not_found(env):
    with pytest.raises(NotFound):
        env.service.load_annotation(env.session, 2, 7, env.user)


def test_stale_edit_cannot_overwrite_newer_answer(env):
    stale_version = env.service.annotation_version(env.annotation.info)
    save(env)
    with pytest.raises(Conflict):
        save(env, info={'label': 'lost update'}, version=stale_version)
    assert env.annotation.info == {'label': 'new'}
    assert env.session.query(env.service.Auditlog).count() == 1


def test_noop_does_not_create_audit_or_result(env):
    _, result, changed = save(env, info={'label': 'old'})
    assert not changed and result is None
    assert env.session.query(env.service.Auditlog).count() == 0


def test_audit_failure_rolls_back_answer_and_result(env):
    old = env.service.Result(project_id=1, task_id=1, task_run_ids=[7], info={'consensus': 'old'})
    env.session.add(old)
    env.session.commit()
    original_flush = env.session.flush
    def fail_audit(*args, **kwargs):
        if any(isinstance(obj, env.service.Auditlog) for obj in env.session.new):
            raise IntegrityError('test audit failure', {}, None)
        return original_flush(*args, **kwargs)
    with patch.object(env.session, 'flush', side_effect=fail_audit):
        with pytest.raises(IntegrityError):
            save(env)
    assert env.annotation.info == {'label': 'old'}
    assert old.last_version
    assert env.session.query(env.service.Result).count() == 1


def test_editor_loads_and_escapes_answer_and_task_data(env):
    env.annotation.info = {'label': '</textarea><script>alert(1)</script>'}
    env.session.commit()
    response = env.client.get(URL)
    assert response.status_code == 200
    assert b'&lt;/textarea&gt;' in response.data
    assert b'<script>alert(1)</script>' not in response.data
    assert b'<script>unsafe</script>' not in response.data


def test_web_save_redirects_and_refreshes_outputs(env):
    response = post(env)
    assert response.status_code == 302
    assert response.location.endswith(URL)
    assert env.annotation.info == {'label': 'new'}
    assert len(env.refresh) == 1


@pytest.mark.parametrize('bad_json', ['', '{bad', 'NaN', 'Infinity', '1e999'])
def test_invalid_json_cannot_change_annotation(env, bad_json):
    response = post(env, bad_json)
    assert response.status_code == 400
    assert env.annotation.info == {'label': 'old'}


@pytest.mark.parametrize('answer', ['"text"', '[1, 2]', 'false', 'null', '{}'])
def test_supported_json_answer_shapes(env, answer):
    response = post(env, answer)
    assert response.status_code == 302
    assert env.annotation.info == json.loads(answer)


def test_conflict_preserves_unsaved_text(env):
    stale_version = env.service.annotation_version(env.annotation.info)
    save(env)
    response = post(env, '{"label":"my unsaved edit"}', version=stale_version)
    assert response.status_code == 409
    assert b'my unsaved edit' in response.data
    assert env.annotation.info == {'label': 'new'}


def test_missing_version_rejected(env):
    assert post(env, version='').status_code == 400


def test_web_ownership_and_project_scope(env):
    login_as(env, 2)
    assert env.client.get(URL).status_code == 403
    assert post(env).status_code == 403
    login_as(env, 1)
    env.session.add(env.Project(id=2, short_name='other'))
    env.session.commit()
    assert env.client.get(URL.replace('/sample/', '/other/')).status_code == 404
    assert env.client.get(URL.replace('/7/', '/999/')).status_code == 404


def test_login_required(env):
    with env.client.session_transaction() as session:
        session.clear()
    assert env.client.get(URL).status_code == 401
    assert post(env).status_code == 401


def test_project_password_and_draft_policy_applies(env):
    env.project.protected = True
    env.session.commit()
    assert env.client.get(URL).location.endswith('/password')
    env.project.protected = False
    env.project.published = False
    env.session.commit()
    assert env.client.get(URL).status_code == 403


def test_csrf_required_and_valid_form_can_save(env):
    env.app.config['WTF_CSRF_ENABLED'] = True
    assert post(env).status_code == 400
    response = env.client.get(URL)
    token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
    assert post(env, csrf_token=token).status_code == 302


@pytest.mark.parametrize('annotation_id', ['0', '-5', 'abc'])
def test_lookup_rejects_invalid_ids(env, annotation_id):
    response = env.client.get('/project/sample/annotations/edit?annotation_id=' + annotation_id)
    assert response.status_code == 400


def test_lookup_redirects_to_selected_annotation(env):
    response = env.client.get('/project/sample/annotations/edit?annotation_id=7')
    assert response.status_code == 302
    assert response.location.endswith(URL)


def test_upload_metadata_is_preserved(env):
    env.annotation.info = {'label': 'old', 'file_name': 'file.png', 'container': 'user_1'}
    env.session.commit()
    assert post(env).status_code == 400
    assert env.annotation.info['file_name'] == 'file.png'


def test_concurrent_saves_reject_the_losing_stale_edit(env):
    version = env.service.annotation_version(env.annotation.info)
    barrier = Barrier(2)
    def writer(label):
        conn = env.connection.engine.connect()
        conn.execute('SET search_path TO ' + env.schema)
        session = sessionmaker(bind=conn)()
        project = session.query(env.Project).get(1)
        user = session.query(env.User).get(1)
        barrier.wait(timeout=5)
        try:
            env.service.save_annotation(session, project, 7, user, {'label': label}, version)
            return 'saved'
        except Conflict:
            return 'conflict'
        finally:
            session.close()
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(writer, ['first', 'second']))
    assert sorted(outcomes) == ['conflict', 'saved']
    env.session.expire_all()
    assert env.annotation.info['label'] in ('first', 'second')
    assert env.session.query(env.service.Auditlog).count() == 1


def test_real_theme_loader_keeps_theme_access_and_editor_overrides(env):
    # Execute setup_theme itself without importing the legacy core bootstrap.
    from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader
    from flask import Flask
    import os
    tree = ast.parse((env.root / 'pybossa/core.py').read_text())
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
                    and item.name == 'setup_theme')
    namespace = dict(os=os, ChoiceLoader=ChoiceLoader,
                     FileSystemLoader=FileSystemLoader, PrefixLoader=PrefixLoader)
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<setup_theme>', 'exec'), namespace)
    app = Flask('pybossa', root_path=str(env.root / 'pybossa'))
    app.config['THEME'] = 'default'
    namespace['setup_theme'](app)
    loader = app.jinja_loader
    for path in ('projects/project.html', 'projects/presenter.html'):
        local, _, _ = loader.get_source(app.jinja_env, path)
        original, _, _ = loader.get_source(app.jinja_env, 'theme/' + path)
        assert 'Edit a saved annotation' in local
        assert 'Edit a saved annotation' not in original
    assert loader.get_source(app.jinja_env, 'annotations/editor.html')


def test_completed_web_save_refreshes_with_new_result(env):
    env.session.add(env.service.Result(project_id=1, task_id=1, task_run_ids=[7]))
    env.session.commit()
    assert post(env).status_code == 302
    assert env.refresh[0][2].last_version
    assert env.refresh[0][2].id is not None


def test_post_commit_refresh_failures_warn_without_losing_saved_answer(env, monkeypatch):
    # Infrastructure failures happen after the atomic save and must not mask it.
    cache = ModuleType('pybossa.cache.projects')
    cache.clean_project = lambda project_id: (_ for _ in ()).throw(RuntimeError('cache unavailable'))
    listeners = ModuleType('pybossa.model.event_listeners')
    listeners.push_webhook = lambda *args: (_ for _ in ()).throw(RuntimeError('queue unavailable'))
    monkeypatch.setitem(sys.modules, 'pybossa.cache.projects', cache)
    monkeypatch.setitem(sys.modules, 'pybossa.model.event_listeners', listeners)
    monkeypatch.setattr(env.view, '_refresh_outputs', env.refresh_outputs)
    monkeypatch.setattr(env.Project, 'dictize', lambda self: {'id': self.id}, raising=False)
    env.session.add(env.service.Result(project_id=1, task_id=1, task_run_ids=[7]))
    env.session.commit()
    assert post(env).status_code == 302
    assert env.annotation.info == {'label': 'new'}
    with env.client.session_transaction() as browser_session:
        messages = [message for _, message in browser_session['_flashes']]
    assert any('Cached exports' in message for message in messages)
    assert any('Result processing' in message for message in messages)
