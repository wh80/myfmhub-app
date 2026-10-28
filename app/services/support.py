from flask import current_app, render_template, url_for
from app.services.email import send_email


def send_ticket_notification(ticket):
    """Email SUPPORT_EMAIL (the MyFmHub team's inbox) about a new support ticket, with a link to it in /admin. Call it inside
    the request, after the ticket is committed, so the link uses the site's own address."""
    person = ticket.created_by
    html = render_template(
        'emails/support/new_ticket.html',
        ticket=ticket,
        person_name=f'{person.first_name} {person.last_name}',
        person_email=person.email,
        organisation_name=ticket.organisation.name,
        ticket_url=url_for('admin.support_ticket', id=ticket.id, _external=True),
    )
    send_email(current_app.config['SUPPORT_EMAIL'], f'New support ticket from {person.first_name} {person.last_name} ({ticket.organisation.name})', html)
