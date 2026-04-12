"""
Shared Gmail API helpers.

Supports OAuth desktop credentials with token persistence for Gmail send.
"""

import base64
import os
from email.message import EmailMessage
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _credentials_file() -> Path:
    return Path(os.environ.get("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json"))


def _token_file() -> Path:
    return Path(os.environ.get("GMAIL_TOKEN_FILE", "gmail_token.json"))


def _load_credentials() -> object | None:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ModuleNotFoundError:
        print(
            "[GMAIL] Google API packages are missing in the current Python environment. "
            "Install requirements.txt in the same interpreter used to run the app."
        )
        return None

    creds = None
    token_path = _token_file()

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as exc:
            print(f"[GMAIL] Failed to load token file '{token_path}': {exc}")
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
        except Exception as exc:
            print(f"[GMAIL] Token refresh failed: {exc}")
            creds = None

    if creds and creds.valid:
        return creds

    credentials_path = _credentials_file()
    if not credentials_path.exists():
        print(
            f"[GMAIL] Missing credentials file '{credentials_path}'. "
            "Set GMAIL_CREDENTIALS_FILE to your OAuth client JSON path."
        )
        return None

    allow_interactive = _as_bool(os.environ.get("GMAIL_ALLOW_INTERACTIVE_OAUTH", "false"))
    if not allow_interactive:
        print(
            "[GMAIL] Token is missing/invalid and interactive OAuth is disabled. "
            "Set GMAIL_ALLOW_INTERACTIVE_OAUTH=true for first-time setup."
        )
        return None

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
        print(f"[GMAIL] OAuth token saved to '{token_path}'.")
        return creds
    except Exception as exc:
        print(f"[GMAIL] Interactive OAuth flow failed: {exc}")
        return None


def send_email_via_gmail(subject: str, body: str, to_email: str, from_email: str = "") -> bool:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ModuleNotFoundError:
        print(
            "[GMAIL] google-api-python-client is missing in the current Python environment. "
            "Install requirements.txt in the same interpreter used to run the app."
        )
        return False

    if not to_email:
        print("[GMAIL] Missing recipient email (EMAIL_TO).")
        return False

    creds = _load_credentials()
    if not creds:
        return False

    message = EmailMessage()
    message["To"] = to_email
    if from_email:
        message["From"] = from_email
    message["Subject"] = subject
    message.set_content(body, subtype="plain", charset="utf-8")

    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        result = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": encoded_message})
            .execute()
        )
        message_id = result.get("id", "unknown")
        print(f"[GMAIL] Email sent to {to_email} | message_id={message_id}")
        return True
    except HttpError as exc:
        print(f"[GMAIL] API error while sending email: {exc}")
        return False
    except Exception as exc:
        print(f"[GMAIL] Unexpected error while sending email: {exc}")
        return False
