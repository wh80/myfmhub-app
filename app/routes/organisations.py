from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from app import db
from app.forms import RegisterForm
from app.models import Organisation, User, Person
from app.services.auth import check_otp_rate_limit, create_otp, send_otp_email
from app.services.permissions import create_default_groups
from app.services.people import find_or_create_user
organisations_bp = Blueprint('organisations', __name__, url_prefix="/organisations")

# served at the root /register url, see create_app.
# An email that already has a login (in another organisation) registers a new organisation with the same
# login. Anyone can type any email here, so the organisation stays unverified, and isn't offered at
# sign-in, until the code sent now is entered (see Person.can_sign_in and choose_person)
def register():
    form = RegisterForm()

    if form.validate_on_submit():
        email = form.email.data.lower()

        existing = User.query.filter_by(email=email).first()
        if existing:
            error = check_otp_rate_limit(existing)
            if error:
                form.email.errors.append(error)
                return render_template('organisations/register.html', form=form)

        organisation = Organisation(
            name=form.organisation_name.data,
        )
        admin_group, team_group = create_default_groups(organisation)
        person = Person(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=email,
            can_login=True,
            user_group=admin_group,
            organisation=organisation,
            user=find_or_create_user(email),
        )
        # the form refuses to register without accept_terms ticked
        organisation.terms_accepted_at = datetime.now(timezone.utc)
        organisation.terms_accepted_by = person
        db.session.add_all([organisation, person])
        db.session.commit()

        otp, code = create_otp(person.user, person)
        try:
            send_otp_email(otp, code, welcome=True)
        except Exception:
            current_app.logger.exception(f'Failed to send OTP to {email}')
            # the code page's "Send a new code" keeps this organisation, so it can still be verified
            flash("Your account was created, but we couldn't send your sign-in code. Use 'Send a new code' below in a few minutes.", 'warning')
            return redirect(url_for('auth.validate', id=otp.id))

        flash(f"Welcome! We've sent a sign-in code to {email}.", 'success')
        return redirect(url_for('auth.validate', id=otp.id))

    return render_template('organisations/register.html', form=form)

# served at the root /terms url, see create_app. Public, since it's read before registering
def terms():
    return render_template('organisations/terms.html')
