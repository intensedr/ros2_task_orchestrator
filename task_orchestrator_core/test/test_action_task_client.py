from action_msgs.msg import GoalStatus
from example_interfaces.action import Fibonacci

from task_orchestrator_core.clients.action_task import (
    ActionTaskClient,
    ActionTaskDataError,
    ActionTaskRejected,
    ActionTaskServerUnavailable,
)
from task_orchestrator_core.task_models import TaskDefinition


class FakeFuture:
    def __init__(self, result=None, exception=None, done=True):
        self._result = result
        self._exception = exception
        self._done = done

    def add_done_callback(self, callback):
        if self._done:
            callback(self)

    def done(self):
        return self._done

    def exception(self):
        return self._exception

    def result(self):
        return self._result


class FakeGoalHandle:
    def __init__(self, accepted=True, result_response=None, cancel_response=None):
        self.accepted = accepted
        self.result_response = result_response
        self.cancel_response = cancel_response

    def get_result_async(self):
        return FakeFuture(self.result_response)

    def cancel_goal_async(self):
        return FakeFuture(self.cancel_response)


class FakeResultResponse:
    def __init__(self, status, result):
        self.status = status
        self.result = result


class FakeCancelResponse:
    def __init__(self, goals_canceling):
        self.goals_canceling = goals_canceling


class FakeActionClient:
    def __init__(self, available=True, goal_handle=None):
        self.available = available
        self.goal_handle = goal_handle
        self.goal = None
        self.timeout_sec = None

    def wait_for_server(self, timeout_sec=None):
        self.timeout_sec = timeout_sec
        return self.available

    def send_goal_async(self, goal):
        self.goal = goal
        return FakeFuture(self.goal_handle)


def _action_task() -> TaskDefinition:
    return TaskDefinition(
        task_name="example/fibonacci",
        topic="/example/fibonacci",
        msg_interface="example_interfaces/action/Fibonacci",
        task_server_type="action",
        cancel_timeout=3.0,
    )


def _client_with_fake_action(fake_action_client):
    client = ActionTaskClient(node=object())
    client._clients[("/example/fibonacci", "example_interfaces/action/Fibonacci")] = fake_action_client
    return client


def test_action_task_client_converts_json_goal_and_result():
    result_response = FakeResultResponse(
        status=GoalStatus.STATUS_SUCCEEDED,
        result=Fibonacci.Result(sequence=[1, 1, 2]),
    )
    fake_action_client = FakeActionClient(goal_handle=FakeGoalHandle(result_response=result_response))
    client = _client_with_fake_action(fake_action_client)

    prepared = client.prepare(_action_task(), '{"order": 5}')
    result = client.execute(prepared, task_id="task-1")

    assert prepared.goal.order == 5
    assert fake_action_client.goal.order == 5
    assert fake_action_client.timeout_sec == 3.0
    assert result.result_json == '{"sequence": [1, 1, 2]}'


def test_action_task_client_rejects_invalid_json():
    client = ActionTaskClient(node=object())

    try:
        client.prepare(_action_task(), "{")
    except ActionTaskDataError as exc:
        assert "not valid JSON" in str(exc)
    else:
        raise AssertionError("expected ActionTaskDataError")


def test_action_task_client_reports_unavailable_action_server():
    fake_action_client = FakeActionClient(available=False)
    client = _client_with_fake_action(fake_action_client)
    prepared = client.prepare(_action_task(), '{"order": 5}')

    try:
        client.execute(prepared, task_id="task-1")
    except ActionTaskServerUnavailable as exc:
        assert "/example/fibonacci" in str(exc)
    else:
        raise AssertionError("expected ActionTaskServerUnavailable")


def test_action_task_client_reports_rejected_goal():
    fake_action_client = FakeActionClient(goal_handle=FakeGoalHandle(accepted=False))
    client = _client_with_fake_action(fake_action_client)
    prepared = client.prepare(_action_task(), '{"order": 5}')

    try:
        client.execute(prepared, task_id="task-1")
    except ActionTaskRejected as exc:
        assert "/example/fibonacci" in str(exc)
    else:
        raise AssertionError("expected ActionTaskRejected")


def test_action_task_client_cancels_active_goal():
    goal_handle = FakeGoalHandle(cancel_response=FakeCancelResponse(goals_canceling=[object()]))
    client = ActionTaskClient(node=object())
    client._goal_handles["task-1"] = goal_handle

    assert client.cancel("task-1", timeout_sec=3.0) is True


def test_action_task_client_cancel_returns_false_without_goal_handle():
    client = ActionTaskClient(node=object())

    assert client.cancel("missing-task", timeout_sec=3.0) is False
