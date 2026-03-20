from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen[str]] = set()

    def spawn(self, command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        popen_kwargs = {
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            **kwargs,
        }
        process: subprocess.Popen[str] = subprocess.Popen(command, **popen_kwargs)
        with self._lock:
            self._processes.add(process)
        return process

    def unregister(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if process in self._processes:
                self._processes.remove(process)

    def run(self, command: list[str], timeout: float | None = None, **kwargs: Any) -> ProcessResult:
        process = self.spawn(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return ProcessResult(process.returncode or 0, stdout or "", stderr or "")
        except subprocess.TimeoutExpired as exc:
            self.terminate_tree(process.pid)
            raise RuntimeError(f"Command timed out: {' '.join(command)}") from exc
        finally:
            self.unregister(process)

    def terminate_tree(self, pid: int) -> None:
        try:
            root = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return

        children = root.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.Error:
                continue
        psutil.wait_procs(children, timeout=1.5)

        for child in children:
            try:
                if child.is_running():
                    child.kill()
            except psutil.Error:
                continue

        try:
            root.terminate()
            root.wait(timeout=1.5)
        except psutil.Error:
            try:
                root.kill()
            except psutil.Error:
                pass

    def kill_all(self) -> None:
        with self._lock:
            processes = list(self._processes)
            self._processes.clear()

        for process in processes:
            if process.poll() is None:
                self.terminate_tree(process.pid)
