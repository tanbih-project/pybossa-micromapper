"""Private media on administrator-approved disks, outside public upload roots."""
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import uuid
import warnings

from flask import current_app, url_for
from PIL import Image
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge, ServiceUnavailable
from werkzeug.utils import secure_filename
from pybossa.core import db
from pybossa.model.media import MediaAsset, ProjectMediaStorage


def roots():
    configured = current_app.config.get('MEDIA_STORAGE_ROOTS') or {}
    if not configured:
        raise ServiceUnavailable('Private media storage is not configured.')
    return configured


def root_path(key, writable=False):
    spec = roots().get(key)
    if not spec or (writable and spec.get('read_only')):
        raise BadRequest('Select an available writable media location.')
    root = Path(spec['path']).resolve()
    # Never allow private files to fall through the public static/uploads routes.
    public = [current_app.static_folder, current_app.config.get('UPLOAD_FOLDER')]
    for item in filter(None, public):
        other = Path(item).resolve()
        if root == other or other in root.parents or root in other.parents:
            raise ServiceUnavailable('Private media and public upload directories must be separate.')
    if not root.is_dir():
        raise ServiceUnavailable('The selected media drive is not mounted or available.')
    return root


def relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or '\\' in value or '\x00' in value:
        raise BadRequest('Enter a relative folder or file path.')
    parts = value.split('/')
    if value.startswith('/') or any(part in ('', '.', '..') or part.startswith('.') for part in parts):
        raise BadRequest('Hidden files, absolute paths and parent-directory paths are not allowed.')
    return PurePosixPath(*parts).as_posix()


def locations(project):
    saved = db.session.query(ProjectMediaStorage).get(project.id)
    if saved:
        return saved
    key = current_app.config.get('MEDIA_DEFAULT_ROOT', 'default')
    return ProjectMediaStorage(project_id=project.id, root_key=key,
                              directory='project_%s' % project.id,
                              source_root_key=key,
                              source_directory='project_%s/sources' % project.id)


def describe_location(key, directory):
    spec = roots().get(key, {})
    return str(Path(spec.get('display_path', spec.get('path', key))) / directory)


