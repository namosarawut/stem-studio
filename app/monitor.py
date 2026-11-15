"""Measures what a job costs the machine, stage by stage.

CPU time, RAM and GPU memory belong to this process. GPU utilization comes from
`ioreg` on macOS and `nvidia-smi` on an NVIDIA card (Windows or Linux), which only report
the whole machine (the window server and browsers use the GPU too), so a baseline taken
before the job starts is reported beside it.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import torch

GB = 1024 ** 3
MB = 1024 ** 2
SAMPLE_SECONDS = 0.5
GPU_SAMPLE_EVERY = 3  # ioreg takes about 200 ms, so run it on every third tick only
BASELINE_SAMPLES = 3


def _gpu_utilization() -> int | None:
    if sys.platform != "darwin":
        return _nvidia_utilization()
    try:
        output = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'"Device Utilization %"\s*=\s*(\d+)', output)
    return int(match.group(1)) if match else None


def _nvidia_utilization() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        output = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+", output)
    return int(match.group(0)) if match else None


def _sysctl(name: str) -> str | None:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=2).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _gpu_core_count() -> int | None:
    try:
        output = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'"gpu-core-count"\s*=\s*(\d+)', output)
    return int(match.group(1)) if match else None


def machine_info(device_label: str) -> dict:
    return {
        "cpu": _sysctl("machdep.cpu.brand_string") or platform.processor() or platform.machine(),
        "cpu_cores": psutil.cpu_count(logical=True),
        "gpu_cores": _gpu_core_count(),
        "ram_gb": round(psutil.virtual_memory().total / GB, 1),
        "accelerator": device_label,
    }


def folder_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _accelerator_memory(device: str) -> int:
    try:
        if device == "mps":
            return int(torch.mps.driver_allocated_memory())
        if device == "cuda":
            return int(torch.cuda.memory_reserved())
    except Exception:  # a failed reading must never fail the job being measured
        pass
    return 0


def _mean(values: list) -> float | None:
    return float(sum(values) / len(values)) if values else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


class ResourceMonitor:
    def __init__(self, device: str, device_label: str, models_dir: Path):
        self.device = device
        self.device_label = device_label
        self.models_dir = models_dir
        self.process = psutil.Process()
        self.stages: list[dict] = []
        self.current: dict | None = None
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="resource-monitor")
        self.peak_system_memory = 0.0

    def _cpu_seconds(self) -> float:
        times = self.process.cpu_times()
        return times.user + times.system

    def start(self) -> None:
        readings = []
        for _ in range(BASELINE_SAMPLES):
            reading = _gpu_utilization()
            if reading is not None:
                readings.append(reading)
            time.sleep(0.2)
        self.baseline_gpu = _mean(readings)
        self.models_before = folder_size(self.models_dir)
        self.swap_before = psutil.swap_memory().used
        self.started = time.time()
        self.cpu_started = self._cpu_seconds()
        self.thread.start()

    def _close_stage(self) -> None:
        if self.current:
            self.current["seconds"] = time.time() - self.current["started_at"]
            self.current["cpu_seconds"] = self._cpu_seconds() - self.current["cpu_at"]
            self.stages.append(self.current)
            self.current = None

    def stage(self, stage_id: str, label: str, hardware: str, measures_cpu: bool = True) -> None:
        """Start a stage, closing the previous one. Set measures_cpu=False when the work
        happens in a child process, whose CPU time is not this process's."""
        with self.lock:
            self._close_stage()
            self.current = {
                "id": stage_id, "label": label, "hardware": hardware, "measures_cpu": measures_cpu,
                "started_at": time.time(), "cpu_at": self._cpu_seconds(), "rss": [], "accel": [], "gpu": [],
            }

    def _loop(self) -> None:
        tick = 0
        while not self.halt.wait(SAMPLE_SECONDS):
            rss = self.process.memory_info().rss
            accel = _accelerator_memory(self.device)
            gpu = _gpu_utilization() if tick % GPU_SAMPLE_EVERY == 0 else None
            self.peak_system_memory = max(self.peak_system_memory, psutil.virtual_memory().percent)
            tick += 1
            with self.lock:
                if self.current is not None:
                    self.current["rss"].append(rss)
                    self.current["accel"].append(accel)
                    if gpu is not None:
                        self.current["gpu"].append(gpu)

    def stop(self) -> None:
        with self.lock:
            self._close_stage()
        self.halt.set()
        self.thread.join(timeout=3)
        self.seconds = time.time() - self.started
        self.cpu_seconds = self._cpu_seconds() - self.cpu_started

    def report(self, audio_seconds: float, job_folder: Path, source: Path) -> dict:
        stages = []
        all_rss, all_accel, active_gpu = [], [], []
        for stage in self.stages:
            all_rss += stage["rss"]
            all_accel += stage["accel"]
            uses_accelerator = stage["hardware"] == self.device_label
            if uses_accelerator:
                active_gpu += stage["gpu"]
            stages.append({
                "id": stage["id"],
                "label": stage["label"],
                "hardware": stage["hardware"],
                "seconds": round(stage["seconds"], 1),
                "cpu_cores": _round(stage["cpu_seconds"] / max(stage["seconds"], 0.001)) if stage["measures_cpu"] else None,
                "ram_peak_gb": _round(max(stage["rss"]) / GB, 2) if stage["rss"] else None,
                "gpu_memory_peak_gb": _round(max(stage["accel"]) / GB, 2) if stage["accel"] and max(stage["accel"]) else None,
                # Machine-wide, so on a CPU stage it would only show what other apps are doing.
                "gpu_util_avg": _round(_mean(stage["gpu"]), 0) if uses_accelerator else None,
            })

        downloaded = folder_size(self.models_dir) - self.models_before
        disk = shutil.disk_usage(job_folder)
        return {
            "machine": machine_info(self.device_label),
            "seconds": round(self.seconds, 1),
            "audio_seconds": round(audio_seconds, 1),
            "speed_vs_audio": round(audio_seconds / max(self.seconds, 0.001), 2),
            "cpu": {
                "seconds": round(self.cpu_seconds, 1),
                "avg_cores": round(self.cpu_seconds / max(self.seconds, 0.001), 2),
            },
            "ram": {
                "peak_gb": _round(max(all_rss) / GB, 2) if all_rss else None,
                "system_peak_pct": round(self.peak_system_memory, 1),
                "swap_added_gb": round(max(0, psutil.swap_memory().used - self.swap_before) / GB, 2),
            },
            "gpu": {
                "util_before_pct": _round(self.baseline_gpu, 0),
                "util_during_pct": _round(_mean(active_gpu), 0),
                "util_peak_pct": max(active_gpu) if active_gpu else None,
                "memory_peak_gb": _round(max(all_accel) / GB, 2) if all_accel and max(all_accel) else None,
            },
            "disk": {
                "source_mb": round(source.stat().st_size / MB, 1),
                "stems_mb": round(folder_size(job_folder / "stems") / MB, 1),
                "job_total_mb": round(folder_size(job_folder) / MB, 1),
                "models_mb": round(folder_size(self.models_dir) / MB),
                "free_gb": round(disk.free / GB, 1),
            },
            "network": {"downloaded_mb": round(max(0, downloaded) / MB)},
            "stages": stages,
        }
