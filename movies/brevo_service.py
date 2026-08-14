"""
HTTP REST API Email Transports — Port 443 HTTPS (Vercel Serverless Compatible)
Priority: Brevo REST API -> MailerSend REST API -> Resend REST API
"""
import base64
import logging
import os
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BREVO_API_URL = 'https://api.brevo.com/v3/smtp/email'
MAILERSEND_API_URL = 'https://api.mailersend.com/v1/email'
RESEND_API_URL = 'https://api.resend.com/emails'


def send_email_via_brevo(to_email, to_name, subject, html_body, text_body, attachments=None, tag=None):
    """
    Primary Transport: Brevo REST API (HTTPS Port 443 — Vercel Serverless Compatible).
    Endpoint: POST https://api.brevo.com/v3/smtp/email
    """
    api_key = getattr(settings, 'BREVO_API_KEY', '') or os.environ.get('BREVO_API_KEY', '')
    if not api_key:
        return False, 'BREVO_API_KEY environment variable is not configured.'

    sender_email = getattr(settings, 'BREVO_SENDER_EMAIL', '') or getattr(settings, 'DEFAULT_FROM_EMAIL', '') or os.environ.get('BREVO_SENDER_EMAIL', '') or 'tickets@bookmyshow.com'
    sender_name = getattr(settings, 'BREVO_SENDER_NAME', '') or os.environ.get('BREVO_SENDER_NAME', '') or 'BookMyShow'

    if '<' in sender_email:
        parts = sender_email.split('<')
        sender_name = parts[0].strip().strip('"')
        sender_email = parts[1].strip().rstrip('>')

    payload = {
        'sender': {'name': sender_name, 'email': sender_email},
        'to': [{'email': to_email, 'name': to_name or to_email.split('@')[0]}],
        'subject': subject,
        'htmlContent': html_body,
        'textContent': text_body,
    }

    if tag:
        payload['tags'] = [str(tag)]

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

            if isinstance(content_data, memoryview):
                content_data = bytes(content_data)
            elif isinstance(content_data, str):
                content_data = content_data.encode('utf-8')
            elif content_data and not isinstance(content_data, (bytes, bytearray)):
                try:
                    content_data = bytes(content_data)
                except Exception:
                    pass

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
            timeout=3.5,
        )
        if resp.status_code in (200, 201):
            msg_id = ""
            try:
                res_json = resp.json()
                msg_id = res_json.get('messageId', '')
            except Exception:
                pass
            info_str = f"Brevo HTTP {resp.status_code} (Sender: {sender_email}, Message ID: {msg_id or 'OK'})"
            logger.info(f'[BREVO SUCCESS] Email sent to {to_email} — {info_str}')
            return True, info_str
        else:
            err_text = resp.text
            if 'sending platform' in err_text.lower() or 'disabled' in err_text.lower():
                err_msg = 'Brevo sending platform is disabled. Activate transactional emails at app.brevo.com -> Transactional -> Settings.'
            else:
                err_msg = f'Brevo API HTTP {resp.status_code}: {err_text}'
            logger.error(f'[BREVO ERROR] {err_msg}')
            return False, err_msg
    except Exception as e:
        err_msg = f'Brevo HTTP request failed: {e}'
        logger.error(f'[BREVO ERROR] {err_msg}')
        return False, err_msg


def send_email_via_mailersend(to_email, to_name, subject, html_body, text_body, attachments=None):
    """
    Fallback Transport: MailerSend REST API (HTTPS Port 443 — Vercel Serverless Compatible).
    Endpoint: POST https://api.mailersend.com/v1/email
    """
    api_key = os.environ.get('MAILERSEND_API_KEY', '')
    if not api_key:
        return False, 'MAILERSEND_API_KEY not configured'

    sender_email = os.environ.get('MAILERSEND_SENDER_EMAIL', 'MS_trial@mailersend.net')
    sender_name = os.environ.get('MAILERSEND_SENDER_NAME', 'BookMyShow')

    payload = {
        'from': {'email': sender_email, 'name': sender_name},
        'to': [{'email': to_email, 'name': to_name or to_email.split('@')[0]}],
        'subject': subject,
        'html': html_body,
        'text': text_body or subject,
    }

    if attachments:
        payload['attachments'] = []
        for att in attachments:
            content_data = att.get('content')
            if isinstance(content_data, str):
                content_data = content_data.encode('utf-8')
            elif isinstance(content_data, memoryview):
                content_data = bytes(content_data)
            if content_data:
                payload['attachments'].append({
                    'filename': att['name'],
                    'content': base64.b64encode(content_data).decode('utf-8'),
                    'disposition': 'attachment',
                })

    try:
        resp = requests.post(
            MAILERSEND_API_URL,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            },
            json=payload,
            timeout=3.5,
        )
        if resp.status_code in (200, 201, 202):
            logger.info(f'[MAILERSEND SUCCESS] Email sent to {to_email}')
            return True, f'MailerSend HTTP {resp.status_code} OK'
        else:
            err_msg = f'MailerSend HTTP {resp.status_code}: {resp.text}'
            logger.error(f'[MAILERSEND ERROR] {err_msg}')
            return False, err_msg
    except Exception as e:
        err_msg = f'MailerSend HTTP request failed: {e}'
        logger.error(f'[MAILERSEND ERROR] {err_msg}')
        return False, err_msg


def send_email_via_resend(to_email, to_name, subject, html_body, text_body, attachments=None):
    """
    Fallback Transport: Resend REST API (HTTPS Port 443 — Vercel Serverless Compatible).
    Endpoint: POST https://api.resend.com/emails
    Note: Free testing mode on Resend restricts recipient email to account owner's email address.
    """
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        return False, 'RESEND_API_KEY not configured'

    sender_name = getattr(settings, 'BREVO_SENDER_NAME', '') or 'BookMyShow'
    sender_email = os.environ.get('RESEND_SENDER_EMAIL', 'onboarding@resend.dev')

    payload = {
        'from': f'{sender_name} <{sender_email}>',
        'to': [to_email],
        'subject': subject,
        'html': html_body,
    }

    if attachments:
        payload['attachments'] = []
        for att in attachments:
            content_data = att.get('content')
            if isinstance(content_data, str):
                content_data = content_data.encode('utf-8')
            elif isinstance(content_data, memoryview):
                content_data = bytes(content_data)
            if content_data:
                payload['attachments'].append({
                    'filename': att['name'],
                    'content': base64.b64encode(content_data).decode('utf-8'),
                })

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=3.5,
        )
        if resp.status_code in (200, 201):
            logger.info(f'[RESEND SUCCESS] Email sent to {to_email}')
            return True, f'Resend HTTP {resp.status_code} OK'
        else:
            err_text = resp.text
            if 'validation_error' in err_text or 'testing mode' in err_text:
                err_msg = 'Resend free testing mode restricts recipient to your account owner email. Verify a domain at https://resend.com/domains or configure BREVO_API_KEY for unrestricted delivery to any recipient.'
            else:
                err_msg = f'Resend HTTP {resp.status_code}: {err_text}'
            logger.warning(f'[RESEND RESTRICTION] {err_msg}')
            return False, err_msg
    except Exception as e:
        err_msg = f'Resend HTTP request failed: {e}'
        logger.error(f'[RESEND ERROR] {err_msg}')
        return False, err_msg


# Primary alias for backwards compatibility
send_email = send_email_via_brevo
