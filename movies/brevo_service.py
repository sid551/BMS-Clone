"""
Brevo (Sendinblue) transactional email via HTTP API.
Uses requests — no SMTP, works on Vercel free tier.
"""
import base64
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BREVO_API_URL = 'https://api.brevo.com/v3/smtp/email'


def send_email(to_email, to_name, subject, html_body, text_body, attachments=None):
    """
    Send a transactional email via Brevo HTTP API.

    attachments: list of dicts with keys:
        - name: filename (e.g. 'ticket.pdf')
        - content: bytes
        - type: MIME type (e.g. 'application/pdf')

    Returns (True, None) on success, (False, error_message) on failure.
    """
    api_key = getattr(settings, 'BREVO_API_KEY', '')
    if not api_key:
        err_msg = 'BREVO_API_KEY environment variable is not configured.'
        logger.warning(err_msg)
        return False, err_msg

    sender_email = getattr(settings, 'BREVO_SENDER_EMAIL', '') or getattr(settings, 'DEFAULT_FROM_EMAIL', 'tickets@bookmyshow.com')
    sender_name = getattr(settings, 'BREVO_SENDER_NAME', '') or 'BookMyShow'

    if '<' in sender_email:
        parts = sender_email.split('<')
        sender_name = parts[0].strip().strip('"')
        sender_email = parts[1].strip().rstrip('>')

    payload = {
        'sender': {'name': sender_name, 'email': sender_email},
        'to': [{'email': to_email, 'name': to_name}],
        'subject': subject,
        'htmlContent': html_body,
        'textContent': text_body,
    }

    if attachments:
        payload['attachment'] = []
        for att in attachments:
            content_data = att.get('content')
            if hasattr(content_data, 'file') and hasattr(content_data.file, 'getvalue'):
                content_data = content_data.file.getvalue()
            elif hasattr(content_data, 'read'):
                content_data = content_data.read()
            elif hasattr(content_data, 'getvalue'):
                content_data = content_data.getvalue()

            if isinstance(content_data, str):
                content_data = content_data.encode('utf-8')

            if content_data and len(content_data) > 50:
                encoded = base64.b64encode(content_data).decode('utf-8')
                payload['attachment'].append({
                    'name': att['name'],
                    'content': encoded,
                })
            else:
                logger.warning(f"Skipping empty or corrupted email attachment: {att.get('name')}")

    try:
        resp = requests.post(
            BREVO_API_URL,
            headers={
                'api-key': api_key,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            json=payload,
            timeout=8,
        )
        if resp.status_code in (200, 201):
            logger.info(f'Brevo email sent to {to_email} — subject: {subject}')
            return True, None
        else:
            err_msg = f'Brevo API HTTP {resp.status_code}: {resp.text}'
            logger.error(err_msg)
            return False, err_msg
    except requests.exceptions.RequestException as e:
        err_msg = f'Brevo HTTP request failed: {e}'
        logger.error(err_msg)
        return False, err_msg
