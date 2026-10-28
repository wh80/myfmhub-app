import os
import shutil
import uuid
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote, urlparse

from flask import current_app, redirect, send_file
from sqlalchemy import func

from app import db
from app.models import File, Site, Building, Zone, Job, Asset, JobSchedule, Person, Supplier

# Files are stored privately in DigitalOcean Spaces (the S3 API) as '<organisation id>/<file id>.<ext>',
# and viewed through a link that's signed for a few minutes. With no DO_SPACES_BUCKET (dev and tests)
# they're saved under LOCAL_FILE_FOLDER instead.

MAX_FILE_MB = 20
VIEW_LINK_SECONDS = 300

# the types that can be uploaded, by extension, with the content type they're served as. The content
# type comes from here, never from the browser, and nothing that can run script (html, svg) is allowed
FILE_TYPES = {
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'txt': 'text/plain',
    'csv': 'text/csv',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'webp': 'image/webp',
}
# opened in the browser by View. Everything else downloads
INLINE_TYPES = {'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


@dataclass
class FileParent:
    """A kind of record files link to. key is the permission resource, the File relationship and the
    blueprint name, so its Files tab is '<key>.files' and its page '<key>.view'."""
    key: str
    model: type
    label: str
    plural: str
    name: callable


FILE_PARENTS = {parent.key: parent for parent in [
    FileParent('sites', Site, 'Site', 'Sites', lambda site: site.name),
    FileParent('buildings', Building, 'Building', 'Buildings', lambda building: building.name),
    FileParent('zones', Zone, 'Zone', 'Zones', lambda zone: zone.name),
    FileParent('assets', Asset, 'Asset', 'Assets', lambda asset: asset.description),
    FileParent('jobs', Job, 'Job', 'Jobs', lambda job: job.description),
    FileParent('job_schedules', JobSchedule, 'Job Schedule', 'Job Schedules', lambda job_schedule: job_schedule.description),
    FileParent('people', Person, 'Person', 'People', lambda person: f'{person.first_name} {person.last_name}'),
    FileParent('suppliers', Supplier, 'Supplier', 'Suppliers', lambda supplier: supplier.name),
]}


def file_links(file):
    """[(parent, [records])] for the kinds of record the file is linked to."""
    return [(parent, getattr(file, parent.key)) for parent in FILE_PARENTS.values() if getattr(file, parent.key)]


### SIZES ###

def size_text(size_mb):
    """A file's size for lists: '0.35 MB', or '< 0.01 MB' for tiny files."""
    if size_mb is None:
        return '-'
    if size_mb < Decimal('0.01'):
        return '< 0.01 MB'
    return f'{size_mb:,.2f} MB'


def storage_used_mb(organisation_id):
    return db.session.query(func.coalesce(func.sum(File.size_mb), 0)).filter(File.organisation_id == organisation_id).scalar()


def storage_text(size_mb):
    """Storage Used on My Organisation, in GB once it passes 1 GB."""
    size_mb = Decimal(size_mb or 0)
    if size_mb >= 1024:
        return f'{size_mb / 1024:,.2f} GB'
    return f'{size_mb:,.2f} MB'


### STORAGE ###

def using_spaces():
    config = current_app.config
    return all(config.get(name) for name in ['DO_SPACES_ENDPOINT', 'DO_SPACES_KEY', 'DO_SPACES_SECRET', 'DO_SPACES_BUCKET'])


def spaces_client():
    import boto3
    from botocore.config import Config

    config = current_app.config
    endpoint = config['DO_SPACES_ENDPOINT']
    if '://' not in endpoint:
        endpoint = f'https://{endpoint}'
    # the region is the first part of the host, e.g. lon1 in https://lon1.digitaloceanspaces.com
    region = urlparse(endpoint).hostname.split('.')[0]
    return boto3.client(
        's3',
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=config['DO_SPACES_KEY'],
        aws_secret_access_key=config['DO_SPACES_SECRET'],
        # boto3 adds CRC32 checksums to every upload by default, which S3-compatible services like
        # Spaces don't all accept. Only send them where the API requires one
        config=Config(request_checksum_calculation='when_required', response_checksum_validation='when_required'),
    )


def local_path(key):
    # keys are built from ids only (see create_file), so they can't point outside the folder
    return os.path.join(current_app.config['LOCAL_FILE_FOLDER'], *key.split('/'))


def store(key, stream, content_type):
    if using_spaces():
        spaces_client().upload_fileobj(stream, current_app.config['DO_SPACES_BUCKET'], key, ExtraArgs={'ContentType': content_type, 'ACL': 'private'})
        return
    path = local_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as out:
        shutil.copyfileobj(stream, out)


def open_stored(key):
    """A readable stream of a stored object. Close it when done."""
    if using_spaces():
        return spaces_client().get_object(Bucket=current_app.config['DO_SPACES_BUCKET'], Key=key)['Body']
    return open(local_path(key), 'rb')


def stored_download(key, content_type, download_name, inline=False):
    """A response that serves a stored object: a short-lived signed link in Spaces, the file locally."""
    if using_spaces():
        disposition = f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(download_name)}"
        url = spaces_client().generate_presigned_url(
            'get_object',
            Params={
                'Bucket': current_app.config['DO_SPACES_BUCKET'],
                'Key': key,
                'ResponseContentType': content_type,
                'ResponseContentDisposition': disposition,
            },
            ExpiresIn=VIEW_LINK_SECONDS,
        )
        return redirect(url)
    return send_file(local_path(key), mimetype=content_type, as_attachment=not inline, download_name=download_name)


