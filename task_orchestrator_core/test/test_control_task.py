import json

from task_orchestrator_core.system_tasks.control import ControlTaskParser, ControlTaskValidationError


def test_cancel_task_parser_accepts_task_ids_and_selectors():
    parser = ControlTaskParser()

    request = parser.parse_cancel(
        json.dumps(
            {
                "task_ids": ["task-1", "task-2"],
                "source": "test",
                "correlation_id": "corr-1",
            }
        )
    )

    assert request.task_ids == ("task-1", "task-2")
    assert request.source == "test"
    assert request.correlation_id == "corr-1"


def test_cancel_task_parser_rejects_invalid_task_ids():
    parser = ControlTaskParser()

    try:
        parser.parse_cancel('{"task_ids": "task-1"}')
    except ControlTaskValidationError as exc:
        assert "task_ids" in str(exc)
    else:
        raise AssertionError("expected ControlTaskValidationError")


def test_stop_task_parser_accepts_empty_payload():
    parser = ControlTaskParser()

    request = parser.parse_stop("")

    assert request.source == ""
    assert request.correlation_id == ""


def test_control_result_json_is_stable():
    parser = ControlTaskParser()

    cancel_result = parser.cancel_result_json(
        success=False,
        canceled_task_ids=["task-1"],
        failed_task_ids=["task-2"],
        error_code="TASK_CANCEL_FAILED",
        error_message="Some tasks could not be canceled.",
    )
    stop_result = parser.stop_result_json(
        success=True,
        stopped_task_ids=["task-1"],
    )

    assert json.loads(cancel_result) == {
        "success": False,
        "canceled_task_ids": ["task-1"],
        "failed_task_ids": ["task-2"],
        "error_code": "TASK_CANCEL_FAILED",
        "error_message": "Some tasks could not be canceled.",
    }
    assert json.loads(stop_result) == {
        "success": True,
        "stopped_task_ids": ["task-1"],
        "error_code": "",
        "error_message": "",
    }
