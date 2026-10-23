import resend
from flask import current_app


def send_email(to, subject, html):
    api_key = current_app.config['RESEND_API_KEY']

    # no key in dev, so log the email instead of sending it
    if not api_key:
        current_app.logger.warning(f'RESEND_API_KEY not set, email not sent.\nTo: {to}\nSubject: {subject}\n{html}')
        return

    resend.api_key = api_key
    resend.Emails.send({
        'from': current_app.config['EMAIL_FROM'],
        'to': [to],
        'subject': subject,
        'html': html,
    })
