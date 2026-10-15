import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

from phigros_chart_mcp.audio_analysis import (analyze_audio, analyze_samples,
                                             load_audio_analysis, match_register_peaks)


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'optional numpy unavailable')
class AudioMeasurements(unittest.TestCase):
    def test_silence_has_no_attacks(self):
        import numpy as np
        measured = analyze_samples(np.zeros(22050, dtype=np.float32))
        self.assertEqual(measured['duration_seconds'], 1)
        self.assertTrue(all(not rows for rows in measured['register_peaks'].values()))
        self.assertEqual(measured['energy_windows'][0]['rms'], 0)

    def test_known_tone_bursts_near_physical_onsets(self):
        import numpy as np
        sr = 22050
        samples = np.zeros(sr * 4, dtype=np.float32)
        for at in (.5, 3.1):
            start, length = round(at * sr), round(.12 * sr)
            samples[start:start + length] = np.sin(np.arange(length) * (2 * np.pi * 100 / sr)) * .5
        measured = analyze_samples(samples)
        for at in (.5, 3.1):
            self.assertTrue(any(abs(p['time_seconds'] - at) < .06 for p in measured['register_peaks']['low_register']))
        self.assertGreater(measured['energy_windows'][0]['rms'], 0)
        self.assertEqual(measured['energy_windows'][1]['rms'], 0)

    def test_nearest_peak_tolerance_and_absolute_audio_time(self):
        record = {'register_peaks': {'low_register': [{'time_seconds': 1.125, 'positive_flux': 20},
                                                       {'time_seconds': 2.0, 'positive_flux': 30}]}}
        matched = match_register_peaks(record, 1.12, 10)
        self.assertEqual(matched['low_register']['nearest_peak_s'], 1.125)
        self.assertAlmostEqual(matched['low_register']['distance_ms'], 5)
        self.assertEqual(match_register_peaks(record, .5, 25), {})

    def test_invalid_measurements_and_settings_fail(self):
        import numpy as np
        for settings in ({'n_fft': 1023}, {'sample_rate': True}, {'hop': 0},
                         {'unknown': 1}, False, {'max_duration_seconds': 1000}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                analyze_samples(np.zeros(22050), settings)
        with self.assertRaises(ValueError):
            analyze_samples(np.array([float('nan')]))
        with self.assertRaises(ValueError):
            analyze_samples(np.zeros(22051), {'max_duration_seconds': 1})

    @unittest.skipUnless(shutil.which('ffmpeg'), 'optional ffmpeg unavailable')
    def test_persistent_cache_hit_settings_invalidation_and_checksum(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.wav'
            pcm = (np.sin(np.arange(22050) * (2 * np.pi * 220 / 22050)) * 5000).astype('<i2')
            with wave.open(str(path), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(22050)
                audio.writeframes(pcm.tobytes())
            first = analyze_audio(str(path), cache_dir=directory)
            self.assertFalse(first['cache_hit'])
            with patch('phigros_chart_mcp.audio_analysis.analyze_samples', side_effect=AssertionError('cache must avoid FFT')):
                second = analyze_audio(str(path), cache_dir=directory)
            self.assertTrue(second['cache_hit'])
            self.assertEqual(first['audio_ref'], second['audio_ref'])
            changed = analyze_audio(str(path), {'hop': 512}, directory)
            self.assertNotEqual(first['audio_ref'], changed['audio_ref'])
            self.assertFalse(changed['cache_hit'])
            loaded = load_audio_analysis(first['audio_ref'], directory)
            self.assertAlmostEqual(loaded['duration_seconds'], 1, places=4)
            cached_path = Path(first['cache_path'])
            altered = json.loads(cached_path.read_text())
            altered['duration_seconds'] = 500
            cached_path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                load_audio_analysis(first['audio_ref'], directory)


if __name__ == '__main__':
    unittest.main()
