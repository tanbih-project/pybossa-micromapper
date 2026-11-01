# Private audio and image storage

Recordings, source audio and source images now use private media storage with
project access checks. Public assets such as avatars continue using `/uploads`.

The default location on this Mac is:

```text
/Users/firojalamqcri/Downloads/pybossa-media/
  project_123/
    recordings/
    sources/
```

The scoped subdirectory keeps unrelated Downloads files out of the container.
Docker mounts it at `/data/private-media`. Files remain on the Mac and survive
container rebuilds. The image does not contain these files.

## Choose a location for each project

Open a project's information page and select **Manage audio and images**, or visit:

```text
/project/<project_short_name>/media
```

Project owners and co-owners can upload source files and register existing files.
Server administrators can expand **Change storage locations for this project**
and independently choose:

- A drive and project folder for new recordings.
- A drive and source folder for existing audio and images.

Folder fields are relative to the selected approved drive. For example, choose
the default drive and enter `speech_morocco` for recordings and
`datasets/morocco/audio` for source files. Newly recorded answers go into
`speech_morocco/recordings`. The source folder may already contain files.

Disk selection is administrator-only because granting a project access to a
server folder also grants access to its audio and images. Project owners cannot
change disk paths through project JSON or supply arbitrary server paths.

Changing a project's locations affects new uploads and registrations. Existing
media URLs retain their original registered locations; files are not moved
automatically. Keep old drives mounted until those assets have been migrated.

## Label existing audio and images

Upload a source file through the media page, or enter a path relative to the
configured source folder, such as `speaker_01/clip_001.wav`, and select
**Register existing file**. Registration validates the file and stores a database
reference; it does not copy the file.

Use the resulting URL in task data:

```json
{"url": "/media/0123456789abcdef0123456789abcdef", "question": "Label this clip"}
```

Audio presenters can use it as an `<audio src>`; image presenters can use it as
an `<img src>`. Source URLs require a signed-in PyBossa session and project
access, including the project password when applicable. A published project is
readable by signed-in users; PyBossa's existing password/draft controls determine
project access. A media URL alone grants no permission.

For a large source collection, configure its folder on the media page, then run:

```bash
docker compose exec -T web python -m docker.media PROJECT_SHORT_NAME --all > media-tasks.csv
```

The CSV contains `filename`, `url` and `media_type` columns and can be used for
task import. To register selected files instead:

```bash
docker compose exec -T web python -m docker.media PROJECT_SHORT_NAME speaker_01/clip_001.wav images/photo_001.jpg
```

Only recognized audio/image extensions are scanned. Every file is still checked
by its actual content. Invalid files are reported to stderr; successfully
registered files remain registered. The command returns a nonzero exit status
if any requested file failed. Nested directory symlinks are not recursively
followed by the bulk scanner; configure the linked directory as the source
folder or register specific linked files instead.

## Save browser recordings

The existing multipart `POST /api/taskrun` endpoint now saves audio/image
attachments in the project's private recording folder. Filenames are generated
UUIDs and derive their extension from validated content, so concurrent uploads
cannot overwrite one another and a browser's `blob` filename is supported.

Session-authenticated presenters must include a CSRF token. The contribution
page supplies a hidden field with ID `pybossa-media-csrf`:

```javascript
const form = new FormData();
form.append('project_id', task.project_id);
form.append('task_id', task.id);
form.append('info', JSON.stringify({ answer: 'recorded' }));
form.append('csrf_token', document.getElementById('pybossa-media-csrf').value);
form.append('file', audioBlob, 'recording.webm');
const response = await fetch('/api/taskrun', {
  method: 'POST', credentials: 'same-origin', body: form
});
if (!response.ok) throw new Error('Recording could not be saved');
const annotation = await response.json();
// annotation.media_url is the private playback URL.
```

Request the task through the normal presenter/scheduler first; the existing
contribution stamp, project and task checks still apply. JSON `info` must be an
object for attachments. API clients can instead supply a valid existing API key
in the `Authorization` header. Do not put API keys in audio/image URLs.

