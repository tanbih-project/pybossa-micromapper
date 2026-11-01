"""Focused PostgreSQL tests using production annotation models and routes.

Only the legacy application bootstrap (Redis, LDAP, mail, etc.) is replaced.
The editor, ORM models, database, templates, login and CSRF handling are real.
"""
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest
from flask import Flask, redirect
from flask_login import LoginManager, UserMixin, current_user
from flask_wtf.csrf import CSRFProtect
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader, PrefixLoader
from sqlalchemy import Column, Integer, Text, Boolean, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope='session')
def modules():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    core = ModuleType('pybossa.core')
    core.db = SimpleNamespace(Model=declarative_base(), session=None)
    core.project_repo = SimpleNamespace()
    core.task_repo = SimpleNamespace()
    original = sys.modules.get('pybossa.core')
    sys.modules['pybossa.core'] = core

    class Project(core.db.Model):
        __tablename__ = 'project'
        id = Column(Integer, primary_key=True)
        short_name = Column(Text, default='sample')
        name = Column(Text, default='Sample project')
        published = Column(Boolean, default=True)
        protected = Column(Boolean, default=False)
        owners_ids = []

        def needs_password(self):
            return self.protected

    class User(core.db.Model, UserMixin):
        __tablename__ = 'user'
        id = Column(Integer, primary_key=True)
        name = Column(Text, default='author')
        admin = Column(Boolean, default=False)

    service = importlib.import_module('pybossa.annotations')
    view = importlib.import_module('pybossa.view.annotations')
    # Use the real project read policy without importing every legacy service.
    spec = importlib.util.spec_from_file_location(
        'annotation_project_auth', root / 'pybossa/auth/project.py')
    project_auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(project_auth)
    yield SimpleNamespace(core=core, service=service, view=view,
                          refresh_outputs=view._refresh_outputs,
                          Project=Project, User=User,
                          project_auth=project_auth.ProjectAuth(None, None), root=root)
    if original is None:
        sys.modules.pop('pybossa.core', None)
    else:
        sys.modules['pybossa.core'] = original


@pytest.fixture
def env(modules, monkeypatch):
    database_url = os.environ.get('ANNOTATION_TEST_DATABASE_URL')
    if not database_url:
        pytest.skip('Set ANNOTATION_TEST_DATABASE_URL to a disposable PostgreSQL database')
    engine = create_engine(database_url)
    schema = 'annotation_test_' + uuid.uuid4().hex
    connection = engine.connect()
    connection.execute('CREATE SCHEMA ' + schema)
    connection.execute('SET search_path TO ' + schema)
    modules.core.db.Model.metadata.create_all(connection)
    session = sessionmaker(bind=connection)()
    modules.core.db.session = session
    project = modules.Project(id=1)
    user = modules.User(id=1)
    session.add_all([project, user, modules.User(id=2, name='other'),
                     modules.User(id=3, name='admin', admin=True)])
    session.commit()
    task = modules.service.Task(id=1, project_id=1, info={'text': '<script>unsafe</script>'})
    session.add(task)
    session.commit()
    annotation = modules.service.TaskRun(id=7, project_id=1, task_id=1,
                                         user_id=1, info={'label': 'old'})
    session.add(annotation)
    session.commit()
    modules.core.project_repo.get_by = lambda **kw: session.query(modules.Project).filter_by(**kw).first()

    # Project read authorization is the same class used by the full application.
    auth = ModuleType('pybossa.auth')
    def authorize(action, obj):
        from flask import abort
        if not modules.project_auth.can(current_user, action, obj):
            abort(403)
    auth.ensure_authorized_to = authorize
    monkeypatch.setitem(sys.modules, 'pybossa.auth', auth)
    projects_view = ModuleType('pybossa.view.projects')
    projects_view._check_if_redirect_to_password = lambda project: redirect('/password')
    monkeypatch.setitem(sys.modules, 'pybossa.view.projects', projects_view)

    app = Flask(__name__)
    app.config.update(SECRET_KEY='annotation-test-only', TESTING=True,
                      WTF_CSRF_ENABLED=False)
    app.jinja_loader = ChoiceLoader([
        DictLoader({'/base.html': '<html><body>{% block content %}{% endblock %}</body></html>'}),
        FileSystemLoader(str(modules.root / 'pybossa/templates')),
        PrefixLoader({'theme': FileSystemLoader(str(modules.root / 'pybossa/themes/default/templates'))}),
    ])
    csrf = CSRFProtect(app)
    login = LoginManager(app)
    @login.user_loader
    def load_user(user_id):
        return session.query(modules.User).get(int(user_id))
    app.add_url_rule('/project/<short_name>/', 'project.details', lambda short_name: '')
    app.add_url_rule('/project/<short_name>/task/<int:task_id>', 'project.task_presenter', lambda **kw: '')
    app.register_blueprint(modules.view.blueprint, url_prefix='/project')
    refresh = []
    monkeypatch.setattr(modules.view, '_refresh_outputs', lambda *args: refresh.append(args))
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = '1'
        browser_session['_fresh'] = True
    yield SimpleNamespace(**vars(modules), session=session, connection=connection,
                          schema=schema,
                          project=project, user=user, annotation=annotation, task=task,
                          app=app, client=client, refresh=refresh, csrf=csrf)
    session.close()
    connection.execute('DROP SCHEMA ' + schema + ' CASCADE')
    connection.close()
    engine.dispose()
