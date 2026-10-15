"""Optional local audio measurements with persistent, content-addressed caching.

These features corroborate chart timing; they do not identify instruments,
transcribe music, or decide which sounds deserve playable notes.
"""
from bisect import bisect_left
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

PRODUCER = 'phigros-chart-mcp/register-flux-v1'
DEFAULTS = {'sample_rate': 22050, 'n_fft': 2048, 'hop': 256,
            'peak_percentile': 75.0, 'peak_context_seconds': 1.0,
            'energy_window_seconds': 1.0, 'max_duration_seconds': 600.0}
BANDS = {'low_register': (30, 180), 'middle_register': (180, 1800),
         'high_register': (1800, 10000)}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _settings(settings):
    settings = {} if settings is None else settings
    if not isinstance(settings, dict) or set(settings) - set(DEFAULTS):
        raise ValueError('Unknown audio settings; use sample_rate, n_fft, hop, peak_percentile, peak_context_seconds, energy_window_seconds, max_duration_seconds')
    result = {**DEFAULTS, **settings}
    for key in ('sample_rate', 'n_fft', 'hop'):
        if isinstance(result[key], bool) or not isinstance(result[key], int):
            raise ValueError(f'{key} must be an integer')
    if not 8000 <= result['sample_rate'] <= 48000:
        raise ValueError('sample_rate must be 8000..48000')
    n_fft = result['n_fft']
    if not 256 <= n_fft <= 8192 or n_fft & (n_fft - 1):
        raise ValueError('n_fft must be a power of two from 256 to 8192')
    if not 32 <= result['hop'] <= n_fft:
        raise ValueError('hop must be 32..n_fft')
    for key, low, high in [('peak_percentile', 50, 99), ('peak_context_seconds', .1, 5),
                           ('energy_window_seconds', .1, 30), ('max_duration_seconds', 1, 600)]:
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} must be finite in {low}..{high}')
    return result


def _numpy():
    try:
        import numpy as np
        return np
    except ImportError as error:
        raise ValueError('Optional audio analysis needs numpy; install phigros-chart-mcp[analysis]. Core authoring remains available.') from error


def analyze_samples(samples, settings=None):
    """Measure mono samples at settings.sample_rate, using bounded FFT blocks."""
    np = _numpy()
    settings = _settings(settings)
    sr, n_fft, hop = (settings[k] for k in ('sample_rate', 'n_fft', 'hop'))
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
        raise ValueError('Audio must contain finite mono samples')
    duration = len(samples) / sr
    if duration > settings['max_duration_seconds']:
        raise ValueError('Audio exceeds max_duration_seconds; no partial analysis cached')
    energy = []
    window_size = max(1, round(settings['energy_window_seconds'] * sr))
    for start in range(0, len(samples), window_size):
        segment = samples[start:start + window_size].astype(np.float64)
        energy.append(dict(start_seconds=start / sr, end_seconds=min(start + window_size, len(samples)) / sr,
                           rms=round(float(np.sqrt(np.mean(segment * segment))), 9)))
    padded = samples if len(samples) >= n_fft else np.pad(samples, (0, n_fft - len(samples)))
    frames = np.lib.stride_tricks.sliding_window_view(padded, n_fft)[::hop]
    frequencies = np.fft.rfftfreq(n_fft, 1 / sr)
    masks = {name: (frequencies >= low) & (frequencies < high) for name, (low, high) in BANDS.items()}
    flux = {name: np.zeros(len(frames), dtype=np.float64) for name in BANDS}
    window = np.hanning(n_fft)
    previous = None
    for start in range(0, len(frames), 256):
        spectra = np.abs(np.fft.rfft(frames[start:start + 256] * window, axis=1))
        before = spectra[:1] if previous is None else previous[None, :]
        positive = np.maximum(np.diff(spectra, axis=0, prepend=before), 0)
        for name, mask in masks.items():
            flux[name][start:start + len(spectra)] = positive[:, mask].sum(axis=1)
        previous = spectra[-1].copy()
    context = max(2, round(settings['peak_context_seconds'] * sr / hop))
    peaks = {}
    for name, values in flux.items():
        rows = []
        for index in range(2, len(values) - 2):
            value = values[index]
            if value <= 0 or value != np.max(values[index - 2:index + 3]):
                continue
            surrounding = values[max(0, index - context):index + context + 1]
            if value > np.percentile(surrounding, settings['peak_percentile']):
                time = (index * hop + n_fft / 2) / sr
                if time < duration:
                    rows.append(dict(time_seconds=round(time, 9), positive_flux=round(float(value), 9)))
        peaks[name] = rows
    return dict(duration_seconds=duration, sample_count=len(samples), register_peaks=peaks,
                energy_windows=energy, time_basis='decoded audio seconds; FFT frame centers',
                limitations=['Positive spectral flux is onset evidence, not instrument or beat transcription',
                             'Band overlap, quiet attacks and sustained sounds can mislead onset matching',
                             'First/last FFT margins have reduced onset coverage; preserve source evidence'])


