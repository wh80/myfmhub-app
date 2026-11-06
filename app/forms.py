from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed, FileSize
from wtforms import StringField, EmailField, BooleanField, DateField, DecimalField, IntegerField, SelectField, SelectMultipleField, RadioField, TextAreaField, DateTimeField, DateTimeLocalField
from wtforms.validators import DataRequired, InputRequired, Email, Length, Optional, NumberRange, ValidationError
from datetime import datetime
from app.services.scheduling import FREQUENCIES, FREQUENCY_LABELS, FREQUENCY_MIN_DAYS, UK_TIMEZONE, uk_today
from app.services.addresses import MAX_ADDRESS_LENGTH
from app.services.documents import FILE_TYPES, MAX_FILE_MB

def strip(value):
    return value.strip() if value else value

# optional selects: an id as a string, or '' for none (rather than 'None')
def optional_id(value):
    return str(value) if value else ''

class LocationForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)], filters=[strip])
    # optional: blank makes a top-level location. Choices are set by set_parent_choices in routes/locations.py,
    # and path_problem() refuses a duplicate path or a move inside itself
    parent_id = SelectField('Parent Location', coerce=optional_id, validators=[Optional()])
    address = TextAreaField('Address', validators=[Optional(), Length(max=MAX_ADDRESS_LENGTH)])
    telephone = StringField('Telephone', validators=[Optional(), Length(max=100)], filters=[strip])
    email = EmailField('Email', validators=[Optional(), Email(), Length(max=255)], filters=[strip])
    # an optional category: choices are set by set_reference_choices
    location_category_id = SelectField('Location Category', coerce=optional_id, validators=[Optional()])
    # anyone in the organisation: choices are set by set_main_contact_choices in routes/locations.py
    main_contact_id = SelectField('Main Contact', coerce=optional_id, validators=[Optional()])

class JobForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=100)])
    work_required = TextAreaField('Work Required', validators=[DataRequired(), Length(max=500)])
    due_by = DateField('Due By', validators=[Optional()])
    # choices are the organisation's locations, by path (set_location_choices)
    location_id = SelectField('Location', coerce=optional_id, validators=[DataRequired(message='Choose a location.')])
    # an optional category or condition: choices are set by set_reference_choices
    job_category_id = SelectField('Job Category', coerce=optional_id, validators=[Optional()])
    estimated_cost = DecimalField('Estimated Cost', places=2, validators=[Optional(), NumberRange(min=0, max=9999999999.99)])
    actual_cost = DecimalField('Actual Cost', places=2, validators=[Optional(), NumberRange(min=0, max=9999999999.99)])