def remove_stored(keys):
    """Delete these objects from storage. A failure is logged, not raised: the records are already gone."""
    keys = list(keys)
    if not keys:
        return
    try:
        if using_spaces():
            client = spaces_client()
            for start in range(0, len(keys), 1000):
                client.delete_objects(
                    Bucket=current_app.config['DO_SPACES_BUCKET'],
                    Delete={'Objects': [{'Key': key} for key in keys[start:start + 1000]], 'Quiet': True},
                )
        else:
            for key in keys:
                if os.path.exists(local_path(key)):
                    os.remove(local_path(key))
    except Exception:
        current_app.logger.exception(f'Failed to delete {len(keys)} stored file(s): {keys}')


def remove_stored_organisation(organisation_id):
    """Delete everything stored under an organisation's folder, after the organisation is deleted."""
    prefix = f'{organisation_id}/'
    try:
        if using_spaces():
            client = spaces_client()
            bucket = current_app.config['DO_SPACES_BUCKET']
            keys = [item['Key'] for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix) for item in page.get('Contents', [])]
            remove_stored(keys)
        else:
            shutil.rmtree(local_path(prefix.rstrip('/')), ignore_errors=True)
    except Exception:
        current_app.logger.exception(f'Failed to delete the stored files for organisation {organisation_id}')


def view_response(file):
    """View: images and PDFs open in the browser, other types download, both under the uploaded name."""
    return stored_download(file.storage_key, file.content_type, file.filename, inline=file.content_type in INLINE_TYPES)


### FILES ###

def extension(filename):
    return filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''


def create_file(upload, description, expires, organisation_id, link_to=None, file_category_id=None):
    """Store an upload (a validated FileStorage) and save its File, linked to link_to if given. Commits.
    If saving the record fails, the stored object is removed again."""
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)

    file_id = uuid.uuid4()
    ext = extension(upload.filename)
    file = File(
        id=file_id,
        description=description,
        expires=expires,
        size_mb=(Decimal(size) / Decimal(1024 * 1024)).quantize(Decimal('0.0001')),
        filename=os.path.basename(upload.filename.replace('\\', '/'))[:255],
        content_type=FILE_TYPES[ext],
        storage_key=f'{organisation_id}/{file_id}.{ext}',
        organisation_id=organisation_id,
        file_category_id=file_category_id,
    )
    store(file.storage_key, upload.stream, file.content_type)
    try:
        db.session.add(file)
        if link_to is not None:
            link_to.files.append(file)
        db.session.commit()
    except Exception:
        db.session.rollback()
        remove_stored([file.storage_key])
        raise
    return file


def delete_files(files):
    """Delete the files' records (and so all their links), then the stored objects. Commits."""
    keys = [file.storage_key for file in files]
    for file in files:
        db.session.delete(file)
    db.session.commit()
    remove_stored(keys)
