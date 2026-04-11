import pytest
from gpu_utils import assess_gpu_capabilities, get_recommended_settings


# ── assess_gpu_capabilities ──────────────────────────────────────────────

class TestAssessGpuCapabilities:
    def _gpu(self, name, memory_gb=8.0, vendor="nvidia"):
        return {"gpu_name": name, "gpu_memory_gb": memory_gb, "gpu_vendor": vendor}

    def test_rtx_50_series_high_end(self):
        result = assess_gpu_capabilities(self._gpu("NVIDIA GeForce RTX 5060 Laptop GPU"))
        assert result["tier"] == "high-end"
        assert result["recommended_device"] == "cuda"
        assert result["recommended_compute"] == "float16"

    def test_rtx_40_series_high_end(self):
        result = assess_gpu_capabilities(self._gpu("NVIDIA GeForce RTX 4090"))
        assert result["tier"] == "high-end"
        assert result["recommended_compute"] == "float16"

    def test_rtx_30_series_mid_high(self):
        result = assess_gpu_capabilities(self._gpu("NVIDIA GeForce RTX 3060", memory_gb=12.0))
        assert result["tier"] == "mid-high"
        assert result["recommended_compute"] == "int8_float16"

    def test_rtx_30_series_low_vram_uses_medium_model(self):
        result = assess_gpu_capabilities(self._gpu("NVIDIA GeForce RTX 3050", memory_gb=4.0))
        assert result["recommended_model"] == "medium"

    def test_rtx_30_series_high_vram_uses_large(self):
        result = assess_gpu_capabilities(self._gpu("NVIDIA GeForce RTX 3060", memory_gb=12.0))
        assert result["recommended_model"] == "large-v2"

    def test_rtx_20_series(self):
        result = assess_gpu_capabilities(self._gpu("NVIDIA GeForce RTX 2070"))
        assert result["tier"] == "mid-high"
        assert result["recommended_device"] == "cuda"

    def test_gtx_legacy(self):
        result = assess_gpu_capabilities(self._gpu("NVIDIA GeForce GTX 1060"))
        assert result["tier"] == "legacy"
        assert result["recommended_compute"] == "int8"

    def test_intel_arc(self):
        result = assess_gpu_capabilities(self._gpu("Intel Arc A770", vendor="intel"))
        assert result["recommended_device"] == "cpu"
        assert result["tier"] == "mid-range"

    def test_intel_iris_xe(self):
        result = assess_gpu_capabilities(self._gpu("Intel Iris Xe Graphics", memory_gb=2.0, vendor="intel"))
        assert result["tier"] == "integrated-good"

    def test_intel_uhd(self):
        result = assess_gpu_capabilities(self._gpu("Intel UHD Graphics 630", memory_gb=1.0, vendor="intel"))
        assert result["tier"] == "integrated-basic"

    def test_amd_rx_7000(self):
        result = assess_gpu_capabilities(self._gpu("AMD Radeon RX 7000 series", vendor="amd"))
        assert result["recommended_device"] == "cpu"
        assert result["tier"] == "high-end"

    def test_unknown_gpu(self):
        result = assess_gpu_capabilities(self._gpu("SomeNewGPU", vendor="unknown"))
        assert result["tier"] == "unknown"
        assert result["recommended_device"] == "cpu"


# ── get_recommended_settings ─────────────────────────────────────────────

class TestGetRecommendedSettings:
    def _hw(self, has_cuda=True, vram=8.0, ram=16.0):
        return {
            "has_cuda": has_cuda,
            "gpu_memory_gb": vram,
            "ram_gb": ram,
        }

    def test_8gb_vram_gets_float16(self):
        """8 GB VRAM GPUs should get float16 (the issue that prompted the fix)."""
        r = get_recommended_settings(self._hw(vram=8.0))
        assert r["compute_type"] == "float16"

    def test_borderline_vram_7_9_gets_float16(self):
        """GPUs reporting slightly under 8 GB (e.g. 7.9) should still get float16."""
        r = get_recommended_settings(self._hw(vram=7.9))
        assert r["compute_type"] == "float16"

    def test_7_5_threshold_edge(self):
        r = get_recommended_settings(self._hw(vram=7.5))
        assert r["compute_type"] == "float16"

    def test_below_7_5_gets_int8_float16(self):
        r = get_recommended_settings(self._hw(vram=6.0))
        assert r["compute_type"] == "int8_float16"

    def test_4gb_vram_gets_int8_float16(self):
        r = get_recommended_settings(self._hw(vram=4.0))
        assert r["compute_type"] == "int8_float16"

    def test_low_vram_gets_int8(self):
        r = get_recommended_settings(self._hw(vram=2.0))
        assert r["compute_type"] == "int8"

    def test_cpu_always_int8(self):
        r = get_recommended_settings(self._hw(has_cuda=False, vram=0, ram=32.0))
        assert r["compute_type"] == "int8"

    def test_device_cuda_when_sufficient_vram(self):
        r = get_recommended_settings(self._hw(vram=8.0))
        assert r["device"] == "cuda"

    def test_device_cpu_when_no_cuda(self):
        r = get_recommended_settings(self._hw(has_cuda=False, vram=0))
        assert r["device"] == "cpu"

    def test_device_cpu_when_low_vram(self):
        r = get_recommended_settings(self._hw(vram=2.0))
        assert r["device"] == "cpu"

    def test_model_large_for_high_vram(self):
        r = get_recommended_settings(self._hw(vram=12.0))
        assert r["model"] == "large-v2"

    def test_model_medium_for_mid_vram(self):
        r = get_recommended_settings(self._hw(vram=4.0))
        assert r["model"] == "medium"

    def test_model_medium_for_cpu_with_ram(self):
        r = get_recommended_settings(self._hw(has_cuda=False, vram=0, ram=16.0))
        assert r["model"] == "medium"

    def test_model_base_for_low_ram_cpu(self):
        r = get_recommended_settings(self._hw(has_cuda=False, vram=0, ram=4.0))
        assert r["model"] == "base"

    def test_beam_size_high_vram(self):
        r = get_recommended_settings(self._hw(vram=12.0))
        assert r["beam_size"] == 5

    def test_beam_size_low_vram(self):
        r = get_recommended_settings(self._hw(vram=2.0, ram=4.0))
        assert r["beam_size"] == 3

    def test_vad_pyannote_for_cuda(self):
        r = get_recommended_settings(self._hw(vram=8.0))
        assert r["vad_method"] == "pyannote_v3"

    def test_vad_silero_for_cpu(self):
        r = get_recommended_settings(self._hw(has_cuda=False, vram=0))
        assert r["vad_method"] == "silero_v4_fw"