class JobRejectForm(FlaskForm):
    rejection_reason = TextAreaField('Rejection Reason', validators=[DataRequired(), Length(max=2000)], filters=[strip])
    # only for a planned job whose schedule is waiting on it: rejecting moves the schedule on, like closing
    next_due_by = DateField('Next Due By', validators=[Optional()])

    # latest is today plus the schedule's frequency for a planned job, else None (no next due date asked)
    def __init__(self, latest=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latest = latest

    def validate_next_due_by(self, field):
        if self.latest is None:
            return
        if not field.data:
            raise ValidationError('Choose when the next job is due.')
        validate_next_due_by_range(field.data, self.latest)

# Assign and Reassign: one supplier or one person, from a single select grouped into Suppliers and People.
# Values are 'supplier:<id>' or 'person:<id>'; the choices are set in the route (job_assignee_choices),
# so WTForms refuses anyone else
class JobReassignForm(FlaskForm):
    assignee = SelectField('Assign To', validators=[DataRequired(message='Choose a supplier or a person.')])
    require_acceptance = BooleanField('Require Acceptance')
    require_start_confirmation = BooleanField('Require Start Confirmation')
    require_scheduling = BooleanField('Require Scheduling')

# Assign also takes any number of helpers (people). The lead can't be one of them
class JobAssignForm(JobReassignForm):
    helper_ids = SelectMultipleField('Helpers', validators=[Optional()])

    def validate_helper_ids(self, field):
        if self.assignee.data and self.assignee.data.removeprefix('person:') in (field.data or []):
            raise ValidationError("The person the job is assigned to can't also be a helper.")

# a comment on accepting, declining or starting a job, which goes in its notes. Declining needs a reason. response is
# the name of the button pressed
class JobResponseForm(FlaskForm):
    response = StringField('Response')
    comments = TextAreaField('Comments', validators=[Length(max=2000)], filters=[strip])

    def validate_comments(self, field):
        if self.response.data == 'decline' and not field.data:
            raise ValidationError('Please say why you are declining this job.')

# a note added on a job's Notes tab, or from the link in an assignee's email
class NoteForm(FlaskForm):
    description = TextAreaField('Add a Note', validators=[DataRequired(message='Enter a note.'), Length(max=2000)], filters=[strip])

# Add Helpers: people who aren't on the job already (set in the route)
class JobHelpersForm(FlaskForm):
    helper_ids = SelectMultipleField('Helpers', validators=[DataRequired(message='Choose at least one person.')])

def uk_now_minute():
    return datetime.now(UK_TIMEZONE).replace(tzinfo=None, second=0, microsecond=0)

# the date the assignee will do the work. due_by is the job's. A due date that's already passed can't be met, so it
# doesn't apply: otherwise an overdue job could never be scheduled
class JobScheduleForm(FlaskForm):
    scheduled_for = DateField('Date', validators=[DataRequired(message='Choose the date you will do the work.')])
    comments = TextAreaField('Comments', validators=[Length(max=2000)], filters=[strip])

    def __init__(self, due_by, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.due_by = due_by

    def validate_scheduled_for(self, field):
        if not field.data:
            return
        today = uk_today()
        if field.data < today:
            raise ValidationError("The date can't be in the past.")
        if self.due_by and self.due_by >= today and field.data > self.due_by:
            raise ValidationError(f"The date can't be after the job's due date ({self.due_by:%d-%m-%Y}).")

# when the work was done, in UK time (stored with the time zone), and notes on it
class JobCompleteForm(FlaskForm):
    completed_at = DateTimeLocalField('Completed At', format='%Y-%m-%dT%H:%M', validators=[DataRequired()], default=uk_now_minute)
    completion_comments = TextAreaField('Completion Comments', validators=[DataRequired(), Length(max=5000)], filters=[strip])

    # started_at is the job's (timezone-aware): the work can't be completed before it started
    def __init__(self, started_at, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_at = started_at

    def completed_at_uk(self):
        return self.completed_at.data.replace(tzinfo=UK_TIMEZONE)

    def validate_completed_at(self, field):
        if not field.data:
            return
        if self.completed_at_uk() > datetime.now(UK_TIMEZONE):
            raise ValidationError("The completed time can't be in the future.")
        # the field has no seconds, so compare to the minute the work started
        if self.started_at and self.completed_at_uk() < self.started_at.replace(second=0, microsecond=0):
            started = self.started_at.astimezone(UK_TIMEZONE)
            raise ValidationError(f"The completed time can't be before the work was started ({started:%d-%m-%Y %H:%M}).")

# a planned job's next due date can be brought forward from latest, but not moved later or into the past
def validate_next_due_by_range(next_due_by, latest):
    if next_due_by < uk_today():
        raise ValidationError("The next due date can't be in the past.")
    if next_due_by > latest:
        raise ValidationError("You can't make the period to the next jobs due date longer than the frequency period of the schedule")

# sets a planned job schedule's next due date when its job is closed. latest is the date the work was completed plus
# the schedule's frequency (or today, if that's already passed)
class PlannedJobScheduleNextDueForm(FlaskForm):
    next_due_by = DateField('Next Due By', validators=[DataRequired()])

    def __init__(self, latest, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latest = latest

    def validate_next_due_by(self, field):
        if field.data:
            validate_next_due_by_range(field.data, self.latest)

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
    
    # choices are the organisation's locations, by path (set_location_choices)
    location_id = SelectField('Location', coerce=optional_id, validators=[DataRequired(message='Choose a location.')])
    # an optional category or condition: choices are set by set_reference_choices
    asset_category_id = SelectField('Asset Category', coerce=optional_id, validators=[Optional()])
    # changing it sets condition_set to today (set_condition)
    asset_condition_id = SelectField('Asset Condition', coerce=optional_id, validators=[Optional()])

class PlannedJobScheduleForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=100)])
    work_required = TextAreaField('Work Required', validators=[DataRequired(), Length(max=500)])
    next_due_by = DateField('Next Due By', validators=[DataRequired()])
    frequency = SelectField('Frequency', choices=[('', '--- Select Frequency ---')] + FREQUENCIES, validators=[DataRequired()])
    notice_days = IntegerField('Notice Days', description='How many days ahead of due date the job is created', validators=[InputRequired(), NumberRange(min=0, max=365)])
    statutory_compliance = BooleanField('Statutory Compliance')
    is_active = BooleanField('Active', default=True, description="Inactive schedules don't raise any jobs")
    # choices are the organisation's locations, by path (set_location_choices)
    location_id = SelectField('Location', coerce=optional_id, validators=[DataRequired(message='Choose a location.')])
    # an optional category or condition: choices are set by set_reference_choices
    job_category_id = SelectField('Job Category', coerce=optional_id, validators=[Optional()])
    estimated_cost = DecimalField('Estimated Cost', places=2, validators=[Optional(), NumberRange(min=0, max=9999999999.99)])
    # days, in steps as small as a quarter of a day
    estimated_duration = DecimalField('Estimated Duration', places=2, description='In days, e.g. 0.5 for half a day', validators=[Optional(), NumberRange(min=0, max=9999)])

    # a job is raised at most one gap ahead, so the schedule never has two jobs waiting to come due
    def validate_notice_days(self, field):
        min_days = FREQUENCY_MIN_DAYS.get(self.frequency.data)
        if field.data is not None and min_days is not None and field.data > min_days:
            raise ValidationError(f"Notice can't be longer than the time between jobs ({min_days} days for {FREQUENCY_LABELS[self.frequency.data].lower()}).")

class SupplierForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    address = TextAreaField('Address', validators=[Optional(), Length(max=MAX_ADDRESS_LENGTH)])
    telephone = StringField('Telephone', validators=[Optional(), Length(max=100)])
    email = EmailField('Email', validators=[Optional(), Email(), Length(max=255)], filters=[strip])
    supplier_category_id = SelectField('Supplier Category', coerce=optional_id, validators=[Optional()])

# My Organisation's Edit page. Names don't have to be unique: other organisations' names are none of its business
class OrganisationForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)], filters=[strip])
    address = TextAreaField('Address', validators=[Optional(), Length(max=MAX_ADDRESS_LENGTH)])
    telephone = StringField('Telephone', validators=[Optional(), Length(max=100)], filters=[strip])

