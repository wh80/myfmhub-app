from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import StringField, EmailField, BooleanField, DateField, DecimalField, IntegerField, SelectField, TextAreaField, DateTimeField, DateTimeLocalField
from wtforms.validators import DataRequired, InputRequired, Email, Length, Optional, NumberRange, ValidationError
from app.services.scheduling import FREQUENCIES, FREQUENCY_LABELS, FREQUENCY_MIN_DAYS, uk_today

class SiteForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    address_line_one = StringField('Address Line 1', validators=[Optional(), Length(max=255)])
    address_line_two = StringField('Address Line 2', validators=[Optional(), Length(max=255)])
    town_city = StringField('Town/City', validators=[Optional(), Length(max=255)])
    post_code = StringField('Post Code', validators=[Optional(), Length(max=255)])
    telephone = StringField('Telephone', validators=[Optional(), Length(max=100)])

class JobForm(FlaskForm):
    summary = StringField('Summary', validators=[DataRequired(), Length(max=120)])
    work_required = TextAreaField('Work Required', validators=[DataRequired(), Length(max=500)])
    due_by = DateField('Due By', validators=[Optional()])
    site_id = SelectField('Site', validators=[DataRequired()])

class JobCloseForm(FlaskForm):
    closed_at = DateField('Closed On', default=uk_today, validators=[DataRequired()])

    def validate_closed_at(self, field):
        if field.data and field.data > uk_today():
            raise ValidationError("The closed date can't be in the future.")

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

class JobScheduleForm(FlaskForm):
    summary = StringField('Summary', validators=[DataRequired(), Length(max=120)])
    work_required = TextAreaField('Work Required', validators=[DataRequired(), Length(max=500)])
    next_due_by = DateField('Next Due By', validators=[DataRequired()])
    frequency = SelectField('Frequency', choices=[('', '--- Select Frequency ---')] + FREQUENCIES, validators=[DataRequired()])
    notice_days = IntegerField('Notice Days', description='How many days ahead of due date the job is created', validators=[InputRequired(), NumberRange(min=0, max=365)])
    statutory_compliance = BooleanField('Statutory Compliance')
    site_id = SelectField('Site', validators=[DataRequired()])

    # a job is raised at most one gap ahead, so the schedule never has two jobs waiting to come due
    def validate_notice_days(self, field):
        min_days = FREQUENCY_MIN_DAYS.get(self.frequency.data)
        if field.data is not None and min_days is not None and field.data > min_days:
            raise ValidationError(f"Notice can't be longer than the time between jobs ({min_days} days for {FREQUENCY_LABELS[self.frequency.data].lower()}).")

class SupplierForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    address_line_one = StringField('Address Line 1', validators=[Optional(), Length(max=255)])
    address_line_two = StringField('Address Line 2', validators=[Optional(), Length(max=255)])
    town_city = StringField('Town/City', validators=[Optional(), Length(max=255)])
    post_code = StringField('Post Code', validators=[Optional(), Length(max=255)])
    telephone = StringField('Telephone', validators=[Optional(), Length(max=100)])

class RegisterForm(FlaskForm):
    organisation_name = StringField('Organisation Name', validators=[DataRequired(), Length(max=100)])
    first_name = StringField('First Name', validators=[DataRequired(), Length(max=100)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(max=100)])
    email = EmailField('Email', validators=[DataRequired(), Email(), Length(max=255)], filters=[lambda value: value.strip() if value else value])

class OTPForm(FlaskForm):
    code = StringField('Code', validators=[DataRequired(), Length(min=6, max=6)], filters=[lambda value: value.strip() if value else value])

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

class ImportForm(FlaskForm):
    file = FileField('CSV file', validators=[FileRequired('Choose a CSV file to upload.'), FileAllowed(['csv'], 'Upload a .csv file.')])
