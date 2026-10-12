"""Accuracy contracts: exact samples, chronology, context and revision lifecycle."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import numpy as np
import pytest

from subbyai.asr.engine import Transcript, WordSpan
from subbyai.audio.buffer import CaptureBuffer
from subbyai.audio.processing import StreamingResampler, downmix, to_mono_16k
from subbyai.audio.segmenter import CLOSE_SPEECH_BOUNDARY, SegmenterConfig, SpeechSegmenter
from subbyai.core.events import CaptionSegment, RecognitionState, TranslationState
from subbyai.core.pipeline_trace import PipelineTrace
from subbyai.core.settings import SessionConfig, Settings
from subbyai.pipeline.captioner import AudioWork, Captioner
from subbyai.pipeline.context import RecognitionContext
from subbyai.pipeline.hallucinations import HallucinationFilter
from subbyai.pipeline.reconciliation import Reconciler
from subbyai.pipeline.sentences import can_join, join_sources, sentence_finished


def tone(seconds, rate=16000, frequency=440):
    return (0.1 * np.sin(2 * np.pi * frequency * np.arange(int(seconds * rate)) / rate)).astype(
        np.float32
    )


def test_resampler_rejects_above_nyquist_without_damaging_speech_band():
    high = to_mono_16k(tone(1, 48000, 10000), 48000)[100:-100]
    speech = to_mono_16k(tone(1, 48000, 2000), 48000)[100:-100]
    assert np.sqrt(np.mean(high**2)) < 0.0001
    assert np.sqrt(np.mean(speech**2)) == pytest.approx(0.1 / np.sqrt(2), abs=0.001)


def test_one_minute_fractional_resampling_has_no_cumulative_drift():
    rate = 44100
    resampler = StreamingResampler(rate)
    count = rate * 60 + 17
    for offset in range(0, count, 1024):
        resampler.push(np.zeros(min(1024, count - offset), np.float32))
    resampler.flush()
    assert abs(resampler.output_samples - count * 16000 / rate) <= 1


def test_downmix_retains_opposite_polarity_channels_and_ordinary_stereo():
    speech = tone(1)
    assert np.array_equal(downmix(np.stack([speech, -speech], axis=1)), speech)
    stereo = np.stack([speech, speech * 0.5], axis=1)
    assert np.allclose(downmix(stereo), speech * 0.75)


def test_capture_buffer_retains_format_and_offsets_through_overflow():
    buffer = CaptureBuffer(0.1)
    source = np.arange(1024 * 2, dtype=np.float32).reshape(1024, 2)
    first = buffer.write(source, 16000)
    second = buffer.write(source + 5, 16000)
    source[:] = 0  # caller-owned capture storage may be reused immediately
    surviving = buffer.read()
    assert surviving is second
    assert surviving.start == 1024 / 16000
    assert surviving.rate == 16000 and surviving.samples.shape == (1024, 2)
    assert first.samples[1, 0] == 2
    assert buffer.dropped_frames == 2048


def test_format_switch_and_driver_gap_create_new_epochs():
    buffer = CaptureBuffer()
    a = buffer.write(np.zeros((441, 2), np.float32), 44100)
    b = buffer.write(np.zeros(480, np.float32), 48000, gap_seconds=0.2)
    assert b.epoch > a.epoch
    assert b.start == pytest.approx(0.21)
    assert buffer.read() is a and buffer.read() is b


def test_flush_preserves_subframe_tail_samples():
    source = tone(1.02)[:16017]
    segmenter = SpeechSegmenter()
    assert not segmenter.push(source, 16000)
    flushed = segmenter.flush()
    assert np.array_equal(flushed, source)
    assert segmenter._emitted_time == (0, len(source) / 16000)


def test_forced_split_repair_contains_exact_original_plus_future_audio():
    source = tone(5)
    segmenter = SpeechSegmenter(SegmenterConfig(max_segment_seconds=2))
    chunks = segmenter.push(source, 16000)
    assert segmenter.completed_final[:2] == [False, True]
    assert segmenter.completed_times[:2] == [(0, 2), (0, 2.6)]
    assert np.array_equal(chunks[0], source[:32000])
    assert np.array_equal(chunks[1], source[:41600])
    for audio, (start, duration) in zip(chunks, segmenter.completed_times, strict=True):
        begin = round(start * 16000)
        assert np.array_equal(audio, source[begin : begin + round(duration * 16000)])


def test_duration_limit_prefers_retained_pause_without_losing_future_speech():
    source = np.concatenate([tone(3.6), np.zeros(5120, np.float32), tone(3)])
    segmenter = SpeechSegmenter(SegmenterConfig(max_segment_seconds=6, close_after_silence=0.5))
    chunks = segmenter.push(source, 16000)
    times = list(segmenter.completed_times)
    assert segmenter.completed_reasons[0] == CLOSE_SPEECH_BOUNDARY
    assert segmenter.completed_final[0]
    assert times[0] == pytest.approx((0, 3.92))
    tail = segmenter.flush()
    if tail is not None:
        chunks.append(tail)
        times.append(segmenter._emitted_time)
    covered = np.zeros(len(source), bool)
    for audio, (start, _duration) in zip(chunks, times, strict=True):
        begin = round(start * 16000)
        assert np.array_equal(audio, source[begin : begin + len(audio)])
        covered[begin : begin + len(audio)] = True
    assert covered[np.abs(source) > 0.001].all()


def test_brief_pause_is_retained_for_duration_cut_without_hard_closing_speech():
    source = np.concatenate([tone(3.6), np.zeros(2240, np.float32), tone(3)])
    segmenter = SpeechSegmenter(SegmenterConfig(max_segment_seconds=6, close_after_silence=0.5))
    # A 140 ms breath is not a hard phrase boundary.
    assert not segmenter.push(source[:4 * 16000], 16000)
    chunks = segmenter.push(source[4 * 16000:], 16000)
    assert segmenter.completed_reasons[0] == CLOSE_SPEECH_BOUNDARY
    assert segmenter.completed_times[0] == pytest.approx((0, 3.74))
    assert np.array_equal(chunks[0], source[:len(chunks[0])])
    tail = segmenter.flush()
    start, duration = segmenter._emitted_time
    begin = round(start * 16000)
    assert np.array_equal(tail, source[begin:begin + round(duration * 16000)])
    assert begin < len(chunks[0])
    assert begin + len(tail) == len(source)


def test_lookahead_revision_precedes_following_silence_closed_window():
    source = np.concatenate([tone(2), np.zeros(16000, np.float32)])
    segmenter = SpeechSegmenter(SegmenterConfig(max_segment_seconds=2, close_after_silence=0.35))
    segmenter.push(source, 16000)
    assert segmenter.completed_times[:2] == [(0, 2), (0, 2.6)]
    assert segmenter.completed_final[:2] == [False, True]
    assert segmenter.completed_reasons[1] == "boundary_revision"


def test_eof_finalizes_prior_revision_before_deferred_following_window():
    source = np.concatenate([tone(2), np.zeros(6400, np.float32)])
    segmenter = SpeechSegmenter(SegmenterConfig(max_segment_seconds=2, close_after_silence=0.35))
    chunks = segmenter.push(source, 16000)
    assert len(chunks) == 1 and segmenter.completed_final == [False]
    segmenter.flush()
    audio, start, duration = segmenter.flushed_revision
    assert start == 0 and duration == 2.4
    assert np.array_equal(audio, source)
    following, timing, _reason, final = segmenter.flushed_following[0]
    assert final and timing[0] > start
    begin = round(timing[0] * 16000)
    assert np.array_equal(following, source[begin:begin + len(following)])


def test_quiet_speech_and_short_answer_survive_energy_fallback():
    segmenter = SpeechSegmenter()
    source = np.concatenate([tone(0.12) * 0.04, np.zeros(16000, np.float32)])
    assert segmenter.push(source, 16000)


def test_padding_retains_soft_phoneme_before_onset():
    source = np.concatenate([tone(0.2) * 0.004, tone(0.6), np.zeros(16000, np.float32)])
    segmenter = SpeechSegmenter()
    chunks = segmenter.push(source, 16000)
    assert np.array_equal(chunks[0][:3200], source[:3200])


def test_confident_ordinary_dialogue_is_not_blacklisted():
    gate = HallucinationFilter(["hello", "thank you", "I'll be back soon"])
    for text in ("Hello.", "Thank you.", "I'll be back soon."):
        assert not gate.should_drop(text, 0.99, 0.99)
        assert gate.should_drop(text, 0.1, 0.2)


def test_timestamp_reconciliation_removes_boundary_name_and_preserves_real_repetition():
    reconciler = Reconciler()
    first = (WordSpan(" Please", 0, 0.3), WordSpan(" ask", 0.3, 0.6), WordSpan(" Nakamura", 0.6, 1))
    assert reconciler.accept("Please ask Nakamura", 0, 1.2, first) == "Please ask Nakamura"
    second = (
        WordSpan(" Nakamura", 0, 0.4),
        WordSpan(" now.", 0.4, 0.8),
        WordSpan(" Now!", 0.85, 1.1),
    )
    assert reconciler.accept("Nakamura now. Now!", 0.6, 1.2, second) == "now. Now!"


def test_multiword_overlap_with_drifting_word_times_does_not_duplicate_clause():
    reconciler = Reconciler()
    reconciler.accept("Please ask not", 0, 5, (
        WordSpan(" Please", 0, 0.5), WordSpan(" ask", 3.2, 3.5), WordSpan(" not", 3.5, 4),
    ))
    assert reconciler.accept("ask not what your country", 3.2, 6, (
        WordSpan(" ask", 0.8, 1), WordSpan(" not", 1, 1.2),
        WordSpan(" what", 1.3, 1.5), WordSpan(" your", 1.5, 1.7),
        WordSpan(" country", 1.7, 2),
    )) == "what your country"


def test_covered_tail_text_alignment_handles_drift_without_losing_a_real_repeat():
    reconciler = Reconciler()
    words = (WordSpan(" Ask", 0, .2), WordSpan(" what", .2, .4),
             WordSpan(" you", .4, .6), WordSpan(" can", .6, .8), WordSpan(" do.", .8, 1))
    text = "Ask what you can do."
    reconciler.accept(text, 0, 1.6, words)
    assert reconciler.accept(text, .8, .9, words) == ""
    # A separate repetition starts after confirmed speech and adds new audio.
    assert reconciler.accept(text, 1.2, 1.8, words) == text


def test_new_repeat_beyond_previous_audio_survives_prefix_text_alignment():
    reconciler = Reconciler()
    reconciler.accept("Please ask not", 0, 2, (
        WordSpan(" Please", 0, .3), WordSpan(" ask", .3, .5), WordSpan(" not", .5, .7),
    ))
    assert reconciler.accept("ask not again", 1.5, 2, (
        WordSpan(" ask", .6, .8), WordSpan(" not", .8, 1), WordSpan(" again", 1, 1.3),
    )) == "ask not again"


def test_covered_tail_does_not_repeat_an_earlier_clause_with_shifted_timestamps():
    reconciler = Reconciler()
    reconciler.accept("Ask what your country can do for you. Ask what you can do.", 0, 6.6)
    assert reconciler.accept("can do for you", 5.8, .92, (
        WordSpan(" can", .1, .2), WordSpan(" do", .2, .3),
        WordSpan(" for", .3, .5), WordSpan(" you", .5, .7),
    )) == ""


def test_cjk_word_alignment_retains_punctuation_and_new_characters():
    reconciler = Reconciler()
    first = (WordSpan("明日", 0, 0.5), WordSpan("会いましょう。", 0.5, 1))
    reconciler.accept("明日会いましょう。", 0, 1.2, first)
    second = (WordSpan("会いましょう。", 0, 0.5), WordSpan("午後三時です。", 0.6, 1.3))
    assert reconciler.accept("会いましょう。午後三時です。", 0.5, 1.5, second) == "午後三時です。"


def test_preview_cannot_advance_finalized_alignment():
    reconciler = Reconciler()
    assert reconciler.preview("Please ask Nakamu", 0, 2) == "Please ask Nakamu"
    assert reconciler.accept("Please ask Nakamura.", 0, 2.6) == "Please ask Nakamura."


def test_api_word_timestamps_without_leading_spaces_do_not_join_words():
    words = (WordSpan("Hello", 0, 0.3), WordSpan("world", 0.3, 0.6), WordSpan(".", 0.6, 0.6))
    assert Reconciler().accept("Hello world.", 0, 1, words) == "Hello world."
    korean = (WordSpan("안녕하세요", 0, 0.3), WordSpan("여러분", 0.3, 0.6))
    assert Reconciler().accept("안녕하세요 여러분", 0, 1, korean) == "안녕하세요 여러분"


def test_fast_adjacent_repetitions_are_not_timestamp_duplicates():
    reconciler = Reconciler()
    reconciler.accept("Go", 0, 0.2, (WordSpan(" Go", 0, 0.1),))
    words = (WordSpan(" Go", 0, 0.05), WordSpan(" go!", 0.05, 0.15))
    assert reconciler.accept("Go go!", 0.05, 0.2, words) == "go!"


def test_collapsed_timestamp_repetition_is_removed_but_timed_repetition_survives():
    words = (
        WordSpan(" Come", 0, 0.2),
        WordSpan(" here", 0.2, 0.4),
        WordSpan(" right", 0.4, 0.6),
        WordSpan(" now.", 0.6, 0.8),
    )
    ghost = tuple(WordSpan(w.text, 1, 1) for w in words)
    real = tuple(WordSpan(w.text, w.start + 1, w.end + 1) for w in words)
    assert Reconciler().accept("", 0, 2, (*words, *ghost)) == "Come here right now."
    assert (
        Reconciler().accept("", 0, 2, (*words, *real))
        == "Come here right now. Come here right now."
    )


def test_context_uses_long_language_evidence_and_resets_on_gaps():
    calls = []

    class Detector:
        def detect_language(self, audio):
            calls.append(len(audio))
            return "ko", 0.99

    context = RecognitionContext()
    for i in range(3):
        hint = context.prepare(Detector(), AudioWork(tone(1), i, 1, 0))
    assert calls == [48000] and hint == "ko"
    context.remember(
        "이름은 민수입니다.", Transcript("", language="ko", confidence=0.9, speech_probability=0.99)
    )
    assert context.text
    context.prepare(Detector(), AudioWork(tone(0.2), 30, 0.2, 0, epoch=1))
    assert context.text == "" and context.language is None


def test_translation_sentence_rules_cover_cjk_and_continuity():
    assert sentence_finished("ありがとうございます。")
    assert sentence_finished("真的吗？")  # noqa: RUF001 - native punctuation
    assert not sentence_finished("If she")
    assert not sentence_finished("Ask not.", "en")
    assert sentence_finished("Thank you.", "en")
    first = CaptionSegment("明日", language="ja", audio_duration=1)
    second = CaptionSegment("行きます。", language="ja", audio_start=1, audio_duration=1)
    assert can_join(first, second) and join_sources([first, second]) == "明日行きます。"
    assert not can_join(first, CaptionSegment("Different scene.", language="en", audio_start=20))
    korean = [
        CaptionSegment("내 이름은", language="ko"),
        CaptionSegment("민수입니다.", language="ko"),
    ]
    assert join_sources(korean) == "내 이름은 민수입니다."
    fragments = [
        CaptionSegment("ASK NOT!", language="en"),
        CaptionSegment("What your country can do for you.", language="en"),
    ]
    assert join_sources(fragments) == "Ask not what your country can do for you."
    assert join_sources([CaptionSegment("NATO IS NOT.", language="en")]).startswith("NATO")
    assert "ASK" in join_sources(fragments, "ASK")


def test_asr_prompt_budget_does_not_inject_whisper_control_tokens():
    from types import SimpleNamespace

    from subbyai.asr.engine import recognition_prompt

    class Tokenizer:
        def encode(self, text, *, add_special_tokens=True):
            assert add_special_tokens is False
            return SimpleNamespace(ids=[ord(c) for c in text])

    model = SimpleNamespace(hf_tokenizer=Tokenizer(), max_length=448)
    prompt = recognition_prompt(model, "Nakamura", "漢" * 600)
    assert len(prompt) == 223
    assert "Nakamura" in "".join(chr(c) for c in prompt)


def test_language_evidence_is_not_concatenated_across_a_dialogue_gap():
    class Detector:
        def detect_language(self, audio):
            return "ja", 0.99

    context = RecognitionContext()
    context.prepare(Detector(), AudioWork(tone(4), 0, 4, 0))
    assert context.language == "ja"
    hint = context.prepare(Detector(), AudioWork(tone(2), 8, 2, 0))
    assert hint is None and len(context._audio) == 32000


@pytest.mark.parametrize("content", [False, True])
def test_trace_requires_explicit_content_opt_in_and_preserves_exact_audio(tmp_path, content):
    trace = PipelineTrace(tmp_path, content=content)
    audio = tone(0.2)
    trace.record(
        "capture",
        audio=audio,
        start=0,
        content={"text": "private sentence"},
        arbitrary_string="private sentence",
    )
    trace.close()
    events = (trace.directory / "events.jsonl").read_text(encoding="utf-8")
    records = [json.loads(line) for line in events.splitlines()]
    assert ("private sentence" in events) is content
    files = list(trace.directory.glob("*.npy"))
    assert bool(files) is content
    if content:
        assert np.array_equal(np.load(files[0], allow_pickle=False), audio)
    assert records[-1]["dropped"] == 0


def test_boundary_caption_is_revised_in_place_and_only_final_text_is_translated(qt_app):
    from subbyai.core.events import PrivacyTier
    from subbyai.translation.base import TranslationResult

    class Engine:
        calls = 0

        def transcribe(self, audio, **kwargs):
            self.calls += 1
            return Transcript(
                "Please ask Nakamu" if self.calls == 1 else "Please ask Nakamura.",
                confidence=0.99,
                speech_probability=0.99,
            )

    class Translator:
        def __init__(self):
            self.inputs = []

        def translate(self, text, *args):
            self.inputs.append(text)
            return TranslationResult("translated", "fake", "fake", PrivacyTier.ON_DEVICE)

    engine, translator = Engine(), Translator()
    captioner = Captioner(lambda c: engine, speech_detector_factory=None)
    config = SessionConfig.from_settings(Settings())
    config = replace(config, target_language="en")
    captioner._session_start = time.monotonic()
    captioner._asr_queue.put(AudioWork(tone(2), 0, 2, time.monotonic(), final=False))
    captioner._asr_queue.put(AudioWork(tone(2.6), 0, 2.6, time.monotonic(), revision=True))
    captioner._segmentation_done.set()
    ready, updated = [], []
    captioner.segment_ready.connect(ready.append)
    captioner.segment_updated.connect(updated.append)
    asr = threading.Thread(target=captioner._guard_recognize_loop, args=(engine, config, None))
    translation = threading.Thread(target=captioner._translate_loop, args=(translator, config))
    asr.start()
    translation.start()
    asr.join(5)
    translation.join(5)
    qt_app.processEvents()
    assert not asr.is_alive() and not translation.is_alive()
    assert len(ready) == 1 and ready[0].recognition_state is RecognitionState.PROVISIONAL
    assert updated[0].id == ready[0].id and updated[0].text == "Please ask Nakamura."
    assert updated[0].recognition_state is RecognitionState.FINAL
    assert translator.inputs == ["Please ask Nakamura."]
    assert updated[-1].translation_state is TranslationState.DONE


@pytest.mark.parametrize("failure", ["empty", "exception"])
def test_rejected_boundary_revision_is_marked_failed_without_logging_content(
    qt_app, caplog, failure
):
    class Engine:
        calls = 0

        def transcribe(self, audio, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return Transcript("Tentative name", confidence=0.99, speech_probability=0.99)
            if failure == "exception":
                raise RuntimeError("private audio transcript from a provider")
            return Transcript("")

    engine = Engine()
    captioner = Captioner(lambda c: engine, speech_detector_factory=None)
    config = SessionConfig.from_settings(Settings())
    captioner._session_start = time.monotonic()
    captioner._asr_queue.put(AudioWork(tone(2), 0, 2, time.monotonic(), final=False))
    captioner._asr_queue.put(AudioWork(tone(2.6), 0, 2.6, time.monotonic(), revision=True))
    captioner._segmentation_done.set()
    ready, updated = [], []
    captioner.segment_ready.connect(ready.append)
    captioner.segment_updated.connect(updated.append)
    captioner._guard_recognize_loop(engine, config, None)
    assert len(ready) == len(updated) == 1
    assert updated[0].id == ready[0].id
    assert updated[0].recognition_state is RecognitionState.FAILED
    assert captioner._translate_queue.empty()
    assert "private audio transcript" not in caplog.text
