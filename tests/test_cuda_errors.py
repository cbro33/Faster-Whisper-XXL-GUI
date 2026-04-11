import pytest
from cuda_errors import (
    detect_cuda_oom,
    detect_cuda_kernel_incompatible,
    detect_cublas_not_supported,
    get_compute_fallback,
    COMPUTE_FALLBACK_ORDER,
)


# ── detect_cuda_oom ──────────────────────────────────────────────────────

class TestDetectCudaOom:
    def test_empty_and_none(self):
        assert detect_cuda_oom("") is False
        assert detect_cuda_oom(None) is False

    def test_plain_cuda_oom(self):
        assert detect_cuda_oom("CUDA out of memory") is True

    def test_cuda_error_oom(self):
        assert detect_cuda_oom("RuntimeError: CUDA error: out of memory") is True

    def test_cublas_alloc_failed(self):
        assert detect_cuda_oom("CUBLAS_STATUS_ALLOC_FAILED when trying to allocate") is True

    def test_cudnn_oom(self):
        assert detect_cuda_oom("cuDNN error: out of memory on device 0") is True

    def test_gpu_oom(self):
        assert detect_cuda_oom("GPU out of memory while running model") is True

    def test_tried_to_allocate(self):
        assert detect_cuda_oom("RuntimeError: out of memory, tried to allocate 512 MiB") is True

    def test_unrelated_error(self):
        assert detect_cuda_oom("RuntimeError: cuBLAS failed with status CUBLAS_STATUS_NOT_SUPPORTED") is False

    def test_case_insensitive(self):
        assert detect_cuda_oom("cuda OUT OF MEMORY") is True


# ── detect_cuda_kernel_incompatible ──────────────────────────────────────

class TestDetectCudaKernelIncompatible:
    def test_empty_and_none(self):
        assert detect_cuda_kernel_incompatible("") is False
        assert detect_cuda_kernel_incompatible(None) is False

    def test_matches(self):
        assert detect_cuda_kernel_incompatible(
            "CUDA error: no kernel image is available for execution on the device"
        ) is True

    def test_case_insensitive(self):
        assert detect_cuda_kernel_incompatible(
            "No Kernel Image Is Available For Execution On The Device"
        ) is True

    def test_unrelated(self):
        assert detect_cuda_kernel_incompatible("CUDA out of memory") is False


# ── detect_cublas_not_supported ──────────────────────────────────────────

class TestDetectCublasNotSupported:
    def test_empty_and_none(self):
        assert detect_cublas_not_supported("") is False
        assert detect_cublas_not_supported(None) is False

    def test_exact_match(self):
        assert detect_cublas_not_supported(
            "RuntimeError: cuBLAS failed with status CUBLAS_STATUS_NOT_SUPPORTED"
        ) is True

    def test_embedded_in_traceback(self):
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"transcribe.py\", line 1719, in encode\n"
            "RuntimeError: cuBLAS failed with status CUBLAS_STATUS_NOT_SUPPORTED\n"
            "[PYI-11288:ERROR] Failed to execute script\n"
        )
        assert detect_cublas_not_supported(stderr) is True

    def test_case_insensitive(self):
        assert detect_cublas_not_supported("cublas_status_not_supported") is True

    def test_does_not_match_alloc_failed(self):
        assert detect_cublas_not_supported("CUBLAS_STATUS_ALLOC_FAILED") is False

    def test_does_not_match_oom(self):
        assert detect_cublas_not_supported("CUDA out of memory") is False


# ── get_compute_fallback ─────────────────────────────────────────────────

class TestGetComputeFallback:
    @pytest.mark.parametrize("current,expected", [
        ("int8_float16", "float16"),
        ("int8_bfloat16", "float16"),
        ("bfloat16", "float16"),
        ("float16", "float32"),
        ("int8", "float32"),
    ])
    def test_known_fallbacks(self, current, expected):
        assert get_compute_fallback(current) == expected

    def test_float32_has_no_fallback(self):
        assert get_compute_fallback("float32") is None

    def test_auto_has_no_fallback(self):
        assert get_compute_fallback("auto") is None

    def test_unknown_has_no_fallback(self):
        assert get_compute_fallback("bfloat8") is None

    def test_fallback_chain_terminates(self):
        """Walk the full fallback chain from every entry and ensure it ends at a type with no further fallback."""
        for start in COMPUTE_FALLBACK_ORDER:
            current = start
            visited = set()
            while current is not None:
                assert current not in visited, f"cycle detected starting from {start}"
                visited.add(current)
                current = get_compute_fallback(current)
