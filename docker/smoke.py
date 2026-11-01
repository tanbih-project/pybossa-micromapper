"""Exercise the real deployed app, cleaning up only this check's own records."""
import json
import os
import pickle
import re
import uuid

from sqlalchemy import text
from pybossa.core import create_app, db, sentinel
from pybossa.model.auditlog import Auditlog
from pybossa.model.category import Category
from pybossa.model.project import Project
from pybossa.model.result import Result
from pybossa.model.task import Task
from pybossa.model.task_run import TaskRun
from pybossa.model.user import User


def field(response, name):
    html = response.get_data(as_text=True)
    match = re.search(r'name="%s"[^>]*value="([^"]*)"' % name, html)
    assert match, 'Missing form field: ' + name
    return match.group(1)


app = create_app(run_as_server=False)
from pybossa.core import task_repo
base = 'http://' + app.config['SERVER_NAME']
client = app.test_client()
with app.app_context():
    admin = db.session.query(User).filter_by(name='admin').one()
    slug = 'docker-smoke-' + uuid.uuid4().hex[:10]
    project = Project(name='Docker smoke check ' + slug, short_name=slug,
                      description='Temporary deployment verification project',
                      owner_id=admin.id, owners_ids=[admin.id],
                      category_id=db.session.query(Category).first().id,
                      published=True, info={'task_presenter': '<p>Smoke test task</p>'})
    project_id = None
    try:
        db.session.add(project)
        db.session.commit()
        project_id = project.id
        task = Task(project_id=project.id, n_answers=1, info={'text': 'Example annotation'})
        task_repo.save(task)
        annotation = TaskRun(project_id=project.id, task_id=task.id,
                             user_id=admin.id, info={'label': 'before'})
        task_repo.save(annotation)
        annotation_id = annotation.id
        db.session.expire_all()
        assert task.state == 'completed'

        response = client.get('/account/signin', base_url=base)
        assert response.status_code == 200, response.status_code
        response = client.post('/account/signin', base_url=base, data={
            'email': admin.email_addr, 'password': os.environ['PYBOSSA_ADMIN_PASSWORD'],
            'csrf_token': field(response, 'csrf_token')})
        assert response.status_code == 302, 'Administrator login failed'
        url = '/project/%s/annotations/%s/edit' % (slug, annotation_id)
        response = client.get(url, base_url=base)
        assert response.status_code == 200, response.get_data(as_text=True)[:200]
        csrf, version = field(response, 'csrf_token'), field(response, 'version')
        assert client.post(url, base_url=base, data={'info': '{}'}).status_code == 400
        response = client.post(url, base_url=base, data={
            'info': json.dumps({'label': 'after'}), 'version': version, 'csrf_token': csrf})
        assert response.status_code == 302, response.get_data(as_text=True)[:200]
        db.session.expire_all()
        assert annotation.info == {'label': 'after'}
        assert db.session.query(TaskRun).filter_by(task_id=task.id).count() == 1
        assert db.session.query(Auditlog).filter_by(project_id=project.id).count() == 1
        results = db.session.query(Result).filter_by(task_id=task.id).all()
        assert len(results) == 2 and sum(result.last_version for result in results) == 1
        response = client.post(url, base_url=base, data={
            'info': '{"label":"stale"}', 'version': version, 'csrf_token': csrf})
        assert response.status_code == 409, response.status_code
        print('PASS: login, templates, CSRF, completed-annotation edit, audit, result versions and conflict protection.')
    finally:
        db.session.rollback()
        if project_id is not None:
            # Scope every cleanup query to the uniquely generated smoke project.
            from pybossa.cache.projects import clean_project
            clean_project(project_id)
            db.session.query(Result).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(Auditlog).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(TaskRun).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.query(Task).filter_by(project_id=project_id).delete(synchronize_session=False)
            db.session.execute(text('DELETE FROM project_stats WHERE project_id=:pid'), {'pid': project_id})
            db.session.query(Project).filter_by(id=project_id).delete(synchronize_session=False)
            db.session.commit()
            for member in sentinel.master.zrange('pybossa_feed', 0, -1):
                item = pickle.loads(member)
                if item.get('short_name') == slug or item.get('project_short_name') == slug:
                    sentinel.master.zrem('pybossa_feed', member)
            print('Removed temporary smoke-check project.')
