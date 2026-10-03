"""Diagnostic fixtures reject malformed input without reading private audio."""

import wave

import pytest

from subbyai.self_check import _fixture_audio


@pytest.mark.parametrize("rate,channels,width", [(8000, 1, 2), (16000, 2, 2), (16000, 1, 1)])
def test_fixture_rejects_unsupported_audio(tmp_path, rate, channels, width):
    path = tmp_path / "fixture.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        stream.writeframes(bytes(rate * channels * width))
    with pytest.raises(ValueError, match="16 kHz mono 16-bit"):
        _fixture_audio(path)


def test_fixture_rejects_truncated_frames(tmp_path):
    path = tmp_path / "fixture.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(bytes(32000))
    path.write_bytes(path.read_bytes()[:-100])
    with pytest.raises(ValueError, match="truncated"):
        _fixture_audio(path)
