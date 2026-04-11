"""
Pure-function CUDA / cuBLAS error detection and compute-type fallback logic.

Everything here is free of Qt and GUI state so it can be unit-tested trivially.
"""


# ---------------------------------------------------------------------------
# Error detection  (string → bool)
# ---------------------------------------------------------------------------

def detect_cuda_oom(text):
    """Return True if *text* contains a CUDA out-of-memory indicator."""
    if not text:
        return False
    lowered = text.lower()
    if "cuda out of memory" in lowered or "cuda error: out of memory" in lowered:
        return True
    if "cublas_status_alloc_failed" in lowered or ("cudnn" in lowered and "out of memory" in lowered):
        return True
    if "out of memory" in lowered and ("cuda" in lowered or "gpu" in lowered):
        return True
    if "out of memory" in lowered and "tried to allocate" in lowered:
        return True
    return False


def detect_cuda_kernel_incompatible(text):
    """Return True if *text* signals a missing CUDA kernel for the device."""
    if not text:
        return False
    return "no kernel image is available for execution on the device" in text.lower()


def detect_cublas_not_supported(text):
    """Return True if *text* contains a cuBLAS 'not supported' error."""
    if not text:
        return False
    return "cublas_status_not_supported" in text.lower()


# ---------------------------------------------------------------------------
# Compute-type fallback
# ---------------------------------------------------------------------------

COMPUTE_FALLBACK_ORDER = {
    "int8_float16": "float16",
    "int8_bfloat16": "float16",
    "bfloat16": "float16",
    "float16": "float32",
    "int8": "float32",
}


def get_compute_fallback(current_compute_type):
    """Return the next safer compute type, or None if no fallback exists."""
    return COMPUTE_FALLBACK_ORDER.get(current_compute_type)
