"""Register existing project media and export task-ready URLs without copying files."""
import argparse
from contextlib import redirect_stdout
import csv
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', help='Project short name')
    parser.add_argument('files', nargs='*', help='Paths relative to the configured source folder')
    parser.add_argument('--all', action='store_true', help='Scan the configured source folder recursively')
    arguments = parser.parse_args()
    if not arguments.files and not arguments.all:
        parser.error('Specify file paths or --all.')
    from pybossa.core import create_app, db
    with redirect_stdout(sys.stderr):
        app = create_app(run_as_server=False)
    from pybossa.core import project_repo
    from pybossa.model.user import User
    from pybossa import media_storage as storage
    with app.app_context():
        project = project_repo.get_by(short_name=arguments.project)
        if project is None:
            parser.error('Project not found.')
        admin = db.session.query(User).filter_by(admin=True).first()
        if admin is None:
            parser.error('An administrator account is required.')
        files = list(arguments.files)
        if arguments.all:
            config = storage.locations(project)
            folder = (storage.root_path(config.source_root_key) / storage.relative_path(config.source_directory)).resolve()
            if not any(folder == storage.root_path(key) or storage.root_path(key) in folder.parents
                       for key in storage.roots()):
                parser.error('Source folder points outside approved media locations.')
            extensions = {'.wav', '.mp3', '.ogg', '.opus', '.flac', '.webm', '.m4a',
                          '.mp4', '.jpg', '.jpeg', '.png', '.webp', '.gif'}
            for directory, subdirectories, names in os.walk(str(folder), followlinks=False):
                subdirectories[:] = [name for name in subdirectories if not name.startswith('.')]
                for name in sorted(names):
                    if not name.startswith('.') and Path(name).suffix.lower() in extensions:
                        files.append((Path(directory) / name).relative_to(folder).as_posix())
        writer = csv.writer(sys.stdout)
        writer.writerow(['filename', 'url', 'media_type'])
        failed = 0
        for filename in dict.fromkeys(files):
            try:
                asset = storage.register_source(project, admin, filename)
                db.session.commit()
                writer.writerow([filename, storage.asset_url(asset, external=True), asset.mimetype])
            except Exception as exc:
                db.session.rollback()
                failed += 1
                print('Could not register %s: %s' % (filename, exc), file=sys.stderr)
        return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
