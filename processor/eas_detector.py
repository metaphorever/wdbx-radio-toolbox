"""
EAS (Emergency Alert System) tone detection.

WDBX config: eas_freq_1_hz=1050, eas_freq_2_hz=853
Detection approach: short-time FFT on ~0.5s windows, check for energy
at both target frequencies within a 30Hz tolerance window.
A "detection" requires both tones present simultaneously for ≥0.5s.

Performance: ~5-10s per 2hr file on CPU using librosa.
Returns list of {start_sec, end_sec} dicts.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import librosa
    _LIBROSA_AVAILABLE = True
except ImportError:
    _LIBROSA_AVAILABLE = False
    logger.warning("librosa/numpy not available — EAS detection disabled")

FREQ_TOLERANCE_HZ = 30      # Hz either side of target
MIN_DETECTION_SEC = 0.5     # both tones must overlap for this long


def detect_eas(
    audio_path: Path,
    freq1: int = 1050,
    freq2: int = 853,
) -> list[dict]:
    """
    Scan audio file for simultaneous presence of freq1 and freq2.
    Returns list of {"start_sec": float, "end_sec": float}.
    Returns [] if librosa unavailable or file unreadable.
    """
    if not _LIBROSA_AVAILABLE:
        return []
    try:
        return _detect(audio_path, freq1, freq2)
    except Exception as e:
        logger.warning("EAS detection failed for %s: %s", audio_path.name, e)
        return []


def _detect(path: Path, freq1: int, freq2: int) -> list[dict]:
    # Load mono, 22050 Hz — librosa default. EAS tones are well below Nyquist.
    y, sr = librosa.load(str(path), sr=22050, mono=True)

    # Short-time FFT: 2048 samples (~93ms per frame at 22050Hz)
    hop = 512
    n_fft = 2048
    D = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    times = librosa.frames_to_time(np.arange(D.shape[1]), sr=sr, hop_length=hop)

    def freq_bin_mask(target_hz: int) -> "np.ndarray":
        """Boolean mask of frequency bins within tolerance of target."""
        return np.abs(freqs - target_hz) <= FREQ_TOLERANCE_HZ

    mask1 = freq_bin_mask(freq1)
    mask2 = freq_bin_mask(freq2)

    # Energy in each tone's band per frame, normalized by total frame energy
    total_energy = D.sum(axis=0) + 1e-9
    energy1 = D[mask1, :].sum(axis=0) / total_energy
    energy2 = D[mask2, :].sum(axis=0) / total_energy

    # Threshold: tone energy must be >5% of total frame energy to count
    ENERGY_THRESHOLD = 0.05
    both_present = (energy1 > ENERGY_THRESHOLD) & (energy2 > ENERGY_THRESHOLD)

    # Find contiguous runs of both_present=True
    detections = []
    in_detection = False
    start_sec = 0.0
    frame_dur = times[1] - times[0] if len(times) > 1 else hop / sr

    for i, present in enumerate(both_present):
        if present and not in_detection:
            start_sec = float(times[i])
            in_detection = True
        elif not present and in_detection:
            end_sec = float(times[i])
            if end_sec - start_sec >= MIN_DETECTION_SEC:
                detections.append({"start_sec": start_sec, "end_sec": end_sec})
            in_detection = False

    if in_detection:
        end_sec = float(times[-1])
        if end_sec - start_sec >= MIN_DETECTION_SEC:
            detections.append({"start_sec": start_sec, "end_sec": end_sec})

    return detections
