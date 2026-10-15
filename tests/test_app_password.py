"""Gmail app-password sending (no OAuth, no expiry)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.notifications.gmail import send_email
from app.notifications.templates import Email


class TestAppPassword(unittest.TestCase):
    @patch("smtplib.SMTP_SSL")
    def test_uses_smtp_when_app_password_set(self, mock_smtp: MagicMock) -> None:
        server = mock_smtp.return_value.__enter__.return_value
        with patch.dict(os.environ, {"GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop"}):
            result = send_email(Email("Subj", "text", "<p>html</p>"),
                                "me@gmail.com", "me@gmail.com", "", "")
        self.assertEqual(result, "smtp")
        mock_smtp.assert_called_once()
        self.assertEqual(mock_smtp.call_args.args[:2], ("smtp.gmail.com", 465))
        server.login.assert_called_once_with("me@gmail.com", "abcdefghijklmnop")
        server.sendmail.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