The recording's registry reference and annotation commit together. A failed
annotation save removes its newly uploaded file. Authors can read their own
recordings; project owners, co-owners and administrators can review them. Other
contributors cannot read these recordings. Anonymous recording uploads are
rejected. The annotation editor preserves the file reference while editing
answers; replacing attachment files is not implemented.

Deleting an annotation revokes its private file registry reference. Physical
recordings remain on disk for explicit retention and backup management; there
is no automatic purge of original files. Deleting a source file or changing its
contents on disk affects tasks referencing that file. Registered source files
are not snapshots.

## Use another drive

Create a dedicated folder on the mounted drive and grant the container user
read/write access for recordings. For example, add to `.env`:

```dotenv
PYBOSSA_MEDIA_EXTRA_ROOT=/Volumes/ResearchDrive/pybossa-media
```

Then start with the optional second-drive mount:

```bash
docker compose -f compose.yaml -f compose.media-extra.yaml up --build -d
```

The second drive appears in each project's media settings. Continue supplying
both Compose files on future `up` commands, or set this in `.env` on macOS/Linux:

```dotenv
COMPOSE_FILE=compose.yaml:compose.media-extra.yaml
```

Bind mounts use `create_host_path: false`; an unavailable drive causes startup
to fail rather than silently creating a different storage directory. Docker
Desktop may require you to enable file sharing for the drive. Do not mount an
entire home directory or disk when a dedicated folder is sufficient.

Additional drives can be added through a Compose override and the trusted
server setting `MEDIA_STORAGE_ROOTS` (or `PYBOSSA_MEDIA_ROOTS_JSON` in Docker).
Each entry supplies `path`, optional `display_path`, and optional `read_only`.
Mount source-only libraries read-only and mark their root `read_only: true`;
they can be used for registration but cannot receive new uploads/recordings.

## Existing symlinks and deployment outside Docker

A server-side `MEDIA_STORAGE_ROOTS` example:

```python
TASKRUN_PRIVATE_MEDIA = True
MEDIA_DEFAULT_ROOT = 'recordings'
MEDIA_STORAGE_ROOTS = {
    'recordings': {'path': '/srv/pybossa-media'},
    'library': {'path': '/mnt/research/audio-images', 'read_only': True},
}
MEDIA_MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_CONTENT_LENGTH = MEDIA_MAX_FILE_BYTES + 1024 * 1024
```

The configured root itself may be a symlink to a disk. Existing source folders
or source files may also be symlinks, provided their resolved targets stay
inside one of the approved roots. Symlink escapes are rejected. New writes do
not follow directory symlinks: select the writable target drive/folder directly
for recordings. In Docker, the symlink target must also be mounted at a path
visible inside the container; a host-only symlink target is not sufficient.

Keep all approved roots outside `www`, Flask static folders and public upload
directories. Install `ffprobe` for audio validation; Docker installs it via
FFmpeg. Apply the new Alembic migration before starting an existing standalone
installation. Docker's initialization service applies it automatically.

## Access and validation safeguards

- Every media request checks authentication and project access; recordings also
  check the contributor or project-manager identity.
- Audio supports byte-range responses for seeking, and HEAD requests.
- Responses use private/no-store caching, fixed validated MIME types, nosniff,
  same-origin resource policy and a restrictive content policy.
- Supported formats: WAV, MP3, Ogg/Opus, FLAC, WebM audio, M4A, JPEG, PNG, WebP
  and GIF. SVG, HTML, scripts and video uploads are rejected.
- Uploads are limited to 256 MB by default and images to 25 million pixels.
  Adjust server settings for a justified workload. Files are streamed to disk,
  checked by Pillow/ffprobe and assigned generated names.
- Registry IDs are random, but access checks remain the security boundary.
- The public `/uploads` route rejects symlinks that escape its upload root.

These changes protect newly registered/private media. **Existing URLs exposed
through a web server's `www` directory or alias remain public until that alias
is removed or restricted.** Register those files privately, update task URLs,
verify playback, then retire the old public route. This change does not move or
rewrite existing production datasets automatically.

Verification commands:

```bash
docker compose exec -T web python -m docker.smoke
docker compose exec -T web python -m docker.media_smoke
```
