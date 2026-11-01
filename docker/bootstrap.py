"""Idempotent setup for this installation's empty database."""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from pybossa.core import create_app, db, setup_scheduled_jobs
from pybossa.model.category import Category
from pybossa.model.user import User
from pybossa.leaderboard.jobs import leaderboard

app = create_app(run_as_server=False)
with app.app_context():
    config_text = Path('alembic.ini.template').read_text().replace(
        'postgresql://pybossa:tester@localhost/pybossa',
        app.config['SQLALCHEMY_DATABASE_URI'])
    Path('alembic.ini').write_text(config_text)
    if 'user' not in inspect(db.engine).get_table_names():
        db.create_all()
        command.stamp(Config('alembic.ini'), 'head')
    else:
        command.upgrade(Config('alembic.ini'), 'head')
    for name, short_name in [('Thinking', 'thinking'), ('Volunteer Sensing', 'sensing')]:
        if not db.session.query(Category).filter_by(short_name=short_name).first():
            db.session.add(Category(name=name, short_name=short_name, description=name + ' projects'))
    db.session.commit()
    if not db.session.query(User).count():
        user = User(name='admin', fullname='Local Administrator',
                    email_addr='admin@localhost.invalid', admin=True, pro=True,
                    valid_email=True, consent=True)
        user.set_password(os.environ['PYBOSSA_ADMIN_PASSWORD'])
        db.session.add(user)
        db.session.commit()
        print('Created local administrator: admin (password is in .env).')
    leaderboard()
    setup_scheduled_jobs(app)
print('PyBossa database and background schedules are ready.')
