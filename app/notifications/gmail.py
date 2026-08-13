"""
Phase 8, part 2 — sending via Gmail.

*** UNVERIFIED ***
This has never been run against a real Gmail account. The build
environment has no network access, so no OAuth flow, no token exchange
and no send has ever been executed. The API usage below follows Google's
documented Gmail API v1 pattern, but treat it as untested until
`python main.py --test-email` succeeds on your own machine.

Setup is genuinely fiddly — see USER_GUIDE.md, section "Connecting Gmail".

Why Gmail API rather than SMTP: Google disabled password-based SMTP for
most accounts, and app passwords require 2FA plus storing a credential
that behaves like a password. OAuth stores a revocable token instead,
which is both safer and what Section 27 of the brief asked for.
"""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.notifications.templates import Email

# Send-only. Deliberately the narrowest scope Gmail offers — this token
# cannot read your mail, only send as you.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailNotConfigured(Exception):
    """Raised when Gmail credentials or token are missing."""


def build_mime_message(email: Email, sender: str, recipient: str) -> dict:
    """
    Builds the base64url-encoded payload the Gmail API expects.
    Pure function — testable without any Google dependency.
    """
    message = MIMEMultipart("alternative")
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = email.subject
    message.attach(MIMEText(email.body_text, "plain", "utf-8"))
    message.attach(MIMEText(email.body_html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw}


def _load_credentials(client_secrets_path: str, token_path: str):
    """
    Loads a saved OAuth token, refreshing it if expired. If no token
    exists, runs the one-time browser consent flow and saves one.

    Imports are inside the function so the rest of the system works
    without google-auth installed.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover
        raise GmailNotConfigured(
            "Google libraries are not installed — run: pip3 install -r requirements.txt"
        ) from exc

    token_file = Path(token_path)
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        secrets_file = Path(client_secrets_path)
        if not secrets_file.exists():
            raise GmailNotConfigured(
                f"No OAuth client secrets at {client_secrets_path}. "
                "See USER_GUIDE.md, 'Connecting Gmail'."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
        creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    return creds


def send_email(
    email: Email,
    sender: str,
    recipient: str,
    client_secrets_path: str,
    token_path: str,
) -> str:
    """
    Sends one email. Returns the Gmail message id.
    UNVERIFIED — see the module docstring.
    """
    if not sender or not recipient:
        raise GmailNotConfigured(
            "GMAIL_SENDER_ADDRESS and GMAIL_RECIPIENT_ADDRESS must both be set in .env"
        )

    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise GmailNotConfigured(
            "google-api-python-client is not installed — run: pip3 install -r requirements.txt"
        ) from exc

    creds = _load_credentials(client_secrets_path, token_path)
    service = build("gmail", "v1", credentials=creds)
    payload = build_mime_message(email, sender, recipient)
    sent = service.users().messages().send(userId="me", body=payload).execute()
    return sent.get("id", "")
