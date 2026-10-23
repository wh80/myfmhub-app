import click
from flask.cli import with_appcontext
from app import db
from app.models import User
from app.services.jobs import create_scheduled_jobs


@click.command('make-platform-admin')
@click.argument('email')
@click.option('--remove', is_flag=True, help='Remove platform admin instead of granting it.')
@with_appcontext
def make_platform_admin(email, remove):
    """Give a user access to /admin (or take it away with --remove)."""
    user = User.query.filter_by(email=email.strip().lower()).first()
    if not user:
        raise click.ClickException(f'No user with email {email}.')

    user.is_platform_admin = not remove
    db.session.commit()
    click.echo(f"{user.email} is {'no longer' if remove else 'now'} a platform admin.")


@click.command('create-jobs-for-job-schedules')
@with_appcontext
def create_jobs_for_job_schedules():
    """Raise a job for every job schedule whose next job creation date (UK time) is today or earlier."""
    created, failed = create_scheduled_jobs()
    for description in created:
        click.echo(f'Created job {description}')
    click.echo(f'Created {len(created)} job(s).')

    # a non-zero exit marks the cron run as failed
    if failed:
        raise click.ClickException(f"{len(failed)} job schedule(s) failed: {', '.join(failed)}")
