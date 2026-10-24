"""The SDK builder can consume persistent simulator exports without staging sources."""
from pathlib import Path
import subprocess
import sys


def test_exported_artifact_context_selects_prebuilt_stage(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'build.py'
    result = subprocess.run([sys.executable, str(script), '--print-command',
        '--gem5-artifacts', str(tmp_path), '--builder', 'test-builder'],
        text=True, capture_output=True, check=True)
    assert 'GEM5_STAGE=gem5-prebuilt' in result.stdout
    assert 'gem5-artifacts=' + str(tmp_path) in result.stdout
    assert '--builder test-builder' in result.stdout


def test_conflicting_prebuilt_sources_are_rejected(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'build.py'
    result = subprocess.run([sys.executable, str(script), '--print-command',
        '--gem5-artifacts', str(tmp_path), '--prebuilt-gem5', str(tmp_path)],
        text=True, capture_output=True)
    assert result.returncode == 2
    assert 'choose either' in result.stderr
