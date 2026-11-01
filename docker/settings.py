"""Local Docker configuration. Secrets are supplied by the ignored .env file."""
import os
import json

DEBUG = False
SECRET = os.environ['PYBOSSA_SECRET_KEY']
SECRET_KEY = SECRET
ITSDANGEROUSKEY = os.environ['PYBOSSA_SIGNING_KEY']
CRYPTOPAN_KEY = os.environ['PYBOSSA_CRYPTOPAN_KEY']
SQLALCHEMY_DATABASE_URI = 'postgresql://pybossa:%s@db/pybossa' % os.environ['POSTGRES_PASSWORD']
SQLALCHEMY_TRACK_MODIFICATIONS = False
REDIS_SENTINEL = []
REDIS_HOST = 'redis'
REDIS_PORT = 6379
REDIS_URL = 'redis://redis:6379/0'
REDIS_SOCKET_TIMEOUT = 5
BRAND = 'MicroMappers Local'
TITLE = 'MicroMappers Local'
LOGO = 'default_logo.svg'
COPYRIGHT = 'QCRI'
DESCRIPTION = 'Local PyBossa with annotation editing'
SERVER_NAME = os.environ.get('PYBOSSA_SERVER_NAME', 'localhost:8085')
PREFERRED_URL_SCHEME = 'http'
UPLOAD_METHOD = 'local'
UPLOAD_FOLDER = '/app/uploads'
ACCOUNT_CONFIRMATION_DISABLED = True
ENFORCE_PRIVACY = False
MAIL_SUPPRESS_SEND = True
MAIL_DEFAULT_SENDER = 'MicroMappers Local <admin@localhost.invalid>'
WTF_CSRF_ENABLED = True
WTF_CSRF_SSL_STRICT = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
NEWS_URL = []
UNPUBLISH_PROJECTS = False
SPAM = []
TERMSOFUSE = 'https://docs.pybossa.com/'
DATAUSE = 'https://opendatacommons.org/licenses/by/'
CONTACT_EMAIL = 'admin@localhost.invalid'
PRO_FEATURES = {'auditlog': False, 'webhooks': False, 'updated_exports': False,
                'notify_blog_updates': False, 'project_weekly_report': False,
                'autoimporter': False, 'better_stats': False}
TASKRUN_PRIVATE_MEDIA = True
MEDIA_DEFAULT_ROOT = 'default'
MEDIA_STORAGE_ROOTS = {
    'default': {'path': '/data/private-media',
                'display_path': os.environ['PYBOSSA_MEDIA_ROOT']},
}
if os.environ.get('PYBOSSA_MEDIA_EXTRA_ROOT'):
    MEDIA_STORAGE_ROOTS['extra'] = {
        'path': '/data/private-media-extra',
        'display_path': os.environ['PYBOSSA_MEDIA_EXTRA_ROOT'],
    }
MEDIA_STORAGE_ROOTS.update(json.loads(os.environ.get('PYBOSSA_MEDIA_ROOTS_JSON', '{}')))
MEDIA_MAX_FILE_BYTES = 256 * 1024 * 1024
MEDIA_MAX_IMAGE_PIXELS = 25000000
MAX_CONTENT_LENGTH = MEDIA_MAX_FILE_BYTES + 1024 * 1024
