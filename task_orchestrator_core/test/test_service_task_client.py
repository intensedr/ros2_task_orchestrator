from std_srvs.srv import SetBool

from task_orchestrator_core.clients.service_task import (
    ServiceTaskClient,
    ServiceTaskDataError,
    ServiceTaskServerUnavailable,
)
from task_orchestrator_core.task_models import TaskDefinition


class FakeClient:
    def __init__(self, available=True, response=None):
        self.available = available
        self.response = response
        self.request = None
        self.timeout_sec = None

    def wait_for_service(self, timeout_sec=None):
        self.timeout_sec = timeout_sec
        return self.available

    def call(self, request, timeout_sec=None):
        self.request = request
        self.timeout_sec = timeout_sec
        return self.response


class FakeNode:
    def __init__(self, client):
        self.client = client
        self.created_clients = []

    def create_client(self, srv_type, srv_name, callback_group=None):
        self.created_clients.append((srv_type, srv_name, callback_group))
        return self.client


def _service_task() -> TaskDefinition:
    return TaskDefinition(
        task_name="example/set_bool",
        topic="/example/set_bool",
        msg_interface="std_srvs/srv/SetBool",
        task_server_type="service",
        cancel_timeout=3.0,
    )


def test_service_task_client_converts_json_request_and_response():
    fake_client = FakeClient(response=SetBool.Response(success=True, message="ok"))
    client = ServiceTaskClient(FakeNode(fake_client))

    prepared = client.prepare(_service_task(), '{"data": true}')
    result = client.execute(prepared)

    assert prepared.request.data is True
    assert fake_client.request.data is True
    assert fake_client.timeout_sec == 3.0
    assert result.task_result_json == '{"message": "ok", "success": true}'


def test_service_task_client_rejects_invalid_json():
    client = ServiceTaskClient(FakeNode(FakeClient()))

    try:
        client.prepare(_service_task(), "{")
    except ServiceTaskDataError as exc:
        assert "not valid JSON" in str(exc)
    else:
        raise AssertionError("expected ServiceTaskDataError")


def test_service_task_client_reports_unavailable_service():
    fake_client = FakeClient(available=False)
    client = ServiceTaskClient(FakeNode(fake_client))
    prepared = client.prepare(_service_task(), '{"data": true}')

    try:
        client.execute(prepared)
    except ServiceTaskServerUnavailable as exc:
        assert "/example/set_bool" in str(exc)
    else:
        raise AssertionError("expected ServiceTaskServerUnavailable")
