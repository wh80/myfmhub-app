from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed, FileSize
from wtforms import StringField, EmailField, BooleanField, DateField, DecimalField, IntegerField, SelectField, TextAreaField, DateTimeField, DateTimeLocalField
from wtforms.validators import DataRequired, InputRequired, Email, Length, Optional, NumberRange, ValidationError
from app.services.scheduling import FREQUENCIES, FREQUENCY_LABELS, FREQUENCY_MIN_DAYS, uk_today
from app.services.files import FILE_TYPES, MAX_FILE_MB

def strip(value):
    return value.strip() if value else value

# the building and zone selects: an id as a string, or '' for none (rather than 'None')
def optional_id(value):
    return str(value) if value else ''

class SiteForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)], filters=[strip])
    address_line_one = StringField('Address Line 1', validators=[Optional(), Length(max=255)])
    address_line_two = StringField('Address Line 2', validators=[Optional(), Length(max=255)])
    address_line_three = StringField('Address Line 3', validators=[Optional(), Length(max=100)])
    town_city = StringField('Town/City', validators=[Optional(), Length(max=255)])
    county = StringField('County', validators=[Optional(), Length(max=100)])
    post_code = StringField('Post Code', validators=[Optional(), Length(max=255)])
    telephone = StringField('Telephone', validators=[Optional(), Length(max=100)])

class JobForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=120)])
    work_required = TextAreaField('Work Required', validators=[DataRequired(), Length(max=500)])
    due = DateField('Due', validators=[Optional()])
    site_id = SelectField('Site', validators=[DataRequired()])
    # optional, and dependent: choices are set by set_location_choices, and check_location makes
    # sure the building is in the site and the zone in the building
    building_id = SelectField('Building', coerce=optional_id, validators=[Optional()])
    zone_id = SelectField('Zone', coerce=optional_id, validators=[Optional()])
    # an optional category or condition: choices are set by set_reference_choices
    job_category_id = SelectField('Job Category', coerce=optional_id, validators=[Optional()])

class JobCloseForm(FlaskForm):
    closed_at = DateField('Closed On', default=uk_today, validators=[DataRequired()])

    # raised_on is job.raised_on: a job can't be closed before it was raised
    def __init__(self, raised_on, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raised_on = raised_on

    def validate_closed_at(self, field):
        if field.data and field.data > uk_today():
            raise ValidationError("The closed date can't be in the future.")
        if field.data and field.data < self.raised_on:
            raise ValidationError(f"The closed date can't be before the job was raised ({self.raised_on:%d-%m-%Y}).")

# sets a job schedule's next due date after its job is closed. latest is the close date plus the
# schedule's frequency (or today, if that's already passed)
class JobScheduleNextDueForm(FlaskForm):
    next_due = DateField('Next Due', validators=[DataRequired()])

    def __init__(self, latest, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latest = latest

    def validate_next_due(self, field):
        if field.data and field.data < uk_today():
            raise ValidationError("The next due date can't be in the past.")
        if field.data and field.data > self.latest:
            raise ValidationError("You can't make the period to the next jobs due date longer than the frequency period of the schedule")

class AssetForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=50)])
    asset_number = StringField('Asset Number', validators=[Optional(), Length(max=50)], filters=[lambda value: value.strip() or None if value else None])
    make = StringField('Make', validators=[Optional(), Length(max=50)], filters=[lambda value: value.strip() or None if value else None])
    model = StringField('Model', validators=[Optional(), Length(max=50)], filters=[lambda value: value.strip() or None if value else None])
    serial_number = StringField('Serial Number', validators=[Optional(), Length(max=50)], filters=[lambda value: value.strip() or None if value else None])
    warranty_expiry = DateField('Warranty Expiry', validators=[Optional()])
    purchase_date = DateField('Purchase Date', validators=[Optional()])
    purchased_from = StringField('Purchased From', validators=[Optional(), Length(max=100)], filters=[lambda value: value.strip() or None if value else None])
    estimated_replacement_date = DateField('Estimated Replacement Date', validators=[Optional()])
    estimated_replacement_cost = DecimalField('Estimated Replacement Cost', places=2, validators=[Optional(), NumberRange(min=0, max=9999999999.99)])
    
    site_id = SelectField('Site', validators=[DataRequired()])
    # optional, and dependent: choices are set by set_location_choices, and check_location makes
    # sure the building is in the site and the zone in the building
    building_id = SelectField('Building', coerce=optional_id, validators=[Optional()])
    zone_id = SelectField('Zone', coerce=optional_id, validators=[Optional()])
    # an optional category or condition: choices are set by set_reference_choices
    asset_category_id = SelectField('Asset Category', coerce=optional_id, validators=[Optional()])
    # changing it sets condition_set to today (set_condition)
    asset_condition_id = SelectField('Asset Condition', coerce=optional_id, validators=[Optional()])

class JobScheduleForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=120)])
    work_required = TextAreaField('Work Required', validators=[DataRequired(), Length(max=500)])
    next_due = DateField('Next Due', validators=[DataRequired()])
    frequency = SelectField('Frequency', choices=[('', '--- Select Frequency ---')] + FREQUENCIES, validators=[DataRequired()])
    notice_days = IntegerField('Notice Days', description='How many days ahead of due date the job is created', validators=[InputRequired(), NumberRange(min=0, max=365)])
    statutory_compliance = BooleanField('Statutory Compliance')
    is_active = BooleanField('Active', default=True, description="Inactive schedules don't raise any jobs")
    site_id = SelectField('Site', validators=[DataRequired()])
    # optional, and dependent: choices are set by set_location_choices, and check_location makes
    # sure the building is in the site and the zone in the building
    building_id = SelectField('Building', coerce=optional_id, validators=[Optional()])
    zone_id = SelectField('Zone', coerce=optional_id, validators=[Optional()])
    # an optional category or condition: choices are set by set_reference_choices
    job_category_id = SelectField('Job Category', coerce=optional_id, validators=[Optional()])

    # a job is raised at most one gap ahead, so the schedule never has two jobs waiting to come due
    def validate_notice_days(self, field):
        min_days = FREQUENCY_MIN_DAYS.get(self.frequency.data)
        if field.data is not None and min_days is not None and field.data > min_days:
            raise ValidationError(f"Notice can't be longer than the time between jobs ({min_days} days for {FREQUENCY_LABELS[self.frequency.data].lower()}).")

# the site (or building, for a zone) comes from the page it's created from, and never changes
class BuildingForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)], filters=[strip])
    # changing it sets condition_set to today (set_condition)
    building_condition_id = SelectField('Building Condition', coerce=optional_id, validators=[Optional()])

class ZoneForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)], filters=[strip])

class SupplierForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    address_line_one = StringField('Address Line 1', validators=[Optional(), Length(max=255)])
    address_line_two = StringField('Address Line 2', validators=[Optional(), Length(max=255)])
    address_line_three = StringField('Address Line 3', validators=[Optional(), Length(max=100)])
    town_city = StringField('Town/City', validators=[Optional(), Length(max=255)])
    county = StringField('County', validators=[Optional(), Length(max=100)])
    post_code = StringField('Post Code', validators=[Optional(), Length(max=255)])
    telephone = StringField('Telephone', validators=[Optional(), Length(max=100)])
    supplier_category_id = SelectField('Supplier Category', coerce=optional_id, validators=[Optional()])

class RegisterForm(FlaskForm):
    organisation_name = StringField('Organisation Name', validators=[DataRequired(), Length(max=100)])
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=100)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=100)])
    email = EmailField('Email', validators=[DataRequired(), Email(), Length(max=255)], filters=[lambda value: value.strip() if value else value])
    accept_terms = BooleanField('I accept the Terms and Conditions', validators=[DataRequired('You must accept the Terms and Conditions to register.')])

class OTPForm(FlaskForm):
    code = StringField('Code', validators=[DataRequired(), Length(min=6, max=6)], filters=[lambda value: value.strip() if value else value])

class AssetAssignForm(FlaskForm):
    # choices are the people in the organisation, plus '' for unassigned (set in the route)
    assigned_to_id = SelectField('Assigned To', coerce=optional_id, validators=[Optional()])

class SwitchOrganisationForm(FlaskForm):
    # the user's person in each organisation they can sign in to, labelled with the organisation's name
    person_id = SelectField('Organisation', validators=[DataRequired()])

class LoginForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email(), Length(max=255)], filters=[lambda value: value.strip() if value else value])

class PersonForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=100)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=100)])
    email = EmailField('Email', validators=[DataRequired(), Email(), Length(max=255)], filters=[lambda value: value.strip() if value else value])
    job_title = StringField('Job Title', validators=[Optional(), Length(max=100)])
    mobile = StringField('Mobile', validators=[Optional(), Length(max=100)])
    user_group_id = SelectField('User Group')
    can_login = BooleanField('Can log in')

    # a group is only needed by people who can log in
    def validate_user_group_id(self, field):
        if self.can_login.data and not field.data:
            raise ValidationError('Choose a user group for someone who can log in.')

class UserGroupForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])

class FileUploadForm(FlaskForm):
    file = FileField('File', validators=[
        FileRequired('Choose a file to upload.'),
        FileAllowed(sorted(FILE_TYPES), 'Upload a PDF, Word, Excel, text, CSV or image file.'),
        FileSize(max_size=MAX_FILE_MB * 1024 * 1024, message=f'Files can be up to {MAX_FILE_MB} MB.'),
    ])
    description = StringField('Description', validators=[DataRequired(), Length(max=255)], filters=[strip])
    expires = DateField('Expires', validators=[Optional()])
    file_category_id = SelectField('File Category', coerce=optional_id, validators=[Optional()])

# a file's details. The stored file itself can't be replaced
class FileEditForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=255)], filters=[strip])
    expires = DateField('Expires', validators=[Optional()])
    file_category_id = SelectField('File Category', coerce=optional_id, validators=[Optional()])

# a category or condition on its reference data page
class ReferenceItemForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=100)], filters=[strip])

# Add in bulk: values separated by commas or new lines (split_values in services/reference_data.py)
class ReferenceBulkForm(FlaskForm):
    values = TextAreaField('Values', validators=[DataRequired(), Length(max=50000)])

class ImportForm(FlaskForm):
    file = FileField('CSV file', validators=[FileRequired('Choose a CSV file to upload.'), FileAllowed(['csv'], 'Upload a .csv file.')])
