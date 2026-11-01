# MicroMappers PyBossa fork

This fork adds editing of existing annotations by **annotation ID (`task_run.id`)**.
It is based on `Scifabric/pybossa` master at
`d6409eeaf5a88ef36d509e70175905e1b8b834e2` (23 June 2022), the upstream
default-branch head when cloned on 1 November 2026. The upstream dependency
stack has not been modernized in this change.

## Run locally with Docker

See [docker/README.md](docker/README.md) for the Docker Desktop deployment,
administrator login, persistent data, and start/stop commands. The default local
address is `http://localhost:8085`.

For per-project recording drives, private audio/image URLs, existing source
folders and symlinks, see [MEDIA_STORAGE.md](MEDIA_STORAGE.md).

## Edit an annotation

1. Sign in and open the project's information page or contribution page.
2. Click **Edit a saved annotation**.
3. Enter the annotation ID and click **Load annotation**.
4. Update the saved answer as JSON and click **Save changes**.

The direct URLs are:

```text
/project/<project_short_name>/annotations/edit
/project/<project_short_name>/annotations/<task_run_id>/edit
```

For example, annotation `123` in project `speech_emotion_morocco` is edited at:

```text
/project/speech_emotion_morocco/annotations/123/edit
```

The editor supports the existing JSON answer types (objects, arrays, strings,
numbers, booleans and null), displays the original task data, and links to the
original task. This is a general JSON editor; it does not reconstruct each
project's custom annotation controls. The annotation ID is different from the
task ID and can be found in task-run exports or the existing task-run API.

## Permissions and data behavior

- Signed-in users can edit their own annotations. Administrators can edit any
  annotation, including anonymous contributions. Project ownership alone does
  not grant permission to edit another user's answer.
- Every lookup checks the project ID. Draft visibility and project passwords
  continue to apply. Anonymous visitors cannot use the editor.
- Only `task_run.info` changes. The annotation ID, project, task, contributor,
  timestamps, media URL, contribution count and task completion state stay the
  same. Existing uploaded-file metadata must be preserved.
- POST requests require the normal application CSRF token. A version token and
  PostgreSQL row locks reject stale edits with HTTP 409 and preserve the user's
  unsaved text. Reload the saved answer and reconcile the change before retrying.
- An audit entry records the editor, annotation ID, timestamp, previous answer
  and replacement answer in the existing `auditlog` table. The annotation edit
  and audit entry commit together. No database migration is needed.
- For an annotation already included in a current result, the old result is
  marked `last_version=false`, and a new current result references the task's
  existing annotations. Its `info` is empty so stale derived consensus is not
  presented as current. The existing `task_completed` webhook is queued with
  the new result ID so a configured processor can recompute it. Without such a
  processor, derived result data must be recalculated separately.
- Historical result rows still reference the same task-run IDs. The audit log
  preserves old answer values; result rows are not snapshots of old answers.
- Project caches and cached task/task-run exports are invalidated after saving.
  A warning is shown if export invalidation or webhook queuing fails after the
  successful database commit. An administrator can clear exports or rerun the
  result processor in that case.
- The existing `/api/taskrun/<id>` behavior is unchanged. Use the new editor for
  completed annotations; its transaction and audit behavior are separate from
  the legacy API.

The default theme is retained as an unmodified upstream submodule. Application
template overrides add the editor links and use a `theme/` template namespace
to extend the selected theme. Custom themes that replace the standard
`projectcontent` or `content` blocks may need to add the editor link themselves.

## Checkout and maintenance

```bash
git clone --recurse-submodules git@github.com:tanbih-project/pybossa-micromapper.git
cd pybossa-micromapper
git remote add upstream https://github.com/Scifabric/pybossa.git
git checkout codex/private-project-media
```

`origin` is the organization fork; `upstream` is Scifabric. The fork's `master`
branch initially matches upstream. The complete local implementation (annotation
editing, Docker deployment and private media) is maintained on
`codex/private-project-media` for review before merging. The earlier
`codex/edit-project-annotations` branch contains the annotation editor alone.

To bring future upstream changes into the customization branch:

```bash
git fetch upstream
git checkout codex/private-project-media
git merge upstream/master
git submodule update --init --recursive
```

Follow upstream's installation instructions for a running server. This checkout
does not change any deployed MicroMappers instance. Validate against your server's
Python version, settings, custom theme, task presenters and result processor
before deploying.

## Focused regression tests

The suite uses the production editor service, routes, Task/TaskRun/Result/Auditlog
ORM models, PostgreSQL, Flask login and CSRF. It substitutes the legacy application
bootstrap (Redis, mail, LDAP, exporter and webhook infrastructure), a minimal
project/user fixture, the outer page layout and the project password redirect.
It uses the production project read authorization policy. It does not boot the
full upstream application or run the legacy test suite.
GitHub Actions runs this focused suite for pushes and pull requests.

Use a separate environment and a disposable PostgreSQL instance:

```bash
python3.10 -m venv .venv-annotations
.venv-annotations/bin/pip install -r requirements-annotations-test.txt
docker run --detach --name pybossa-annotation-test-db \
  --publish 127.0.0.1:55439:5432 \
  --env POSTGRES_USER=pybossa_test --env POSTGRES_PASSWORD=pybossa_test \
  --env POSTGRES_DB=pybossa_annotations postgres:16-alpine
export ANNOTATION_TEST_DATABASE_URL=postgresql://pybossa_test:pybossa_test@127.0.0.1:55439/pybossa_annotations
.venv-annotations/bin/python -m pytest tests_annotations -q
docker rm --force pybossa-annotation-test-db
```

Each test creates and removes its own uniquely named database schema. Never
point tests at a production database. The dependencies above are isolated test
dependencies, not replacements for upstream's application requirements.
