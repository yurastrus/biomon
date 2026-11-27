"""
/pam/verification/verify: when the queue runs out (404) or a segment fails to
load, the previous segment's audio must stop.

Before 2026-11-27 the "no segments left" message appeared while the last
segment kept playing in the hidden panel.

pytest does not run JS, so this pins the template source as a regression guard.

Run:
    venv/Scripts/python -m pytest tests/test_pam_verify_stop_audio.py -v
"""
import re
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parent.parent
            / 'app/pam/templates/pam_verification_interface.html')


def test_stop_audio_pauses_and_drops_source():
    src = TEMPLATE.read_text(encoding='utf-8')
    body = re.search(r"function stopAudio\(\) \{([\s\S]*?)\n    \}", src).group(1)
    assert 'audioPlayer.pause()' in body
    assert 'audioPlayer.loop = false' in body
    assert "removeAttribute('src')" in body


def test_load_failure_branch_stops_audio_before_empty_message():
    src = TEMPLATE.read_text(encoding='utf-8')
    catch = src[src.index('} catch (xhr) {'):]
    catch = catch[:catch.index('} finally {')]
    assert catch.index('stopAudio()') < catch.index('noSegmentMessage.show()')
