from task_orchestrator_core.system_tasks.wait import WaitTaskExecutor, WaitTaskValidationError


def test_wait_task_parses_and_executes_without_external_ros_server():
    slept_for = []
    executor = WaitTaskExecutor(sleep=slept_for.append)

    request = executor.parse('{"duration_sec": 1.5}')
    result = executor.execute(request)

    assert request.duration_sec == 1.5
    assert slept_for == [1.5]
    assert result.result_json == '{"duration_sec": 1.5}'


def test_wait_task_rejects_invalid_json():
    executor = WaitTaskExecutor(sleep=lambda _: None)

    try:
        executor.parse("{")
    except WaitTaskValidationError as exc:
        assert "not valid JSON" in str(exc)
    else:
        raise AssertionError("expected WaitTaskValidationError")


def test_wait_task_rejects_negative_duration():
    executor = WaitTaskExecutor(sleep=lambda _: None)

    try:
        executor.parse('{"duration_sec": -1}')
    except WaitTaskValidationError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("expected WaitTaskValidationError")
