"""Tests for managed model serving: the mlx-vlm patch and endpoint parsing."""

from drik.serving import local_port, patch_vision_guard


def test_patch_adds_holo_vision_types():
    src = (
        'class VisionModel:\n'
        '    def check(self):\n'
        '        if self.model_type not in ["qwen3_vl", "qwen3_5", "qwen3_5_moe"]:\n'
        '            raise ValueError(self.model_type)\n'
    )
    out = patch_vision_guard(src)
    assert out is not None
    assert '"qwen3_5_vision"' in out and '"qwen3_5_moe_vision"' in out
    assert '"qwen3_vl"' in out  # original entries preserved
    compact = out.replace(" ", "")
    assert '"qwen3_5_moe","qwen3_5_vision"' in compact


def test_patch_handles_multiline_list():
    src = (
        'if self.model_type not in ["qwen3_vl", "qwen3_5",\n'
        '                           "qwen3_5_moe"]:\n'
        '    raise ValueError\n'
    )
    out = patch_vision_guard(src)
    assert out is not None and '"qwen3_5_vision"' in out


def test_patch_noop_when_already_patched():
    src = 'if self.model_type not in ["qwen3_vl", "qwen3_5_vision"]:'
    assert patch_vision_guard(src) is None


def test_patch_noop_when_guard_missing():
    assert patch_vision_guard("def unrelated(): pass") is None


def test_local_port_for_localhost_endpoints():
    assert local_port("http://localhost:1234/v1") == 1234
    assert local_port("http://127.0.0.1:8080/v1") == 8080
    assert local_port("http://localhost/v1") == 80


def test_local_port_rejects_remote_endpoints():
    assert local_port("https://api.example.com/v1") is None
