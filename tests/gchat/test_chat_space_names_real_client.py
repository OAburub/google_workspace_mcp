"""Regression test: DM labels must come out right through the REAL Google API client.

The tajah fork once looked DM memberships up five at a time through one shared service
object. A googleapiclient service wraps one httplib2.Http, which is not thread-safe, so
concurrent responses were crossed (a DM got another space's person) or lost. Mocked
services cannot see that, so this test drives _name_spaces through a real
googleapiclient service against a local fake Chat server (no Google, no credentials)
and also asserts the client never has two requests in flight at once.
"""

import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.discovery import build_from_document

from gchat import chat_helpers
from gchat.chat_helpers import _name_spaces

N_SPACES = 28
CALLER = "users/ME"


class _CountingHttp(httplib2.Http):
    """The httplib2 client a real googleapiclient service uses, counting how many
    requests are in flight through it at once. The count is taken on the client side
    on purpose: a shared connection can serialize the requests before the server
    sees them, which hides the concurrency that corrupts the responses."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._count_lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def request(self, *args, **kwargs):
        with self._count_lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            return super().request(*args, **kwargs)
        finally:
            with self._count_lock:
                self.in_flight -= 1


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like Google's endpoints

    def do_GET(self):
        time.sleep(random.uniform(0.0, 0.004))
        space_id = self.path.split("/spaces/")[1].split("/")[0]
        body = json.dumps(
            {
                "memberships": [
                    {"member": {"name": CALLER, "type": "HUMAN"}},
                    {"member": {"name": f"users/P{space_id}", "type": "HUMAN"}},
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _discovery(port):
    return {
        "kind": "discovery#restDescription",
        "name": "chat",
        "version": "v1",
        "protocol": "rest",
        "rootUrl": f"http://127.0.0.1:{port}/",
        "servicePath": "",
        "baseUrl": f"http://127.0.0.1:{port}/",
        "basePath": "",
        "batchPath": "batch",
        "parameters": {},
        "schemas": {
            "ListMembershipsResponse": {
                "id": "ListMembershipsResponse",
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
        },
        "resources": {
            "spaces": {
                "resources": {
                    "members": {
                        "methods": {
                            "list": {
                                "id": "chat.spaces.members.list",
                                "path": "v1/{+parent}/members",
                                "httpMethod": "GET",
                                "parameters": {
                                    "parent": {
                                        "type": "string",
                                        "required": True,
                                        "location": "path",
                                        "pattern": "^spaces/[^/]+$",
                                    },
                                    "pageSize": {
                                        "type": "integer",
                                        "location": "query",
                                    },
                                    "pageToken": {
                                        "type": "string",
                                        "location": "query",
                                    },
                                },
                                "parameterOrder": ["parent"],
                                "response": {"$ref": "ListMembershipsResponse"},
                            }
                        }
                    }
                }
            }
        },
    }


def _people_service():
    """Stub People API: user P<space id> is named 'Person-<space id>'."""
    service = Mock()
    people = service.people.return_value

    def execute_result(value):
        request = Mock()
        request.execute.return_value = value
        return request

    people.get.side_effect = lambda resourceName, personFields: execute_result(
        {"resourceName": "people/ME"}
    )

    def get_batch_get(resourceNames, personFields):
        responses = []
        for resource_name in resourceNames:
            space_id = resource_name.split("/")[1][1:]
            responses.append(
                {
                    "httpStatusCode": 200,
                    "requestedResourceName": resource_name,
                    "person": {
                        "resourceName": resource_name,
                        "names": [{"displayName": f"Person-{space_id}"}],
                    },
                }
            )
        return execute_result({"responses": responses})

    people.getBatchGet.side_effect = get_batch_get
    return service


@pytest.fixture()
def fake_chat_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    chat_helpers._sender_name_cache.clear()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        chat_helpers._sender_name_cache.clear()


@pytest.mark.asyncio
async def test_every_dm_gets_its_own_participant_through_the_real_client(
    fake_chat_server,
):
    port = fake_chat_server
    http = _CountingHttp(timeout=10)
    chat_service = build_from_document(_discovery(port), http=http)
    spaces = [
        {"name": f"spaces/S{i:03d}", "spaceType": "DIRECT_MESSAGE"}
        for i in range(N_SPACES)
    ]

    labels = await _name_spaces(chat_service, _people_service(), spaces)

    expected = {
        space["name"]: f"Person-{space['name'].split('/')[1]}" for space in spaces
    }
    assert labels == expected
    # One shared client must never carry two requests at once (httplib2 is not thread-safe).
    assert http.max_in_flight == 1
