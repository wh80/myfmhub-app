from app.extensions import db
from app.models import Location

# a location's materialised_path is its ancestors' names and its own, from the top down, joined with
# SEPARATOR: 'Head Office > Main Building > Ground Floor'. It's unique per organisation, ignoring case,
# which is what stops two locations with the same name in the same place. Names can't contain '>', so a
# path always splits back into the same names

SEPARATOR = ' > '
MAX_PATH_LENGTH = 1000


def build_path(parent, name):
    name = name.strip()
    return f'{parent.materialised_path}{SEPARATOR}{name}' if parent else name


def normalise_path(text):
    """A path someone typed (an import's location or parent column), with the spacing around each '>'
    tidied, so 'Head Office>Main Building' matches. Doesn't change case."""
    return SEPARATOR.join(part.strip() for part in text.split('>'))


def find_by_path(organisation_id, path):
    """The organisation's location with this path, ignoring case and spacing around '>', or None."""
    return Location.query.filter(
        Location.organisation_id == organisation_id,
        db.func.lower(Location.materialised_path) == normalise_path(path).lower(),
    ).first()


def path_taken(organisation_id, path, location=None):
    """True if another location already has this path, ignoring case. location is the one being edited."""
    query = Location.query.filter(
        Location.organisation_id == organisation_id,
        db.func.lower(Location.materialised_path) == path.lower(),
    )
    if location is not None and location.id is not None:
        query = query.filter(Location.id != location.id)
    return query.first() is not None


def descendants(location):
    """Every location inside this one, at any depth (not the location itself)."""
    found = []
    pending = list(location.children)
    while pending:
        child = pending.pop()
        found.append(child)
        pending.extend(child.children)
    return found


def subtree_ids(location):
    """A subquery of the ids of the location and every location inside it, by path, for filtering
    records 'at this location or inside it'."""
    return db.session.query(Location.id).filter(
        Location.organisation_id == location.organisation_id,
        db.or_(
            Location.id == location.id,
            Location.materialised_path.startswith(location.materialised_path + SEPARATOR, autoescape=True),
        ),
    )


def path_problem(organisation_id, name, parent, location=None):
    """Why a location can't have this name and parent, as (field name, message), or None. location is the
    one being edited (it can't move inside itself, and its sub-locations' paths have to stay short enough)."""
    if '>' in name:
        return 'name', "A name can't contain '>', which separates the names in a location's path."
    if location is not None and parent is not None:
        if parent.id == location.id or parent in descendants(location):
            return 'parent_id', "A location can't be inside itself or one of its own sub-locations."
    path = build_path(parent, name)
    if path_taken(organisation_id, path, location):
        return 'name', f"There is already a location called '{path}'."
    longest = len(path)
    if location is not None and location.id is not None:
        old = len(location.materialised_path)
        longest = max([longest] + [len(path) + len(child.materialised_path) - old for child in descendants(location)])
    if longest > MAX_PATH_LENGTH:
        return 'name', f'Location paths can be up to {MAX_PATH_LENGTH} characters, including the locations inside this one.'
    return None


def set_path(location):
    """Set the location's materialised_path from its parent and name, and its sub-locations' paths after
    it (they start with it). Call after changing a name or parent. Doesn't commit."""
    location.materialised_path = build_path(location.parent, location.name)
    for child in location.children:
        set_path(child)


def location_tree(locations):
    """[(location, [children...]), ...] for the top-level locations, each child the same shape, by name."""
    by_parent = {}
    for location in locations:
        by_parent.setdefault(location.parent_id, []).append(location)

    def branch(parent_id):
        return [(location, branch(location.id)) for location in sorted(by_parent.get(parent_id, []), key=lambda l: (l.name.lower(), l.id))]

    return branch(None)
