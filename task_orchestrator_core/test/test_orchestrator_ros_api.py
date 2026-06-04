import json
import os
import threading
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from std_srvs.srv import SetBool

from task_orchestrator_core.constants import ACTION_EXECUTE_TASK, TOPIC_RESULTS
from task_orchestrator_core.orchestrator_node import TaskOrchestratorNode
from task_orchestrator_core.task_models import TaskDefinition
from task_orchestrator_msgs.action import ExecuteTaskV1
from task_orchestrator_msgs.msg import TaskResultV1, TaskStatusV1
from task_orchestrator_msgs.srv import ListEventsV1, ListTaskRecordsV1


def _wait_for_future(future, timeout_sec: float):
    done = threading.Event()
    future.add_done_callback(lambda _: done.set())
    if not future.done() and not done.wait(timeout_sec):
        raise AssertionError("timed out waiting for ROS2 future")
    return future.result()


def _start_executor(*nodes):
    executor = MultiThreadedExecutor(num_threads=4)
    for node in nodes:
        executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    return executor, thread


def _stop_executor(executor, thread, *nodes):
    executor.shutdown()
    thread.join(timeout=2.0)
    for node in nodes:
        node.destroy_node()
    rclpy.try_shutdown()


def _make_goal(task_id: str, task_name: str, task_data_json: str) -> ExecuteTaskV1.Goal:
    goal = ExecuteTaskV1.Goal()
    goal.api_version = "v1beta1"
    goal.task_id = task_id
    goal.task_name = task_name
    goal.source = "integration-test"
    goal.correlation_id = f"{task_id}-corr"
    goal.task_data_json = task_data_json
    return goal


def _call_service(client_node, service_type, service_name: str, request, timeout_sec: float):
    client = client_node.create_client(service_type, service_name)
    assert client.wait_for_service(timeout_sec=timeout_sec)
    return _wait_for_future(client.call_async(request), timeout_sec=timeout_sec)


def test_execute_system_wait_through_ros_action_api(tmp_path):
    rclpy.try_shutdown()
    ros_log_dir = tmp_path / "ros_log"
    ros_log_dir.mkdir()
    os.environ["ROS_LOG_DIR"] = str(ros_log_dir)
    rclpy.init(args=None)

    orchestrator = TaskOrchestratorNode()
    client_node = rclpy.create_node("task_orchestrator_wait_client_test")
    results: list[TaskResultV1] = []
    client_node.create_subscription(TaskResultV1, TOPIC_RESULTS, results.append, 10)
    executor, thread = _start_executor(orchestrator, client_node)

    try:
        client = ActionClient(client_node, ExecuteTaskV1, ACTION_EXECUTE_TASK)
        assert client.wait_for_server(timeout_sec=2.0)

        goal_handle = _wait_for_future(
            client.send_goal_async(_make_goal("wait-api-task", "system/wait", '{"duration_sec": 0}')),
            timeout_sec=2.0,
        )
        result_response = _wait_for_future(goal_handle.get_result_async(), timeout_sec=2.0)

        time.sleep(0.1)

        assert result_response.result.status == TaskStatusV1.DONE
        assert result_response.result.result_json == '{"duration_sec": 0.0}'
        assert [result.task_id for result in results] == ["wait-api-task"]
        assert results[0].status == TaskStatusV1.DONE
    finally:
        _stop_executor(executor, thread, orchestrator, client_node)


def test_late_client_recovers_task_records_and_events(tmp_path):
    rclpy.try_shutdown()
    ros_log_dir = tmp_path / "ros_log"
    ros_log_dir.mkdir()
    os.environ["ROS_LOG_DIR"] = str(ros_log_dir)
    rclpy.init(args=None)

    orchestrator = TaskOrchestratorNode()
    client_node = rclpy.create_node("task_orchestrator_recovery_client_test")
    executor, thread = _start_executor(orchestrator, client_node)

    try:
        action_client = ActionClient(client_node, ExecuteTaskV1, ACTION_EXECUTE_TASK)
        assert action_client.wait_for_server(timeout_sec=2.0)

        goal_handle = _wait_for_future(
            action_client.send_goal_async(_make_goal("recovery-task", "system/wait", '{"duration_sec": 0}')),
            timeout_sec=2.0,
        )
        result_response = _wait_for_future(goal_handle.get_result_async(), timeout_sec=2.0)
        assert result_response.result.status == TaskStatusV1.DONE

        late_client_node = rclpy.create_node("task_orchestrator_late_client_test")
        executor.add_node(late_client_node)

        records_request = ListTaskRecordsV1.Request()
        records_request.task_name = "system/wait"
        records_response = _call_service(
            late_client_node,
            ListTaskRecordsV1,
            "/task_orchestrator/list_task_records",
            records_request,
            timeout_sec=2.0,
        )
        events_request = ListEventsV1.Request()
        events_request.task_id = "recovery-task"
        events_response = _call_service(
            late_client_node,
            ListEventsV1,
            "/task_orchestrator/list_events",
            events_request,
            timeout_sec=2.0,
        )

        assert [record.result.task_id for record in records_response.records] == ["recovery-task"]
        assert records_response.records[0].result.status == TaskStatusV1.DONE
        assert [event.event_type for event in events_response.events] == [
            "task.completed",
            "task.started",
            "task.received",
        ]
    finally:
        if "late_client_node" in locals():
            executor.remove_node(late_client_node)
            late_client_node.destroy_node()
        _stop_executor(executor, thread, orchestrator, client_node)


def test_execute_service_backed_task_through_ros_action_api(tmp_path):
    rclpy.try_shutdown()
    ros_log_dir = tmp_path / "ros_log"
    ros_log_dir.mkdir()
    os.environ["ROS_LOG_DIR"] = str(ros_log_dir)
    rclpy.init(args=None)

    orchestrator = TaskOrchestratorNode()
    orchestrator._task_registry.add(
        TaskDefinition(
            task_name="example/set_bool",
            topic="/example/set_bool",
            msg_interface="std_srvs/srv/SetBool",
            task_server_type="service",
        )
    )
    client_node = rclpy.create_node("task_orchestrator_service_client_test")
    service_node = rclpy.create_node("task_orchestrator_set_bool_server_test")

    def handle_set_bool(request, response):
        response.success = request.data
        response.message = "enabled" if request.data else "disabled"
        return response

    service_node.create_service(SetBool, "/example/set_bool", handle_set_bool)
    executor, thread = _start_executor(orchestrator, client_node, service_node)

    try:
        client = ActionClient(client_node, ExecuteTaskV1, ACTION_EXECUTE_TASK)
        assert client.wait_for_server(timeout_sec=2.0)

        goal_handle = _wait_for_future(
            client.send_goal_async(_make_goal("service-api-task", "example/set_bool", '{"data": true}')),
            timeout_sec=2.0,
        )
        result_response = _wait_for_future(goal_handle.get_result_async(), timeout_sec=2.0)
        result_payload = json.loads(result_response.result.result_json)

        assert result_response.result.status == TaskStatusV1.DONE
        assert result_payload == {"message": "enabled", "success": True}
    finally:
        _stop_executor(executor, thread, orchestrator, client_node, service_node)
