"""Generate this local installation's secrets once; never overwrite them."""
import os
from pathlib import Path
import secrets

path = Path(__file__).resolve().parents[1] / '.env'
values = {
    'POSTGRES_PASSWORD': secrets.token_hex(24),
    'PYBOSSA_SECRET_KEY': secrets.token_hex(32),
    'PYBOSSA_SIGNING_KEY': secrets.token_hex(32),
    'PYBOSSA_CRYPTOPAN_KEY': secrets.token_hex(16),
    'PYBOSSA_ADMIN_PASSWORD': secrets.token_urlsafe(18),
    'PYBOSSA_PORT': '8085',
    'PYBOSSA_SERVER_NAME': 'localhost:8085',
}
try:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('Existing .env preserved.')
else:
    with os.fdopen(descriptor, 'w') as stream:
        stream.write('\n'.join('%s=%s' % item for item in values.items()) + '\n')
    print('Created .env with local secrets and the admin password (mode 600).')

# Add media defaults to an existing installation without changing its secrets.
existing = dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#') and '=' in line)
media_defaults = {
    'PYBOSSA_MEDIA_ROOT': str(Path.home() / 'Downloads' / 'pybossa-media'),
    'PYBOSSA_MEDIA_GID': str(os.getgid()),
}
missing = {key: value for key, value in media_defaults.items() if key not in existing}
if missing:
    with path.open('a') as stream:
        stream.write('\n' + '\n'.join('%s=%s' % item for item in missing.items()) + '\n')
    existing.update(missing)
media_root = Path(existing['PYBOSSA_MEDIA_ROOT'])
if not media_root.exists():
    media_root.mkdir(parents=True, mode=0o2770)
    media_root.chmod(0o2770)
print('Private media directory: %s' % media_root)
