"""
The Chat tools call users().availability(), a resource that only newer releases of
google-api-python-client ship in their bundled API description. The other Chat tests
mock the service, so they cannot notice an installed library that is too old.
"""

from google.auth.credentials import AnonymousCredentials
from googleapiclient.discovery import build


def test_installed_chat_client_has_users_availability():
    service = build(
        "chat", "v1", credentials=AnonymousCredentials(), cache_discovery=False
    )
    availability = service.users().availability()
    for method in ("get", "patch", "markAsDoNotDisturb", "markAsActive"):
        assert hasattr(availability, method), method
