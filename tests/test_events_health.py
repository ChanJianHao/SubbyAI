from subbyai.core.events import CaptionSegment, PrivacyTier, TranslationState
from subbyai.core.health import HealthMonitor, HealthState


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_translation_attaches_without_mutating_original():
    seg = CaptionSegment(text="こんにちは", language="ja")
    assert seg.display_translation is None
    translated = seg.with_translation("Hello", "en", "builtin", PrivacyTier.ON_DEVICE)
    assert translated.display_translation == "Hello"
    assert translated.text == "こんにちは"  # original speech is preserved
    assert seg.translation is None  # frozen: original object untouched
    assert translated.translation_tier is PrivacyTier.ON_DEVICE


def test_failed_translation_hides_translation_but_keeps_caption():
    seg = CaptionSegment(text="hola").with_translation_state(TranslationState.FAILED)
    assert seg.display_translation is None
    assert seg.text == "hola"


def test_uncertainty_flags_low_confidence_and_non_speech():
    assert CaptionSegment(text="x", confidence=0.9, speech_probability=0.9).is_uncertain is False
    assert CaptionSegment(text="x", confidence=0.3).is_uncertain is True
    assert CaptionSegment(text="x", speech_probability=0.2).is_uncertain is True


def test_segment_ids_are_unique():
    a, b = CaptionSegment(text="a"), CaptionSegment(text="b")
    assert a.id != b.id


def test_privacy_tier_labels():
    assert PrivacyTier.ON_DEVICE.label == "On this device"
    assert PrivacyTier.LOCAL_NETWORK.label == "Local network"
    assert PrivacyTier.CLOUD.label == "Cloud"


def test_health_distinguishes_silence_from_music_from_speech():
    clock = FakeClock()
    monitor = HealthMonitor(clock=clock)
    monitor.set_state(HealthState.LISTENING)

    monitor.note_speech()
    assert monitor.report().state is HealthState.LISTENING

    # Sound continues, but no speech: "music", not "broken".
    clock.advance(13)
    monitor.note_audio(0.05)
    assert monitor.report().state is HealthState.SOUND_NO_SPEECH

    # Now the sound stops entirely.
    clock.advance(11)
    monitor.note_audio(0.0)
    report = monitor.report()
    assert report.state is HealthState.SILENT
    assert "can't hear anything" in report.detail


def test_health_reports_delay():
    clock = FakeClock()
    monitor = HealthMonitor(clock=clock)
    monitor.set_state(HealthState.LISTENING)
    monitor.note_speech()
    monitor.note_lag(5.0)
    report = monitor.report()
    assert report.state is HealthState.DELAYED
    assert "5 seconds behind" in report.detail


def test_health_lifecycle_states_pass_through():
    monitor = HealthMonitor(clock=FakeClock())
    monitor.set_state(HealthState.DOWNLOADING, "Downloading…")
    assert monitor.report().state is HealthState.DOWNLOADING
    monitor.set_state(HealthState.ERROR, "boom")
    assert monitor.report().detail == "boom"
    assert HealthState.ERROR.is_running is False
    assert HealthState.LISTENING.is_running is True


def test_device_name_is_reported_for_error_messages():
    monitor = HealthMonitor(clock=FakeClock())
    monitor.set_state(HealthState.LISTENING)
    monitor.set_device("Speakers (Realtek)")
    monitor.note_speech()
    assert monitor.report().device_name == "Speakers (Realtek)"
