"""system_profile.py — Detect hardware and derive optimal inference settings.

Detects (in priority order):
  1. Apple Silicon (Metal) — unified memory, all layers on GPU via Metal
  2. NVIDIA GPU (CUDA)     — dedicated VRAM; falls back to CPU if model too large
  3. AMD GPU (ROCm)        — dedicated VRAM; same fallback logic as CUDA
  4. CPU only              — x86 or generic ARM, no accelerator

Public API
----------
  profile = build_system_profile()   # call once at startup
  path, n_gpu_layers = select_gguf_model(profile, model_dir)
  device = embedding_device(profile)  # "mps" | "cuda" | "cpu"
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import psutil


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Runtime memory multiplier: GGUF file × factor ≈ peak RAM/VRAM usage.
_GPU_RUNTIME_FACTOR = 1.15   # GPU KV-cache overhead
_CPU_RUNTIME_FACTOR = 1.30   # CPU + swap pressure

# Headroom to keep free after the model loads.
# Metal uses unified memory that macOS can page/compress; psutil's `available`
# is already conservative, so 1.5 GB is sufficient.
# CPU needs more breathing room for the OS and other processes.
_HEADROOM = {
    "metal": 1.5,
    "cuda":  1.0,   # VRAM check — GPU memory is more predictable
    "rocm":  1.0,
    "cpu":   3.0,
}

# codermodel/ lives inside the project root (same dir as this file).
_DEFAULT_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "codermodel"
)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class SystemProfile:
    """Hardware facts + derived inference settings for llama-cpp-python."""
    backend: str              # "metal" | "cuda" | "rocm" | "cpu"
    gpu_name: Optional[str]
    gpu_vram_gb: Optional[float]    # None for Metal (unified) and CPU
    gpu_vram_free_gb: Optional[float]
    available_ram_gb: float         # psutil free RAM at profiling time
    cpu_count: int
    n_threads: int                  # suggested thread count for Llama()

    def __str__(self) -> str:
        gpu_str = (
            f"{self.gpu_name} ({self.gpu_vram_gb:.1f} GB VRAM, "
            f"{self.gpu_vram_free_gb:.1f} GB free)"
            if self.gpu_vram_gb is not None
            else (self.gpu_name or "—")
        )
        return (
            f"SystemProfile(backend={self.backend}, gpu={gpu_str}, "
            f"ram_free={self.available_ram_gb:.1f} GB, "
            f"threads={self.n_threads})"
        )


# ---------------------------------------------------------------------------
# Hardware probes
# ---------------------------------------------------------------------------

def _probe_cuda() -> Optional[Tuple[str, float, float]]:
    """Return (gpu_name, total_vram_gb, free_vram_gb) for the first NVIDIA GPU."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            name = parts[0]
            total_gb = int(parts[1]) / 1024
            free_gb = int(parts[2]) / 1024
            return name, total_gb, free_gb
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return None


def _probe_rocm() -> Optional[Tuple[str, float, float]]:
    """Return (gpu_name, total_vram_gb, free_vram_gb) for the first AMD GPU."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            for card_key, card_data in data.items():
                total_b = card_data.get("VRAM Total Memory (B)", 0)
                used_b = card_data.get("VRAM Total Used Memory (B)", 0)
                if total_b > 0:
                    total_gb = total_b / (1024 ** 3)
                    free_gb = (total_b - used_b) / (1024 ** 3)
                    return card_key, total_gb, free_gb
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return None


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------

def build_system_profile() -> SystemProfile:
    """Detect hardware and return a SystemProfile.

    Call once at startup — probes involve subprocess calls and should not
    be repeated in a hot path.
    """
    cpu_count = os.cpu_count() or 4
    available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)

    # ------------------------------------------------------------------
    # 1. Apple Silicon (Metal) — unified memory
    # ------------------------------------------------------------------
    if sys.platform == "darwin" and platform.machine() == "arm64":
        chip = _apple_chip_name()
        return SystemProfile(
            backend="metal",
            gpu_name=chip,
            gpu_vram_gb=None,       # unified — same pool as RAM
            gpu_vram_free_gb=None,
            available_ram_gb=available_ram_gb,
            cpu_count=cpu_count,
            n_threads=max(1, cpu_count - 1),
        )

    # ------------------------------------------------------------------
    # 2. NVIDIA CUDA
    # ------------------------------------------------------------------
    cuda = _probe_cuda()
    if cuda:
        gpu_name, total_vram_gb, free_vram_gb = cuda
        return SystemProfile(
            backend="cuda",
            gpu_name=gpu_name,
            gpu_vram_gb=total_vram_gb,
            gpu_vram_free_gb=free_vram_gb,
            available_ram_gb=available_ram_gb,
            cpu_count=cpu_count,
            # Fewer CPU threads when GPU does the heavy lifting
            n_threads=max(1, min(4, cpu_count // 2)),
        )

    # ------------------------------------------------------------------
    # 3. AMD ROCm
    # ------------------------------------------------------------------
    rocm = _probe_rocm()
    if rocm:
        gpu_name, total_vram_gb, free_vram_gb = rocm
        return SystemProfile(
            backend="rocm",
            gpu_name=gpu_name,
            gpu_vram_gb=total_vram_gb,
            gpu_vram_free_gb=free_vram_gb,
            available_ram_gb=available_ram_gb,
            cpu_count=cpu_count,
            n_threads=max(1, min(4, cpu_count // 2)),
        )

    # ------------------------------------------------------------------
    # 4. CPU only
    # ------------------------------------------------------------------
    return SystemProfile(
        backend="cpu",
        gpu_name=None,
        gpu_vram_gb=None,
        gpu_vram_free_gb=None,
        available_ram_gb=available_ram_gb,
        cpu_count=cpu_count,
        n_threads=max(1, cpu_count - 1),
    )


def _apple_chip_name() -> str:
    """Return a human-readable Apple chip name (e.g. 'Apple M4')."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "Apple Silicon"


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

