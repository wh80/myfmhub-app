from flask import current_app, render_template, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import db
from app.models import Person
from app.services.email import send_email
from app.services.files import remove_stored_organisation
from app.services.people import delete_person

# Deleting an organisation is confirmed from a link emailed to the admin who asked. The link carries a
# signed token naming the organisation and that admin's person, and expires after this long
DELETE_LINK_EXPIRY_HOURS = 1
DELETE_TOKEN_SALT = 'delete-organisation'


def delete_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=DELETE_TOKEN_SALT)


def make_delete_token(person):
    return delete_serializer().dumps({'organisation': str(person.organisation_id), 'person': str(person.id)})


def read_delete_token(token):
    """The token's data, or None when it's been tampered with or has expired."""
    try:
        return delete_serializer().loads(token, max_age=DELETE_LINK_EXPIRY_HOURS * 3600)
    except (BadSignature, SignatureExpired):
        return None


def send_delete_email(person):
    html = render_template(
        'emails/organisations/confirm_delete.html',
        first_name=person.first_name,
        organisation_name=person.organisation.name,
        confirm_url=url_for('my_organisation.confirm_delete', token=make_delete_token(person), _external=True),
        expiry_hours=DELETE_LINK_EXPIRY_HOURS,
    )
    send_email(person.user.email, f'Confirm deleting {person.organisation.name} from MyFmHub', html)


def delete_organisation(organisation):
    """Delete the organisation and everything in it, then its stored files. People go through
    delete_person, so a login is only deleted when this was its last organisation. Commits."""
    organisation_id = organisation.id
    for person in Person.query.filter_by(organisation_id=organisation.id).all():
        delete_person(person)
    # people must go first: the rest (sites, jobs, groups, imports...) cascades from the organisation
    db.session.flush()
    # forget any people list loaded earlier, so the cascade doesn't try to delete them again
    db.session.expire(organisation)
    db.session.delete(organisation)
    db.session.commit()
    remove_stored_organisation(organisation_id)