def directory_fd(key, directory, create=False):
    """Walk write paths with directory FDs; never follow a project symlink."""
    root = root_path(key, writable=create)
    fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative_path(directory).split('/'):
            created = False
            if create:
                try:
                    os.mkdir(part, mode=0o770, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if created:
                os.fchmod(next_fd, 0o2770)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise BadRequest('Media folder is unavailable, read-only, or contains a symlink. Use an approved writable folder for uploads.')


def canonical_source(key, path):
    """Resolve administrator-created source symlinks only inside approved roots."""
    candidate = (root_path(key) / relative_path(path)).resolve()
    for allowed_key in roots():
        allowed = root_path(allowed_key)
        if allowed in candidate.parents:
            return allowed_key, relative_path(candidate.relative_to(allowed).as_posix())
    raise BadRequest('The symlink points outside the approved media locations.')


def open_asset_file(key, path):
    """Open a regular file without following subsequently substituted symlinks."""
    path = relative_path(path)
    parent, name = path.rsplit('/', 1) if '/' in path else (None, path)
    fd = directory_fd(key, parent) if parent else os.open(
        str(root_path(key)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError:
        raise NotFound('Media file is unavailable.')
    finally:
        os.close(fd)
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(file_fd)
        raise NotFound('Not a regular media file.')
    return os.fdopen(file_fd, 'rb'), metadata


def detect_media(stream):
    """Allow validated raster images and audio containers, never HTML/SVG/script."""
    stream.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(stream)
            formats = {'JPEG': ('image/jpeg', 'jpg'), 'PNG': ('image/png', 'png'),
                       'WEBP': ('image/webp', 'webp'), 'GIF': ('image/gif', 'gif')}
            result = formats.get(image.format)
            if not result or image.width * image.height > current_app.config.get('MEDIA_MAX_IMAGE_PIXELS', 25000000):
                raise BadRequest('Unsupported image format or image dimensions.')
            image.verify()
            return result
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BadRequest('The image dimensions exceed the permitted limit.')
    except (OSError, ValueError, SyntaxError):
        pass
    stream.seek(0)
    header = stream.read(32)
    signatures = (
        header.startswith((b'ID3', b'fLaC', b'OggS', b'\x1a\x45\xdf\xa3')),
        len(header) >= 12 and header[:4] == b'RIFF' and header[8:12] == b'WAVE',
        len(header) >= 8 and header[4:8] == b'ftyp',
        len(header) >= 2 and header[0] == 0xff and header[1] & 0xe0 == 0xe0,
    )
    if not any(signatures):
        raise BadRequest('Upload a valid supported audio file or raster image.')
    stream.seek(0)
    fd = stream.fileno()
    fd_path = '/proc/self/fd/%s' % fd if Path('/proc/self/fd').is_dir() else '/dev/fd/%s' % fd
    try:
        result = subprocess.run([
            current_app.config.get('MEDIA_FFPROBE', 'ffprobe'), '-v', 'error',
            '-protocol_whitelist', 'file,pipe', '-show_entries',
            'format=format_name:stream=codec_type', '-of', 'json', '-i', fd_path],
            pass_fds=(fd,), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=True)
        data = json.loads(result.stdout)
    except FileNotFoundError:
        raise ServiceUnavailable('Audio validation requires ffprobe on the server.')
    except (subprocess.SubprocessError, ValueError):
        raise BadRequest('Upload a valid supported audio file or raster image.')
    codecs = [item.get('codec_type') for item in data.get('streams', [])]
    if 'audio' not in codecs or 'video' in codecs:
        raise BadRequest('Only audio recordings and raster images are supported.')
    formats = {'wav': ('audio/wav', 'wav'), 'mp3': ('audio/mpeg', 'mp3'),
               'ogg': ('audio/ogg', 'ogg'), 'flac': ('audio/flac', 'flac'),
               'matroska,webm': ('audio/webm', 'webm'),
               'mov,mp4,m4a,3gp,3g2,mj2': ('audio/mp4', 'm4a')}
    media_type = formats.get(data.get('format', {}).get('format_name'))
    if not media_type:
        raise BadRequest('Unsupported audio container.')
    return media_type


def save_upload(project, user, file, kind):
    if kind not in ('source', 'recording'):
        raise BadRequest('Invalid media category.')
    config = locations(project)
    key = config.root_key if kind == 'recording' else config.source_root_key
    directory = config.directory + '/recordings' if kind == 'recording' else config.source_directory
    parent_fd = directory_fd(key, directory, create=True)
    asset_id = uuid.uuid4().hex
    temporary = asset_id + '.pending'
    final_name = None
    try:
        fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o660, dir_fd=parent_fd)
        with os.fdopen(fd, 'w+b') as output:
            os.fchmod(output.fileno(), 0o660)
            total = 0
            limit = current_app.config.get('MEDIA_MAX_FILE_BYTES', 256 * 1024 * 1024)
            while True:
                block = file.stream.read(65536)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise RequestEntityTooLarge('Media file exceeds the configured size limit.')
                output.write(block)
            if not total:
                raise BadRequest('The uploaded file is empty.')
            output.flush()
            mimetype, extension = detect_media(output)
            os.fsync(output.fileno())
        final_name = asset_id + '.' + extension
        os.rename(temporary, final_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        asset = MediaAsset(id=asset_id, project_id=project.id, owner_id=user.id,
                           kind=kind, root_key=key, relative_path=directory + '/' + final_name,
                           original_name=secure_filename(file.filename or '') or final_name,
                           mimetype=mimetype, size=total, uploaded=True)
        db.session.add(asset)
        return asset
    except Exception:
        for name in (temporary, final_name):
            if name:
                try:
                    os.unlink(name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
        raise
    finally:
        os.close(parent_fd)


def cleanup_failed_upload(asset):
    """Only remove this request's generated file if its registry row did not commit."""
    key, path, asset_id = asset.root_key, asset.relative_path, asset.id
    db.session.rollback()
    if db.session.query(MediaAsset).get(asset_id) is not None:
        return
    parent, name = path.rsplit('/', 1)
    fd = directory_fd(key, parent)
    try:
        os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        pass
    finally:
        os.close(fd)


def register_source(project, user, filename):
    config = locations(project)
    requested = config.source_directory + '/' + relative_path(filename)
    key, path = canonical_source(config.source_root_key, requested)
    stream, metadata = open_asset_file(key, path)
    with stream:
        mimetype, _ = detect_media(stream)
    existing = db.session.query(MediaAsset).filter_by(
        project_id=project.id, kind='source', root_key=key, relative_path=path).first()
    if existing:
        return existing
    asset = MediaAsset(id=uuid.uuid4().hex, project_id=project.id, owner_id=user.id,
                       kind='source', root_key=key, relative_path=path,
                       original_name=PurePosixPath(filename).name, mimetype=mimetype,
                       size=metadata.st_size, uploaded=False)
    db.session.add(asset)
    return asset


def asset_url(asset, external=False):
    return url_for('media.download', asset_id=asset.id, _external=external)
