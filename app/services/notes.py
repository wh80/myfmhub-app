from app.extensions import db
from app.models import Note, Person

# A record's Notes tab: one list of what's happened to it and what people have said about it. Only jobs have notes so
# far. Notes are never edited or deleted, so they can be relied on as a record. Each system note is added in the same
# commit as the action it records. None of these commit


def add_note(job, description, by=None, is_system=True):
    """by is the Person who did it, a name (a supplier or someone using an emailed link), or None for the system."""
    note = Note(
        job=job,
        organisation_id=job.organisation_id,
        description=description,
        is_system=is_system,
        created_by=by if isinstance(by, Person) else None,
        created_by_name=by.full_name if isinstance(by, Person) else by,
    )
    db.session.add(note)
    return note


def step_note(job, action, responder=None, comments=None):
    """A system note for a step of the job, which action describes, e.g. 'accepted the job'. responder is the person
    doing it, who may be acting for the assignee, or None for the assignee's emailed link. Call it before the
    assignee is cleared. Comments go in the note, and that's the record of them."""
    name = job.assignee_name
    if responder is None:
        text, by = f'{name} {action} using their emailed link', name
    elif job.is_assignee(responder):
        text, by = f'{name} {action}', responder
    else:
        text, by = f'{responder.full_name} {action} on behalf of {name}', responder
    return add_note(job, f'{text} with the following comments:\n{comments}' if comments else f'{text}.', by)
