from datetime import datetime, timezone

from app import db
from app.models import User, UserSession

# a person is someone's membership of one organisation, and their user is the login they share with their
# people in other organisations. These keep Person.email and User.email the same, and never leave a user
# with no people (unless they're a platform admin). None of them commit


def find_or_create_user(email):
    """The user with this (lower-case) email, or a new one. An email can be in several organisations."""
    user = User.query.filter(User.email == email).first()
    if user is None:
        user = User(email=email)
        db.session.add(user)
    return user


def end_person_sessions(person):
    """Revoke the sessions working in the person's organisation as this person. Their user's sessions in
    other organisations carry on."""
    UserSession.query.filter(
        UserSession.person_id == person.id,
        UserSession.revoked_at.is_(None),
    ).update({'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False)


def remove_user_if_unused(user, person):
    """Delete user (with its codes and sessions) if person was its only one, so nothing else needs the login."""
    if not user.is_platform_admin and not [other for other in user.people if other.id != person.id]:
        db.session.delete(user)


def delete_person(person):
    """Delete a person. Their user goes too if this was their only organisation."""
    user = person.user
    end_person_sessions(person)
    remove_user_if_unused(user, person)
    db.session.delete(person)


def change_person_email(person, email):
    """Move the person to the user with the new email, creating it if needed, rather than changing the
    user's email: the user may be in other organisations, and this organisation mustn't be able to take
    over their login there. The person's sessions end, as they were signed in with the old email."""
    old_user = person.user
    end_person_sessions(person)
    person.user = find_or_create_user(email)
    person.email = email
    remove_user_if_unused(old_user, person)
