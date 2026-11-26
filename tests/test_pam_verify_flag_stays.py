"""
/pam/verification/verify: the flag button marks the segment and STAYS on it.

Before 2026-11-26 the flag handler called loadNextSegment() right after saving
the flag, so the verifier could not also give their own answer. Now it stays,
shows a message, and locks the flag button for that segment only.

pytest does not run JS, so this pins the handler's source as a regression guard.

Run:
    venv/Scripts/python -m pytest tests/test_pam_verify_flag_stays.py -v
"""
import re
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parent.parent
            / 'app/pam/templates/pam_verification_interface.html')


def _flag_handler_source():
    src = TEMPLATE.read_text(encoding='utf-8')
    start = src.index("$('#verify-flag').on('click'")
    end = src.index('backButtons.on(', start)
    return src, src[start:end]


def test_flag_does_not_advance_to_next_segment():
    _, handler = _flag_handler_source()
    assert 'loadNextSegment' not in handler
    assert 'skippedSegments' not in handler


def test_flag_locks_button_for_flagged_segment():
    src, handler = _flag_handler_source()
    assert 'flaggedSegments.add(segment.segment_id)' in handler
    # The lock is computed per current segment, so the next segment gets a live button.
    assert re.search(r"function updateFlagButton\(\)[\s\S]{0,300}"
                     r"flaggedSegments\.has\(currentSegment\.segment_id\)", src)
    # setBusy must not blindly re-enable the flag button.
    set_busy = src[src.index('function setBusy'):src.index('function reopenPreviousSubmission')]
    assert "'#verify-flag'" not in set_busy
    assert 'updateFlagButton()' in set_busy
    # Declared before the first loadNextSegment() call (no TDZ error on page load).
    assert src.index('const flaggedSegments') < src.index('loadNextSegment();')