def select_gguf_model(
    profile: SystemProfile,
    model_dir: Optional[str] = None,
) -> Optional[Tuple[str, int]]:
    """Return ``(model_path, n_gpu_layers)`` for the best model that fits.

    Selection strategy (largest = most accurate first):
      - Apple Silicon / CPU: model must fit in available system RAM.
      - CUDA / ROCm:
          * Try full GPU offload (model fits in free VRAM) → n_gpu_layers=-1.
          * Fall back to CPU execution (model fits in RAM) → n_gpu_layers=0.

    Returns None if no model fits under any configuration.
    """
    search_dir = model_dir or os.environ.get("GGUF_MODEL_DIR", _DEFAULT_MODEL_DIR)
    if not os.path.isdir(search_dir):
        return None

    candidates: List[str] = sorted(
        [
            os.path.join(search_dir, f)
            for f in os.listdir(search_dir)
            if f.endswith(".gguf") and os.path.isfile(os.path.join(search_dir, f))
        ],
        key=os.path.getsize,
        reverse=True,   # largest (most accurate) first
    )
    if not candidates:
        return None

    # Refresh available RAM at selection time (may differ from profile build time)
    available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)

    for path in candidates:
        size_gb = os.path.getsize(path) / (1024 ** 3)
        name = os.path.basename(path)

        if profile.backend == "metal":
            headroom = _HEADROOM["metal"]
            needed = size_gb * _GPU_RUNTIME_FACTOR + headroom
            if needed <= available_ram_gb:
                _log_choice(name, size_gb, "Metal (unified)", available_ram_gb)
                return path, -1
            _log_skip(name, size_gb, needed, available_ram_gb)

        elif profile.backend in ("cuda", "rocm"):
            vram_free = profile.gpu_vram_free_gb or 0.0
            gpu_headroom = _HEADROOM[profile.backend]
            gpu_needed = size_gb * _GPU_RUNTIME_FACTOR + gpu_headroom
            if gpu_needed <= vram_free:
                _log_choice(name, size_gb, f"{profile.backend.upper()} VRAM", vram_free)
                return path, -1
            # Model doesn't fit in VRAM — fall back to CPU for this candidate
            cpu_needed = size_gb * _CPU_RUNTIME_FACTOR + _HEADROOM["cpu"]
            if cpu_needed <= available_ram_gb:
                _log_choice(name, size_gb, "CPU (GPU VRAM too small)", available_ram_gb)
                return path, 0
            _log_skip(name, size_gb, cpu_needed, available_ram_gb)

        else:  # cpu
            headroom = _HEADROOM["cpu"]
            needed = size_gb * _CPU_RUNTIME_FACTOR + headroom
            if needed <= available_ram_gb:
                _log_choice(name, size_gb, "CPU", available_ram_gb)
                return path, 0
            _log_skip(name, size_gb, needed, available_ram_gb)

    return None


def _log_choice(name: str, size_gb: float, where: str, budget_gb: float) -> None:
    print(f"[system-profile] Selected {name} ({size_gb:.1f} GB) → {where} "
          f"({budget_gb:.1f} GB available)")


def _log_skip(name: str, size_gb: float, needed_gb: float, budget_gb: float) -> None:
    print(f"[system-profile] Skipped  {name} ({size_gb:.1f} GB) — "
          f"needs {needed_gb:.1f} GB, {budget_gb:.1f} GB available")


# ---------------------------------------------------------------------------
# Embedding device
# ---------------------------------------------------------------------------

def embedding_device(profile: SystemProfile) -> str:
    """Return the torch device string for sentence-transformers.

    "mps"  — Apple Silicon (Metal Performance Shaders)
    "cuda" — NVIDIA GPU
    "cpu"  — everything else
    """
    if profile.backend == "metal":
        return "mps"
    if profile.backend == "cuda":
        return "cuda"
    return "cpu"


# ---------------------------------------------------------------------------
# CLI — print profile for debugging
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    profile = build_system_profile()
    print(profile)
    result = select_gguf_model(profile)
    if result:
        path, n_gpu = result
        print(f"Best model: {os.path.basename(path)}  n_gpu_layers={n_gpu}")
    else:
        print("No GGUF model found or none fits available memory.")
    print(f"Embedding device: {embedding_device(profile)}")
