import threading
import time

from task_orchestrator_media.models import DeviceConfig, FramePacket, SessionRecord
from task_orchestrator_media.runtime import FrameBuffer, RecordingWorker
from task_orchestrator_media.spool import SpoolStore


class _BlockingBundle:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def append(self, _frame):
        self.started.set()
        assert self.release.wait(timeout=1.0)

    def finalize(self, _session):
        return None


def test_frame_buffer_waits_for_newer_frame():
    buffer = FrameBuffer()
    received = []

    waiter = threading.Thread(
        target=lambda: received.append(buffer.wait_for_next(0, timeout_sec=1.0)),
        daemon=True,
    )
    waiter.start()
    buffer.push(b"jpeg", 1, "camera", "image/jpeg", ".jpg", "jpeg")
    waiter.join(timeout=1.0)

    assert received[0] is not None
    assert received[0].data == b"jpeg"
    received_count, captured_ns, received_monotonic = buffer.status()
    assert received_count == 1
    assert captured_ns == 1
    assert received_monotonic > 0


def test_recording_worker_drains_queue_and_finalizes(tmp_path):
    device = DeviceConfig(device_id="camera", source_topic="/image")
    session = SessionRecord(
        session_id="session-1",
        device_id="camera",
        media_kind="image",
        state="RECORDING",
        task_id="task",
        mission_id="mission",
        idempotency_key="",
        started_ns=time.time_ns(),
        max_duration_sec=0.0,
        metadata_json="{}",
    )
    bundle = SpoolStore(tmp_path).begin_recording(device, session)
    worker = RecordingWorker(bundle, session, queue_size=4)
    for index in range(3):
        worker.enqueue(
            FramePacket(
                sequence=index + 1,
                data=f"frame-{index}".encode(),
                captured_ns=session.started_ns + index * 1_000_000,
                frame_id="camera",
                mime_type="image/jpeg",
                extension=".jpg",
            )
        )

    result = worker.stop()

    assert result.state == "FINALIZED"
    assert result.frame_count == 3
    assert result.size_bytes == 21
    assert len(result.artifacts) == 1


def test_recording_worker_bounds_queue_by_bytes():
    session = SessionRecord(
        session_id="bounded-session",
        device_id="camera",
        media_kind="image",
        state="RECORDING",
        task_id="task",
        mission_id="mission",
        idempotency_key="",
        started_ns=time.time_ns(),
        max_duration_sec=0.0,
        metadata_json="{}",
    )
    bundle = _BlockingBundle()
    worker = RecordingWorker(bundle, session, queue_size=4, queue_max_bytes=5)
    frame = FramePacket(
        sequence=1,
        data=b"four",
        captured_ns=session.started_ns,
        frame_id="camera",
        mime_type="image/jpeg",
        extension=".jpg",
    )

    worker.enqueue(frame)
    assert bundle.started.wait(timeout=1.0)
    worker.enqueue(frame)

    assert worker.snapshot().dropped_frames == 1
    bundle.release.set()
    assert worker.stop().state == "FINALIZED"
