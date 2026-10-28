import re
from collections import namedtuple
from app import db
from app.models import (
    JobCategory, AssetCategory, AssetCondition, BuildingCondition, SupplierCategory, FileCategory,
    Job, JobSchedule, Asset, Building, Supplier, File,
)
from app.services.scheduling import uk_today

# each organisation's categories and conditions (see ReferenceItem in models.py), managed under
# /reference-data with reference_data.manage. used_by lists (model, column, label) for the records
# that point at an item: deleting the item clears those columns (and condition_set, for a condition)
ReferenceType = namedtuple('ReferenceType', 'slug label singular model icon used_by')

REFERENCE_TYPES = {reference_type.slug: reference_type for reference_type in [
    ReferenceType('job-categories', 'Job Categories', 'job category', JobCategory, 'bi-tools',
                  [(Job, 'job_category_id', 'jobs'), (JobSchedule, 'job_category_id', 'job schedules')]),
    ReferenceType('asset-categories', 'Asset Categories', 'asset category', AssetCategory, 'bi-box-seam',
                  [(Asset, 'asset_category_id', 'assets')]),
    ReferenceType('asset-conditions', 'Asset Conditions', 'asset condition', AssetCondition, 'bi-heart-pulse',
                  [(Asset, 'asset_condition_id', 'assets')]),
    ReferenceType('building-conditions', 'Building Conditions', 'building condition', BuildingCondition, 'bi-building',
                  [(Building, 'building_condition_id', 'buildings')]),
    ReferenceType('supplier-categories', 'Supplier Categories', 'supplier category', SupplierCategory, 'bi-truck',
                  [(Supplier, 'supplier_category_id', 'suppliers')]),
    ReferenceType('file-categories', 'File Categories', 'file category', FileCategory, 'bi-folder',
                  [(File, 'file_category_id', 'files')]),
]}

MAX_LENGTH = 100
# the most values one Add in bulk can take
MAX_BULK_ITEMS = 500


def find_item(model, organisation_id, description):
    """The organisation's item with this description, ignoring case, or None."""
    return model.query.filter(
        model.organisation_id == organisation_id,
        db.func.lower(model.description) == description.strip().lower(),
    ).first()


def item_usage(reference_type, item):
    """[(label, count)] of the records using the item, leaving out the ones with none."""
    usage = []
    for model, column, label in reference_type.used_by:
        count = model.query.filter(getattr(model, column) == item.id).count()
        if count:
            usage.append((label, count))
    return usage


def delete_item(reference_type, item):
    """Delete the item, clearing it from every record that uses it. Doesn't commit."""
    for model, column, _ in reference_type.used_by:
        values = {column: None}
        # a condition_set date means nothing without the condition
        if column.endswith('_condition_id'):
            values['condition_set'] = None
        model.query.filter(getattr(model, column) == item.id).update(values, synchronize_session=False)
    db.session.delete(item)


def split_values(text):
    """The values typed into Add in bulk: separated by commas or new lines, trimmed, blanks dropped."""
    return [value.strip() for value in re.split(r'[,\r\n]+', text or '') if value.strip()]


def add_items(reference_type, organisation_id, text):
    """Add each new value from Add in bulk. Values already in the list (or earlier in the text),
    ignoring case, are skipped. Returns (added, skipped, too_long) lists of descriptions. Doesn't commit."""
    model = reference_type.model
    existing = {item.description.lower() for item in model.query.filter(model.organisation_id == organisation_id)}
    added, skipped, too_long = [], [], []
    for value in split_values(text):
        if len(value) > MAX_LENGTH:
            too_long.append(value)
        elif value.lower() in existing:
            skipped.append(value)
        else:
            existing.add(value.lower())
            db.session.add(model(description=value, organisation_id=organisation_id))
            added.append(value)
    return added, skipped, too_long


def set_condition(record, column, condition_id):
    """Set an asset's or building's condition. condition_set becomes today's UK date when it changes
    (or None when it's cleared), and stays as it was when it doesn't."""
    if getattr(record, column) != condition_id:
        setattr(record, column, condition_id)
        record.condition_set = uk_today() if condition_id else None
