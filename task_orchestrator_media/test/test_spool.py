import hashlib
import json
from dataclasses import replace

import pytest
from task_orchestrator_media.models import DeviceConfig, FramePacket, SessionRecord
from task_orchestrator_media.spool import SpoolQuotaExceeded, SpoolStore


def _device() -> DeviceConfig:
    return DeviceConfig(
        device_id="front_rgb",
        source_topic="/camera/image/compressed",
        frame_id="camera_optical_frame",
    )


def _frame(sequence: int, captured_ns: int, data: bytes = b"jpeg-data") -> FramePacket:
    return FramePacket(
        sequence=sequence,
        data=data,
        captured_ns=captured_ns,
        frame_id="camera_optical_frame",
        mime_type="image/jpeg",
        extension=".jpg",
        source_format="jpeg",
    )


def _session(session_id: str = "recording-1") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        device_id="front_rgb",
        media_kind="image",
        state="RECORDING",
        task_id="task-1",
        mission_id="mission-1",
        idempotency_key="recording-key",
        started_ns=1_700_000_000_000_000_000,
        max_duration_sec=0.0,
        metadata_json='{"purpose":"inspection"}',
    )


def test_write_snapshot_creates_media_and_sidecar(tmp_path):
    store = SpoolStore(tmp_path)
    frame = _frame(1, 1_700_000_000_100_000_000)

    artifact = store.write_snapshot(_device(), frame, "task-1", "mission-1", '{"label":"door"}')

    media_path = tmp_path / artifact.storage_key
    sidecar_path = media_path.with_name(f"{media_path.name}.json")
    assert media_path.read_bytes() == b"jpeg-data"
    assert json.loads(sidecar_path.read_text())["artifact_id"] == artifact.artifact_id
    assert artifact.sha256 == hashlib.sha256(b"jpeg-data").hexdigest()
    assert artifact.storage_key.startswith("snapshots/")
    assert not artifact.storage_key.startswith("/")


def test_artifact_upload_state_survives_restart(tmp_path):
    store = SpoolStore(tmp_path)
    artifact = store.write_snapshot(_device(), _frame(1, 1_700_000_000_100_000_000), "", "", "{}")
    available = replace(
        artifact,
        upload_state="AVAILABLE",
        remote_object_key=f"fleet/{artifact.storage_key}",
        public_url="https://media.example/fleet/image.jpg",
    )
    store.persist_artifact_upload(available)

    restored = {item.artifact_id: item for item in SpoolStore(tmp_path).load_artifacts()}

    assert restored[artifact.artifact_id].upload_state == "AVAILABLE"
    assert restored[artifact.artifact_id].remote_object_key == f"fleet/{artifact.storage_key}"


def test_write_snapshot_enforces_quota_before_media_write(tmp_path):
    store = SpoolStore(tmp_path, max_bytes=10)
    with pytest.raises(SpoolQuotaExceeded):
        store.write_snapshot(_device(), _frame(1, 1_700_000_000_000_000_000), "", "", "{}")
    assert list(tmp_path.rglob("*.jpg")) == []


def test_spool_rejects_unsafe_direct_device_and_extension(tmp_path):
    store = SpoolStore(tmp_path)
    unsafe_device = DeviceConfig(device_id="../camera", source_topic="/image")
    with pytest.raises(ValueError):
        store.write_snapshot(unsafe_device, _frame(1, 1), "", "", "{}")

    unsafe_frame = FramePacket(
        sequence=1,
        data=b"payload",
        captured_ns=1,
        frame_id="camera",
        mime_type="image/jpeg",
        extension="/../../payload",
    )
    with pytest.raises(ValueError):
        store.write_snapshot(_device(), unsafe_frame, "", "", "{}")


def test_recording_finalizes_frame_manifest_and_session(tmp_path):
    store = SpoolStore(tmp_path)
    session = _session()
    bundle = store.begin_recording(_device(), session)
    bundle.append(_frame(1, session.started_ns + 100_000_000, b"one"))
    bundle.append(_frame(2, session.started_ns + 200_000_000, b"two"))
    session.state = "FINALIZED"
    session.stopped_ns = session.started_ns + 300_000_000

    artifact = bundle.finalize(session)

    assert artifact is not None
    assert artifact.artifact_kind == "image_sequence"
    assert artifact.size_bytes == 6
    manifest = json.loads((tmp_path / artifact.storage_key).read_text())
    assert manifest["frame_index"] == "frames.jsonl"
    assert manifest["frame_count"] == 2
    assert manifest["first_frame"]["filename"] == "frames/00000001.jpg"
    assert manifest["last_frame"]["filename"] == "frames/00000002.jpg"
    assert manifest["artifact"]["sha256"] == artifact.sha256
    persisted_session = json.loads((tmp_path / artifact.storage_key).with_name("session.json").read_text())
    assert persisted_session["state"] == "FINALIZED"
    assert persisted_session["frame_count"] == 2


def test_recover_sessions_finalizes_open_recording(tmp_path):
    store = SpoolStore(tmp_path)
    session = _session("orphaned-session")
    bundle = store.begin_recording(_device(), session)
    bundle.append(_frame(1, session.started_ns + 100_000_000, b"recover-me"))

    recovered = SpoolStore(tmp_path).recover_sessions()

    assert len(recovered) == 1
    assert recovered[0].state == "RECOVERED"
    assert recovered[0].frame_count == 1
    assert len(recovered[0].artifacts) == 1
    assert (tmp_path / recovered[0].artifacts[0].storage_key).is_file()
    assert list(tmp_path.rglob(".finalize-reserve")) == []


def test_empty_recording_finishes_as_error(tmp_path):
    store = SpoolStore(tmp_path)
    session = _session("empty-session")
    bundle = store.begin_recording(_device(), session)
    session.state = "FINALIZED"
    bundle.finalize(session)

    assert session.state == "ERROR"
    assert session.error_code == "NO_FRAMES"


def test_recording_reserves_space_for_terminal_manifest(tmp_path):
    probe_store = SpoolStore(tmp_path / "probe")
    probe_session = _session("probe")
    probe_bundle = probe_store.begin_recording(_device(), probe_session)
    reserved_usage = probe_store.used_bytes
    probe_bundle.append(_frame(1, probe_session.started_ns, b"payload"))
    payload_usage = probe_store.used_bytes

    frame_growth = payload_usage - reserved_usage
    store = SpoolStore(tmp_path / "bounded")
    session = _session("bounded")
    bundle = store.begin_recording(_device(), session)
    assert store.used_bytes >= reserved_usage
    quota = store.used_bytes + frame_growth + 1
    store.max_bytes = quota
    bundle.append(_frame(1, session.started_ns, b"payload"))
    session.state = "FINALIZED"

    artifact = bundle.finalize(session)

    assert artifact is not None
    assert (store.root / artifact.storage_key).is_file()
    assert store.used_bytes <= quota
