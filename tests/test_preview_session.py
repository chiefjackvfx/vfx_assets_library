import json
from pathlib import Path
from queue import Queue

from universal_asset_library.previews.blender_preview_session import (
    PROTOCOL_PREFIX,
    BlenderPreviewSession,
    BlenderPreviewSessionError,
)


class _OutputStream:
    def __init__(self) -> None:
        self.values = Queue()
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        value = self.values.get(timeout=2)
        if value is None:
            raise StopIteration
        return value

    def emit(self, value: dict) -> None:
        self.values.put(PROTOCOL_PREFIX + json.dumps(value) + "\n")

    def close(self) -> None:
        self.closed = True


class _InputStream:
    def __init__(self, process) -> None:
        self.process = process
        self.closed = False

    def write(self, value: str) -> int:
        command = json.loads(value)
        self.process.commands.append(command)
        if command["command"] == "shutdown":
            self.process.returncode = 0
            self.process.stdout.values.put(None)
        elif command["command"] == "render":
            if command["payload"].get("crash"):
                self.process.returncode = 1
                self.process.stdout.values.put(None)
                return len(value)
            if command["payload"].get("fail"):
                self.process.stdout.emit({
                    "event": "result",
                    "job_id": command["job_id"],
                    "status": "failed",
                    "diagnostic": "job failed",
                })
                return len(value)
            self.process.stdout.emit({
                "event": "progress",
                "job_id": command["job_id"],
                "message": f"Rendering {command['kind']}",
            })
            self.process.stdout.emit({
                "event": "result",
                "job_id": command["job_id"],
                "status": "ready",
                "metadata": {"kind": command["kind"]},
            })
        return len(value)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = command
        self.kwargs = kwargs
        self.returncode = None
        self.commands = []
        self.stdout = _OutputStream()
        self.stdin = _InputStream(self)
        self.terminated = False
        self.killed = False
        self.stdout.emit({"event": "ready", "blender_version": "5.2.0"})

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.stdout.values.put(None)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.values.put(None)


def test_session_reuses_one_process_for_texture_and_hdri_jobs(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "blender"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    texture_template = tmp_path / "texture.blend"
    hdri_template = tmp_path / "hdri.blend"
    texture_template.write_bytes(b"texture-template")
    hdri_template.write_bytes(b"hdri-template")
    processes = []

    def popen(command, **kwargs):
        process = _FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(
        "universal_asset_library.previews.blender_preview_session.subprocess.Popen",
        popen,
    )
    progress = []
    session = BlenderPreviewSession(str(executable))

    first = session.render(
        "texture", texture_template, {"asset": "A"},
        timeout_seconds=10, progress=progress.append,
    )
    second = session.render(
        "texture", texture_template, {"asset": "B"},
        timeout_seconds=10,
    )
    third = session.render(
        "hdri", hdri_template, {"asset": "C"},
        timeout_seconds=10,
    )
    first_hash = session.template_hash(texture_template)
    texture_template.write_bytes(b"changed-after-cache")
    assert session.template_hash(texture_template) == first_hash
    session.close()

    assert first == {"kind": "texture"}
    assert second == {"kind": "texture"}
    assert third == {"kind": "hdri"}
    assert progress == ["Rendering texture"]
    assert session.blender_version == "5.2.0"
    assert session.start_count == 1
    assert len(processes) == 1
    assert "--version" not in processes[0].command
    assert processes[0].kwargs["stdin"] is not None
    render_commands = [
        command for command in processes[0].commands
        if command["command"] == "render"
    ]
    assert [command["kind"] for command in render_commands] == [
        "texture", "texture", "hdri",
    ]
    assert processes[0].commands[-1] == {"command": "shutdown"}


def test_cancellation_terminates_session_and_next_job_restarts(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "blender"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    template = tmp_path / "template.blend"
    template.write_bytes(b"template")
    processes = []

    def popen(command, **kwargs):
        process = _FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(
        "universal_asset_library.previews.blender_preview_session.subprocess.Popen",
        popen,
    )
    token = type("Canceled", (), {"cancelled": True})()
    session = BlenderPreviewSession(str(executable))

    try:
        session.render(
            "texture", template, {}, timeout_seconds=10,
            cancel_token=token,
        )
    except BlenderPreviewSessionError as error:
        assert error.status == "canceled"
    else:
        raise AssertionError("Canceled session render unexpectedly succeeded")

    assert processes[0].terminated
    session.render("texture", template, {}, timeout_seconds=10)
    assert len(processes) == 2
    session.close()


def test_job_failure_keeps_session_but_process_crash_restarts_it(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "blender"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    template = tmp_path / "template.blend"
    template.write_bytes(b"template")
    processes = []

    def popen(command, **kwargs):
        process = _FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(
        "universal_asset_library.previews.blender_preview_session.subprocess.Popen",
        popen,
    )
    session = BlenderPreviewSession(str(executable))

    try:
        session.render(
            "texture", template, {"fail": True}, timeout_seconds=10
        )
    except BlenderPreviewSessionError as error:
        assert error.diagnostic == "job failed"
    else:
        raise AssertionError("Failed preview job unexpectedly succeeded")
    assert session.render(
        "texture", template, {}, timeout_seconds=10
    ) == {"kind": "texture"}
    assert len(processes) == 1

    try:
        session.render(
            "texture", template, {"crash": True}, timeout_seconds=10
        )
    except BlenderPreviewSessionError as error:
        assert "exited unexpectedly" in error.diagnostic
    else:
        raise AssertionError("Crashed preview process unexpectedly succeeded")
    assert session.render(
        "texture", template, {}, timeout_seconds=10
    ) == {"kind": "texture"}
    assert len(processes) == 2
    session.close()