class RegisterForm(FlaskForm):
    organisation_name = StringField('Organisation Name', validators=[DataRequired(), Length(max=100)], filters=[strip])
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

class DocumentUploadForm(FlaskForm):
    file = FileField('File', validators=[
        FileRequired('Choose a file to upload.'),
        FileAllowed(sorted(FILE_TYPES), 'Upload a PDF, Word, Excel, text, CSV or image file.'),
        FileSize(max_size=MAX_FILE_MB * 1024 * 1024, message=f'Files can be up to {MAX_FILE_MB} MB.'),
    ])
    description = StringField('Description', validators=[DataRequired(), Length(max=255)], filters=[strip])
    expires = DateField('Expires', validators=[Optional()])
    document_category_id = SelectField('Document Category', coerce=optional_id, validators=[Optional()])

# a document's details. The stored file itself can't be replaced
class DocumentEditForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=255)], filters=[strip])
    expires = DateField('Expires', validators=[Optional()])
    document_category_id = SelectField('Document Category', coerce=optional_id, validators=[Optional()])

# a category or condition on its reference data page
class ReferenceItemForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=100)], filters=[strip])

# Add in bulk: values separated by commas or new lines (split_values in services/reference_data.py)
class ReferenceBulkForm(FlaskForm):
    values = TextAreaField('Values', validators=[DataRequired(), Length(max=50000)])

# raised from the Support page
class SupportTicketForm(FlaskForm):
    description = TextAreaField('Description', description='Describe the issue or question, with any details that would help us look into it',
                                validators=[DataRequired(), Length(max=5000)], filters=[strip])

# a platform admin's note on a support ticket
class SupportTicketNoteForm(FlaskForm):
    description = TextAreaField('Note', validators=[DataRequired(), Length(max=5000)], filters=[strip])

class ImportForm(FlaskForm):
    file = FileField('Excel file', validators=[FileRequired('Choose a file to upload.'), FileAllowed(['xlsx'], 'Upload an Excel Workbook (.xlsx). If you have a CSV or an older .xls file, open it in Excel and save it as an Excel Workbook.')])
