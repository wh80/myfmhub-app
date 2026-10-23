from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from app import db
from app.forms import RegisterForm
from app.models import Organisation, User, Person
from app.services.auth import create_and_send_otp
from app.services.permissions import create_default_groups
organisations_bp = Blueprint('organisations', __name__, url_prefix="/organisations")

# served at the root /register url, see create_app
def register():
    form = RegisterForm()

    if form.validate_on_submit():
        email = form.email.data.lower()

        if User.query.filter_by(email=email).first():
            form.email.errors.append('An account with this email already exists.')
            return render_template('organisations/register.html', form=form)

        organisation = Organisation(
            name=form.organisation_name.data,
        )
        admin_group, team_group = create_default_groups(organisation)
        user = User(
            email=email,
            user_group=admin_group,
            can_login=True,
            organisation=organisation,
        )
        person = Person(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=email,
            organisation=organisation,
            user=user,
        )
        db.session.add_all([organisation, user, person])
        db.session.commit()

        try:
            otp = create_and_send_otp(user.email, person.first_name, welcome=True)
        except Exception:
            current_app.logger.exception(f'Failed to send OTP to {user.email}')
            flash("Your account was created, but we couldn't send your sign-in code. Please try signing in again in a few minutes.", 'warning')
            return redirect(url_for('auth.login'))

        flash(f"Welcome! We've sent a sign-in code to {user.email}.", 'success')
        return redirect(url_for('auth.validate', id=otp.id))

    return render_template('organisations/register.html', form=form)
