"""Unit tests for the Workspace-directory fallback that names coworkers who are not in
the caller's contacts (People getBatchGet returns them without a name)."""

import json
from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gchat import chat_helpers
from gchat.chat_helpers import _lookup_people_names, _name_spaces


@pytest.fixture(autouse=True)
def _reset_name_state():
    def reset():
        chat_helpers._sender_name_cache.clear()
        chat_helpers._domain_directory_names.clear()
        chat_helpers._domain_directory_loaded = False

    reset()
    yield
    reset()


def _request(result=None, error=None):
    request = Mock()
    if error is not None:
        request.execute.side_effect = error
    else:
        request.execute.return_value = result
    return request


def _http_error(status):
    body = json.dumps({"error": {"code": status, "message": "denied"}}).encode()
    return HttpError(httplib2.Response({"status": status}), body)


def _directory_person(user_id, name):
    return {"resourceName": f"people/{user_id}", "names": [{"displayName": name}]}


def _people_service(contacts=None, directory_pages=None, directory_error=None):
    """getBatchGet names only `contacts` (id -> name); every other person comes back
    with no name, as People does for coworkers outside the caller's contacts.
    listDirectoryPeople serves `directory_pages` (a list of pages of people)."""
    contacts = contacts or {}
    service = Mock()
    calls = {"directory": 0}

    def get(resourceName, personFields):
        return _request({"resourceName": "people/100"})  # the caller

    def get_batch_get(resourceNames, personFields):
        responses = []
        for resource_name in resourceNames:
            user_id = resource_name.split("/")[1]
            person = {"resourceName": resource_name}
            if user_id in contacts:
                person["names"] = [{"displayName": contacts[user_id]}]
            responses.append(
                {
                    "httpStatusCode": 200,
                    "requestedResourceName": resource_name,
                    "person": person,
                }
            )
        return _request({"responses": responses})

    def list_directory_people(**kwargs):
        calls["directory"] += 1
        if directory_error is not None:
            return _request(error=directory_error)
        pages = directory_pages or [[]]
        token = kwargs.get("pageToken")
        index = int(token) if token else 0
        body = {"people": pages[index]}
        if index + 1 < len(pages):
            body["nextPageToken"] = str(index + 1)
        return _request(body)

    people = service.people.return_value
    people.get.side_effect = get
    people.getBatchGet.side_effect = get_batch_get
    people.listDirectoryPeople.side_effect = list_directory_people
    return service, calls


@pytest.mark.asyncio
async def test_non_contact_coworker_is_named_from_the_directory():
    service, calls = _people_service(
        contacts={"200": "Alice Smith"},
        directory_pages=[[_directory_person("300", "Bob Jones")]],
    )

    names = await _lookup_people_names(service, ["users/200", "users/300"])

    assert names == {"users/200": "Alice Smith", "users/300": "Bob Jones"}
    assert calls["directory"] == 1


@pytest.mark.asyncio
async def test_directory_is_not_called_when_contacts_name_everyone():
    service, calls = _people_service(contacts={"200": "Alice Smith"})

    names = await _lookup_people_names(service, ["users/200"])

    assert names == {"users/200": "Alice Smith"}
    assert calls["directory"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 500])
async def test_directory_failure_leaves_the_id_unnamed_and_does_not_raise(status):
    service, calls = _people_service(
        contacts={"200": "Alice Smith"}, directory_error=_http_error(status)
    )

    names = await _lookup_people_names(service, ["users/200", "users/300"])

    assert names == {"users/200": "Alice Smith"}
    assert calls["directory"] == 1


@pytest.mark.asyncio
async def test_directory_is_listed_once_and_reused_across_calls():
    service, calls = _people_service(
        directory_pages=[
            [_directory_person("300", "Bob Jones"), _directory_person("400", "Carol")]
        ]
    )

    first = await _lookup_people_names(service, ["users/300"])
    second = await _lookup_people_names(service, ["users/400"])

    assert first == {"users/300": "Bob Jones"}
    assert second == {"users/400": "Carol"}
    assert calls["directory"] == 1


@pytest.mark.asyncio
async def test_empty_directory_listing_is_not_cached():
    service, calls = _people_service(directory_pages=[[]])

    await _lookup_people_names(service, ["users/300"])
    await _lookup_people_names(service, ["users/400"])

    assert chat_helpers._domain_directory_loaded is False
    assert calls["directory"] == 2


@pytest.mark.asyncio
async def test_directory_pagination_is_followed():
    service, calls = _people_service(
        directory_pages=[
            [_directory_person("300", "Bob Jones")],
            [_directory_person("400", "Carol White")],
        ]
    )

    names = await _lookup_people_names(service, ["users/300", "users/400"])

    assert names == {"users/300": "Bob Jones", "users/400": "Carol White"}
    assert calls["directory"] == 2


@pytest.mark.asyncio
async def test_dm_with_non_contact_coworker_is_labelled_with_the_directory_name():
    service, _ = _people_service(
        directory_pages=[[_directory_person("200", "Alice Smith")]]
    )
    chat_service = Mock()
    chat_service.spaces.return_value.members.return_value.list.side_effect = (
        lambda parent, pageSize: _request(
            {
                "memberships": [
                    {"member": {"name": "users/100", "type": "HUMAN"}},
                    {"member": {"name": "users/200", "type": "HUMAN"}},
                ]
            }
        )
    )
    spaces = [{"name": "spaces/DM1", "spaceType": "DIRECT_MESSAGE"}]

    labels = await _name_spaces(chat_service, service, spaces)

    assert labels == {"spaces/DM1": "Alice Smith"}
