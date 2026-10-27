import csv
import io
import re
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import current_app, render_template, request, url_for

from app import db
from app.models import File, FileExport
from app.services.auth import as_utc
from app.services.email import send_email
from app.services.exports import csv_value
from app.services.files import file_links, open_stored, remove_stored, store

# "Download all files" on Export data. It's rare (an organisation leaving, or an audit), so there's no queue
# or scheduled job: the request saves a running FileExport and starts a thread in the web process, which
# zips every file in the organisation with a manifest.csv, stores the zip under '<organisation id>/exports/'
# and emails the requester a link to exports.download_files (which needs them signed in). Zips are
# deleted after EXPORT_KEEP_DAYS, and tidy_file_exports() does that whenever Export data is used
EXPORT_KEEP_DAYS = 7
# a thread dies with its server (a deploy or restart), leaving its export running. One that's been
# running this long is marked failed by tidy_file_exports(), so the organisation can ask again
STALE_RUNNING_HOURS = 3


def active_file_export(organisation_id):
    """The organisation's export that's being prepared, if any. There's only ever one."""
    return FileExport.query.filter_by(organisation_id=organisation_id, status='running').first()


def start_file_export(person):
    """Start preparing a zip of the person's organisation's files, in a thread. Returns (error message, thread):
    the thread is None when it's refused. Commits. Needs a request, whose address the email links back to."""
    tidy_file_exports()
    if active_file_export(person.organisation_id):
        return 'An export of your files is already being prepared. We\'ll email you when it\'s ready.', None
    if not File.query.filter_by(organisation_id=person.organisation_id).count():
        return 'Your organisation has no files to export yet.', None
    export = FileExport(organisation_id=person.organisation_id, person=person, email=person.user.email,
                        status='running', started_at=datetime.now(timezone.utc))
    db.session.add(export)
    db.session.commit()

    thread = threading.Thread(
        target=run_in_thread,
        args=(current_app._get_current_object(), export.id, request.host_url),
        name=f'file-export-{export.id}',
        daemon=True,
    )
    thread.start()
    return None, thread


def zip_name(text):
    """Safe for a file name in a zip on Windows and Mac."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', text).strip(' .')
    return text[:100] or 'file'


def links_text(file):
    return '; '.join(f'{parent.plural}: ' + ', '.join(parent.name(record) for record in records) for parent, records in file_links(file))


def build_zip(export):
    """Zip the organisation's files into a temporary file, with manifest.csv. Returns (zip file, count).
    A file that can't be read from storage is listed in the manifest as missing rather than failing the export."""
    files = File.query.filter_by(organisation_id=export.organisation_id).order_by(File.description, File.id).all()
    out = tempfile.TemporaryFile()
    manifest = io.StringIO()
    writer = csv.writer(manifest)
    writer.writerow(['ID', 'Description', 'File In Zip', 'Original Filename', 'Expires', 'Size (MB)', 'Uploaded (UTC)', 'Linked To', 'Note'])
    used = set()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for file in files:
            # 'files/<description> - <filename>', numbered if two files would get the same name
            base, dot, ext = file.filename.rpartition('.')
            stem = zip_name(f'{file.description} - {base if dot else file.filename}')
            name, n = f'files/{stem}{dot}{ext if dot else ""}', 1
            while name.lower() in used:
                n += 1
                name = f'files/{stem} ({n}){dot}{ext if dot else ""}'
            used.add(name.lower())

            note = ''
            try:
                source = open_stored(file.storage_key)
                try:
                    with archive.open(name, 'w', force_zip64=True) as target:
                        while chunk := source.read(1024 * 1024):
                            target.write(chunk)
                finally:
                    source.close()
            except Exception:
                current_app.logger.exception(f'File export {export.id}: could not read {file.storage_key}')
                note, name = 'Missing from storage', ''
            writer.writerow([csv_value(value) for value in [file.id, file.description, name, file.filename, file.expires, file.size_mb, file.created_at, links_text(file), note]])
        # a BOM so Excel reads it as UTF-8, like the other CSV downloads
        archive.writestr('manifest.csv', '﻿' + manifest.getvalue())
    return out, len(files)


def send_export_email(export, base_url):
    """Email the requester that their export is ready, or that it failed. base_url is the site's address,
    from the request that started it."""
    with current_app.test_request_context(base_url=base_url):
        html = render_template(
            'emails/exports/files_export.html',
            export=export,
            download_url=url_for('exports.download_files', id=export.id, _external=True),
            exports_url=url_for('exports.index', _external=True),
            keep_days=EXPORT_KEEP_DAYS,
        )
    subject = 'Your MyFmHub files export is ready' if export.status == 'ready' else 'Your MyFmHub files export failed'
    send_email(export.email, subject, html)


def run_export(export):
    """Build and store one export that's been marked running. Commits."""
    archive, count = build_zip(export)
    try:
        size = archive.seek(0, 2)
        archive.seek(0)
        key = f'{export.organisation_id}/exports/{export.id}.zip'
        store(key, archive, 'application/zip')
    finally:
        archive.close()
    export.storage_key = key
    export.file_count = count
    export.size_mb = (Decimal(size) / Decimal(1024 * 1024)).quantize(Decimal('0.0001'))
    export.status = 'ready'
    export.finished_at = datetime.now(timezone.utc)
    export.expires_at = export.finished_at + timedelta(days=EXPORT_KEEP_DAYS)
    db.session.commit()


def run_in_thread(app, export_id, base_url):
    """The thread start_file_export starts: build the zip, then email the requester either way."""
    with app.app_context():
        export = db.session.get(FileExport, export_id)
        try:
            run_export(export)
        except Exception:
            db.session.rollback()
            current_app.logger.exception(f'File export {export_id} failed')
            export.status = 'failed'
            export.error = 'Something went wrong preparing the zip. Please try again.'
            export.finished_at = datetime.now(timezone.utc)
            db.session.commit()

        try:
            send_export_email(export, base_url)
        except Exception:
            current_app.logger.exception(f'File export {export_id}: could not send the email to {export.email}')


def tidy_file_exports():
    """Delete zips past their expiry and fail exports whose thread has died. Every organisation's, since
    there's no scheduled job to do it. Commits."""
    now = datetime.now(timezone.utc)
    expire_file_exports(now)
    fail_stale_file_exports(now)


def expire_file_exports(now):
    """Delete the zips of ready exports past their expiry, keeping the rows as history. Commits."""
    expired = [export for export in FileExport.query.filter_by(status='ready') if as_utc(export.expires_at) <= now]
    remove_stored([export.storage_key for export in expired])
    for export in expired:
        export.status = 'expired'
    db.session.commit()
    return len(expired)


def fail_stale_file_exports(now):
    stale = [export for export in FileExport.query.filter_by(status='running') if as_utc(export.started_at) <= now - timedelta(hours=STALE_RUNNING_HOURS)]
    for export in stale:
        export.status = 'failed'
        export.error = 'It took too long and was stopped. Please try again.'
        export.finished_at = now
    db.session.commit()
    return len(stale)