def _cache_dir(cache_dir):
    configured = cache_dir or os.environ.get('PHIGROS_CHART_MCP_CACHE_DIR')
    root = Path(configured).expanduser() if configured else Path.home() / '.cache' / 'phigros-chart-mcp'
    return root.resolve() / 'audio-v1'


def _cache_path(audio_ref, cache_dir):
    if not isinstance(audio_ref, str) or not re.fullmatch(r'audio://[0-9a-f]{64}', audio_ref):
        raise ValueError('Expected an audio:// SHA-256 reference')
    return _cache_dir(cache_dir) / (audio_ref.removeprefix('audio://') + '.json')


def load_audio_analysis(audio_ref, cache_dir=None):
    path = _cache_path(audio_ref, cache_dir)
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Audio cache unavailable; analyze the original local audio again')
    record = json.loads(path.read_text())
    checksum = record.pop('record_sha256', None)
    if checksum != _hash(record) or record.get('audio_ref') != audio_ref or record.get('producer') != PRODUCER:
        raise ValueError('Audio cache checksum/version mismatch; remove the invalid cache record and analyze again')
    if 'audio://' + _hash(record['key']) != audio_ref:
        raise ValueError('Audio cache key mismatch')
    return record


def analyze_audio(path, settings=None, cache_dir=None):
    """Decode a local audio file once; return only a compact cached reference."""
    settings = _settings(settings)
    np = _numpy()
    source = Path(path).expanduser()
    if not source.is_absolute() or not source.is_file() or source.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('Audio requires an absolute existing file no larger than 256 MiB')
    if source.suffix.lower() not in ('.ogg', '.mp3', '.wav', '.flac', '.m4a', '.aac', '.opus', '.aif', '.aiff'):
        raise ValueError('Use a local supported audio file; playlists/URLs are not accepted')
    decoder = shutil.which('ffmpeg')
    if decoder is None:
        raise ValueError('Optional audio analysis requires ffmpeg on PATH')
    version = subprocess.run([decoder, '-version'], capture_output=True, text=True, timeout=10, check=True).stdout.splitlines()[0]
    sha = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    key = dict(source_sha256=sha.hexdigest(), settings=settings, producer=PRODUCER,
               numpy_version=np.__version__, decoder_version=version)
    audio_ref = 'audio://' + _hash(key)
    cache_path = _cache_path(audio_ref, cache_dir)
    cached = cache_path.exists()
    if cached:
        record = load_audio_analysis(audio_ref, cache_dir)
    else:
        command = [decoder, '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe', '-i', str(source),
                   '-map', '0:a:0', '-vn', '-ac', '1', '-ar', str(settings['sample_rate']),
                   '-t', str(settings['max_duration_seconds'] + 1), '-f', 'f32le', 'pipe:1']
        try:
            decoded = subprocess.run(command, capture_output=True, timeout=120, check=True)
        except subprocess.CalledProcessError as error:
            raise ValueError('Audio decoding failed: ' + error.stderr.decode(errors='replace')[-1000:]) from error
        except subprocess.TimeoutExpired as error:
            raise ValueError('Audio decoding exceeded 120 seconds; no partial analysis cached') from error
        measurements = analyze_samples(np.frombuffer(decoded.stdout, dtype='<f4'), settings)
        record = dict(audio_ref=audio_ref, producer=PRODUCER, key=key, source_sha256=key['source_sha256'],
                      settings=settings, **measurements)
        payload = _json({**record, 'record_sha256': _hash(record)}) + '\n'
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=cache_path.parent, delete=False) as temp:
                temp_name = temp.name
                temp.write(payload)
            os.replace(temp_name, cache_path)
        finally:
            if temp_name and Path(temp_name).exists():
                Path(temp_name).unlink()
    return dict(audio_ref=audio_ref, source_sha256=key['source_sha256'], duration_seconds=record['duration_seconds'],
                cache_hit=cached, settings=settings, producer=PRODUCER,
                peak_counts={name: len(rows) for name, rows in record['register_peaks'].items()},
                energy_window_count=len(record['energy_windows']), cache_path=str(cache_path),
                limitations=record['limitations'])


def match_register_peaks(record, audio_seconds, tolerance_ms=25):
    """Return at most one nearest measured peak per band, never inferred notes."""
    if not math.isfinite(audio_seconds) or not math.isfinite(tolerance_ms) or not 0 <= tolerance_ms <= 250:
        raise ValueError('Finite audio time and tolerance 0..250 ms required')
    result = {}
    for name, rows in record['register_peaks'].items():
        # bisect key avoids rebuilding a full timestamp array for every beat.
        index = bisect_left(rows, audio_seconds, key=lambda row: row['time_seconds'])
        candidates = rows[max(0, index - 1):index + 1]
        if candidates:
            peak = min(candidates, key=lambda row: abs(row['time_seconds'] - audio_seconds))
            distance = abs(peak['time_seconds'] - audio_seconds) * 1000
            if distance <= tolerance_ms:
                result[name] = dict(nearest_peak_s=peak['time_seconds'], distance_ms=round(distance, 6),
                                    positive_flux=peak['positive_flux'])
    return result
