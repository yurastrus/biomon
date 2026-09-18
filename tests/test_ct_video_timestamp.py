"""Tests for reading the burned-in timestamp of camera-trap videos.

The module under test turns a strip of pixels back into a capture time, so the
tests come in two layers:

* the reasoning layer -- parsing, format inference, plausibility, voting -- is
  tested directly, because that is where a wrong answer silently corrupts the
  database;
* the pixel layer is tested against synthetic frames drawn here, so the suite
  needs no sample videos and still exercises bar detection, glyph segmentation,
  calibration and reading end to end.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.camera_traps import video_timestamp as vt


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic frames
# ─────────────────────────────────────────────────────────────────────────────

def _draw_frame(text, width=640, height=240, bar_height=28,
                dark_background=True, bar_at_bottom=True, seed=0, pitch=14):
    """Draw a frame with a noisy 'scene' and an info bar carrying ``text``.

    Characters are placed one by one at a fixed pitch rather than written as a
    run. That is what a camera's overlay actually does -- it is a monospaced
    bitmap font with clear gaps -- and drawing it any other way would make the
    fixture, not the code, decide whether neighbouring glyphs can be told apart.
    """
    rng = np.random.default_rng(seed)
    # A photographed scene: noise everywhere, so no row of it is ever flat.
    scene = rng.integers(40, 210, size=(height, width)).astype(np.uint8)
    img = Image.fromarray(scene, mode='L')

    draw = ImageDraw.Draw(img)
    if bar_at_bottom:
        y0, y1 = height - bar_height, height
    else:
        y0, y1 = 0, bar_height

    background = 20 if dark_background else 240
    ink = 250 if dark_background else 10
    draw.rectangle([0, y0, width, y1 - 1], fill=background)

    x = 10
    for char in text:
        if char != ' ':
            draw.text((x, y0 + 6), char, fill=ink)
        x += pitch
    return img


def _clip(start, count=8, step=1, **kwargs):
    """A sequence of frames whose bar ticks one second per frame."""
    frames = []
    for i in range(count):
        stamp = start + timedelta(seconds=i * step)
        text = f"1000 24C {stamp:%Y/%m/%d %H:%M:%S} 0042"
        frames.append(_draw_frame(text, seed=i, **kwargs))
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Rendering the expected sequence
# ─────────────────────────────────────────────────────────────────────────────

def test_render_expected_drops_spaces_because_they_leave_no_ink():
    stamp = datetime(2026, 7, 20, 14, 2, 33)
    assert vt.render_expected(stamp, 'ymd', '24') == '2026/07/2014:02:33'


@pytest.mark.parametrize('order,expected', [
    ('ymd', '2026/07/20'),
    ('dmy', '20/07/2026'),
    ('mdy', '07/20/2026'),
    ('ydm', '2026/20/07'),
])
def test_render_expected_follows_the_chosen_date_order(order, expected):
    stamp = datetime(2026, 7, 20, 14, 2, 33)
    assert vt.render_expected(stamp, order, '24').startswith(expected)


def test_render_expected_marks_the_half_of_the_day_in_12_hour_format():
    assert vt.render_expected(datetime(2026, 7, 20, 14, 2, 33), 'ymd', '12').endswith('02:02:33PM')
    assert vt.render_expected(datetime(2026, 7, 20, 9, 2, 33), 'ymd', '12').endswith('09:02:33AM')
    # Midnight and noon are the two that catch naive implementations.
    assert vt.render_expected(datetime(2026, 7, 20, 0, 5, 0), 'ymd', '12').endswith('12:05:00AM')
    assert vt.render_expected(datetime(2026, 7, 20, 12, 5, 0), 'ymd', '12').endswith('12:05:00PM')


def test_render_expected_rejects_an_unknown_order():
    with pytest.raises(ValueError):
        vt.render_expected(datetime(2026, 7, 20), 'yyy', '24')


# ─────────────────────────────────────────────────────────────────────────────
# Parsing a transcribed bar
# ─────────────────────────────────────────────────────────────────────────────

def test_parses_a_timestamp_that_runs_into_the_counter():
    # The real shape of a transcribed bar: no spaces survive, so the date, the
    # time and the clip counter form one unbroken run of digits.
    text = '1000?0?3200890?2026/07/2014:02:330006'
    assert vt.parse_timestamp(text, 'ymd') == datetime(2026, 7, 20, 14, 2, 33)


@pytest.mark.parametrize('separator', ['/', '-', '.', ':', ''])
def test_separator_style_does_not_matter(separator):
    text = f'2026{separator}07{separator}2014:02:33'
    assert vt.parse_timestamp(text, 'ymd') == datetime(2026, 7, 20, 14, 2, 33)


def test_the_same_digits_read_differently_under_different_orders():
    # 2026/07/08 and 08/07/2026 are the same digits in a different order; the
    # operator's choice is what decides, which is why the page asks.
    assert vt.parse_timestamp('2026/07/0814:02:33', 'ymd') == datetime(2026, 7, 8, 14, 2, 33)
    assert vt.parse_timestamp('08/07/202614:02:33', 'dmy') == datetime(2026, 7, 8, 14, 2, 33)
    assert vt.parse_timestamp('08/07/202614:02:33', 'mdy') == datetime(2026, 8, 7, 14, 2, 33)


def test_two_digit_year_is_read_as_this_century():
    assert vt.parse_timestamp('26/07/2014:02:33', 'ymd') == datetime(2026, 7, 20, 14, 2, 33)


def test_twelve_hour_marker_moves_the_afternoon():
    assert vt.parse_timestamp('2026/07/2002:02:33PM', 'ymd', '12') == \
        datetime(2026, 7, 20, 14, 2, 33)
    assert vt.parse_timestamp('2026/07/2002:02:33AM', 'ymd', '12') == \
        datetime(2026, 7, 20, 2, 2, 33)


def test_an_impossible_date_is_not_offered():
    # 32 is not a day and 13 is not a month, whatever order is claimed.
    assert vt.candidate_timestamps('2026/13/3214:02:33', 'ymd') == []


def test_a_bar_without_a_timestamp_raises():
    with pytest.raises(vt.TimestampError):
        vt.parse_timestamp('1000?0?32C89F', 'ymd')


def test_parse_rejects_an_unknown_order():
    with pytest.raises(ValueError):
        vt.candidate_timestamps('2026/07/2014:02:33', 'nope')


# ─────────────────────────────────────────────────────────────────────────────
# Plausibility
# ─────────────────────────────────────────────────────────────────────────────

def test_a_reset_camera_clock_is_not_plausible():
    # The classic failure: a battery change resets the clock to 2000-01-01.
    assert not vt.is_plausible(datetime(2000, 1, 1, 12, 0, 0))


def test_a_clock_running_far_ahead_is_not_plausible():
    assert not vt.is_plausible(datetime.now() + timedelta(days=3))


def test_a_clock_a_few_hours_ahead_is_tolerated():
    # A time-zone slip is not a corrupt reading; it stays usable.
    assert vt.is_plausible(datetime.now() + timedelta(hours=6))


def test_an_implausible_timestamp_is_never_returned_by_the_parser():
    with pytest.raises(vt.TimestampError):
        vt.parse_timestamp('2001/07/2014:02:33', 'ymd', year_width=4)


def test_leaving_the_year_width_open_lets_a_ghost_reading_through():
    """Why the year width is pinned by calibration instead of being inferred.

    Read with a two-digit year, the very same digits that spell an implausible
    2001 also spell a perfectly plausible 2020-01-07 20:14:02. The ghost even
    advances one second per frame, exactly like the real reading, so no amount of
    cross-frame voting can expose it. Only the operator's declared format can.
    """
    ghost = vt.candidate_timestamps('2001/07/2014:02:33', 'ymd')
    assert datetime(2020, 1, 7, 20, 14, 2) in ghost
    assert vt.candidate_timestamps('2001/07/2014:02:33', 'ymd', year_width=4) == []


# ─────────────────────────────────────────────────────────────────────────────
# Inferring the date order over a folder
# ─────────────────────────────────────────────────────────────────────────────

def test_a_day_above_twelve_settles_the_order():
    # 2026/07/20: the 20 cannot be a month, so mdy and ydm are out.
    assert vt.infer_date_order([('2026', '07', '20')]) == ['ymd']


def test_an_ambiguous_folder_reports_every_survivor_instead_of_guessing():
    # Nothing here exceeds twelve, so the digits alone cannot decide.
    survivors = vt.infer_date_order([('2026', '07', '08')])
    assert set(survivors) == {'ymd', 'ydm'}


def test_one_decisive_clip_settles_a_whole_folder():
    samples = [('2026', '07', '08'), ('2026', '07', '20'), ('2026', '08', '03')]
    assert vt.infer_date_order(samples) == ['ymd']


def test_an_inconsistent_folder_leaves_nothing_standing():
    # 13 as a month and 32 as a day cannot both be accommodated by any order.
    assert vt.infer_date_order([('2026', '13', '32')]) == []


# ─────────────────────────────────────────────────────────────────────────────
# The counter cross-check
# ─────────────────────────────────────────────────────────────────────────────

def test_counter_confirms_the_file_it_came_from():
    # The bar's trailing digits run into the seconds ahead of them, so the
    # comparison is by suffix.
    assert vt.counter_matches('330006', 'DSCF0006.AVI') is True


def test_counter_from_another_file_is_rejected():
    assert vt.counter_matches('330006', 'DSCF0142.AVI') is False


def test_a_missing_counter_is_reported_as_unknown_not_as_failure():
    assert vt.counter_matches(None, 'DSCF0006.AVI') is None
    assert vt.counter_matches('0006', 'clip.avi') is None


# ─────────────────────────────────────────────────────────────────────────────
# Bar detection
# ─────────────────────────────────────────────────────────────────────────────

def test_the_bar_is_found_at_the_bottom():
    frames = [vt.to_gray(f) for f in _clip(datetime(2026, 7, 20, 14, 2, 33))]
    y0, y1, dark = vt.find_info_bar(frames)
    assert y0 >= 200 and y1 == 240
    assert dark is True


def test_the_bar_is_found_at_the_top_too():
    frames = [vt.to_gray(f) for f in
              _clip(datetime(2026, 7, 20, 14, 2, 33), bar_at_bottom=False)]
    y0, y1, _dark = vt.find_info_bar(frames)
    assert y0 == 0 and y1 <= 40


def test_a_light_bar_is_recognised_as_light():
    frames = [vt.to_gray(f) for f in
              _clip(datetime(2026, 7, 20, 14, 2, 33), dark_background=False)]
    _y0, _y1, dark = vt.find_info_bar(frames)
    assert dark is False


def test_a_frame_without_a_bar_raises_rather_than_inventing_one():
    rng = np.random.default_rng(1)
    noise = [rng.integers(0, 255, size=(240, 640)).astype(np.float32)
             for _ in range(4)]
    with pytest.raises(vt.TimestampError):
        vt.find_info_bar(noise)


def test_no_frames_at_all_raises():
    with pytest.raises(vt.TimestampError):
        vt.find_info_bar([])


# ─────────────────────────────────────────────────────────────────────────────
# Calibration and reading
# ─────────────────────────────────────────────────────────────────────────────

def test_calibration_learns_the_digits_from_one_known_timestamp():
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vt.calibrate_profile(_clip(start, count=10), start, 'ymd')

    # Ten seconds of ticking expose every digit of the units place, and the
    # rest of the timestamp supplies the others.
    assert set('0123456789') <= set(profile.templates)
    assert profile.date_order == 'ymd'
    assert profile.dark_background is True


def test_a_calibrated_profile_reads_a_clip_it_has_never_seen():
    trained = datetime(2026, 7, 20, 14, 2, 33)
    profile = vt.calibrate_profile(_clip(trained, count=10), trained, 'ymd')

    other = datetime(2026, 9, 8, 6, 35, 25)
    reading = vt.read_clip(_clip(other, count=10), profile)

    assert reading.start == other
    assert reading.agreement == 1.0
    assert reading.confident
    assert reading.per_frame[-1] == other + timedelta(seconds=9)


def test_calibration_refuses_a_timestamp_that_does_not_match_the_frames():
    start = datetime(2026, 7, 20, 14, 2, 33)
    frames = _clip(start, count=6)
    # Wrong by a day: the operator mistyped, and that must not be papered over.
    with pytest.raises(vt.TimestampError):
        vt.calibrate_profile(frames, start + timedelta(days=1), 'ymd')


def test_calibration_refuses_the_wrong_date_order():
    # The camera printed 2026/07/20; claiming day-first cannot be reconciled,
    # because 2026 is not a day.
    start = datetime(2026, 7, 20, 14, 2, 33)
    with pytest.raises(vt.TimestampError):
        vt.calibrate_profile(_clip(start, count=6), start, 'dmy')


def test_calibration_needs_more_than_one_frame():
    start = datetime(2026, 7, 20, 14, 2, 33)
    with pytest.raises(vt.TimestampError):
        vt.calibrate_profile(_clip(start, count=1), start, 'ymd')


def test_a_clip_keeps_its_own_per_frame_times_when_the_clock_ticks_unevenly():
    """An overlay clock out of step with the frame rate must not be 'corrected'.

    Real clips skip a second between two samples because the camera's clock and
    the frame rate are independent. The reader has to keep what each frame
    actually shows, or every frame after the skip is recorded one second wrong.
    """
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vt.calibrate_profile(_clip(start, count=10), start, 'ymd')

    stamps = [start, start + timedelta(seconds=1)] + [
        start + timedelta(seconds=i) for i in range(3, 11)
    ]
    frames = [
        _draw_frame(f"1000 24C {s:%Y/%m/%d %H:%M:%S} 0042", seed=i)
        for i, s in enumerate(stamps)
    ]

    reading = vt.read_clip(frames, profile)
    assert reading.per_frame == stamps
    assert reading.agreement == 1.0


def test_one_unreadable_frame_does_not_decide_the_clip():
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vt.calibrate_profile(_clip(start, count=10), start, 'ymd')

    frames = _clip(start, count=10)
    # Wipe one frame's bar: it now carries nothing, and the rest must carry on.
    blank = np.asarray(frames[4].convert('L'), dtype=np.float32)
    blank[210:, :] = 20.0
    frames[4] = blank

    reading = vt.read_clip(frames, profile)
    assert reading.start == start
    assert reading.per_frame[4] == start + timedelta(seconds=4)
    assert 0.6 <= reading.agreement < 1.0


def test_a_clip_no_frame_of_which_can_be_read_raises():
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vt.calibrate_profile(_clip(start, count=10), start, 'ymd')

    frames = []
    for i in range(4):
        arr = np.asarray(_draw_frame('1000 24C', seed=i).convert('L'),
                         dtype=np.float32)
        frames.append(arr)

    with pytest.raises(vt.TimestampError):
        vt.read_clip(frames, profile)


def test_reading_an_empty_clip_raises():
    profile = vt.CameraProfile()
    with pytest.raises(vt.TimestampError):
        vt.read_clip([], profile)


# ─────────────────────────────────────────────────────────────────────────────
# Profile storage
# ─────────────────────────────────────────────────────────────────────────────

def test_a_profile_survives_the_round_trip_through_the_database():
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vt.calibrate_profile(_clip(start, count=10), start, 'ymd',
                                   label='test camera')

    restored = vt.CameraProfile.from_json(profile.to_json())

    assert restored.label == 'test camera'
    assert restored.date_order == profile.date_order
    assert set(restored.templates) == set(profile.templates)
    for char, template in profile.templates.items():
        assert np.allclose(restored.templates[char], template)

    # And it still reads, which is the only thing the round trip is for.
    other = datetime(2026, 9, 8, 6, 35, 25)
    assert vt.read_clip(_clip(other, count=8), restored).start == other


def test_an_unknown_glyph_reads_as_a_question_mark_rather_than_a_guess():
    profile = vt.CameraProfile(templates={})
    glyph = np.zeros((vt.GLYPH_H, vt.GLYPH_W), dtype=np.float32)
    char, _score = profile.match(glyph)
    assert char == '?'


# ─────────────────────────────────────────────────────────────────────────────
# Showing the operator what was read
# ─────────────────────────────────────────────────────────────────────────────

def test_the_bar_can_be_cropped_out_as_a_png_for_review():
    frame = _clip(datetime(2026, 7, 20, 14, 2, 33), count=1)[0]
    png = vt.crop_bar_png(frame)
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    assert Image.open(__import__('io').BytesIO(png)).width == 640


# ─────────────────────────────────────────────────────────────────────────────
# The title-card layout
# ─────────────────────────────────────────────────────────────────────────────

def _draw_card(lines, width=640, height=360, dark_background=True,
               pitch=14, seed=0):
    """Draw a title card: a solid frame with a few centred lines of text.

    This is the second layout found in the field. Cuddeback clips show such a
    card for a fraction of a second and then run footage with no overlay at all,
    so the card is the only record of when the clip was taken.
    """
    background = 10 if dark_background else 245
    ink = 250 if dark_background else 5
    img = Image.new('L', (width, height), color=background)
    draw = ImageDraw.Draw(img)

    y = 60
    for line in lines:
        x = 60
        for char in line:
            if char != ' ':
                draw.text((x, y), char, fill=ink)
            x += pitch
        y += 50
    return img


def _cuddeback_card(stamp, code='0309', **kwargs):
    """A card in the shape the real camera prints: unpadded, 12-hour, no seconds."""
    hour = stamp.hour % 12 or 12
    half = 'AM' if stamp.hour < 12 else 'PM'
    return _draw_card([
        'CAMERA',
        f'{stamp.month}/{stamp.day}/{stamp.year}  {hour}:{stamp.minute:02d} {half}',
        code,
    ], **kwargs)


def test_a_title_card_is_told_apart_from_footage():
    card = vt.to_gray(_cuddeback_card(datetime(2024, 6, 20, 11, 13)))
    footage = vt.to_gray(_clip(datetime(2026, 7, 20, 14, 2, 33), count=1)[0])

    assert vt.looks_like_title_card(card) is True
    assert vt.looks_like_title_card(footage) is False


def test_a_card_is_split_into_its_separate_lines():
    # Splitting matters: run together, the location code below the timestamp
    # could be read as part of it.
    card = vt.to_gray(_cuddeback_card(datetime(2024, 6, 20, 11, 13)))
    assert len(vt.card_lines(card)) == 3


def test_unpadded_fields_and_missing_seconds_are_read():
    # "7/9/2024 3:51PM" pads nothing and records no seconds at all.
    found = vt.loose_candidates('7/9/20243:51PM', 'mdy', '12')
    assert found == [datetime(2024, 7, 9, 15, 51, 0)]


def test_the_card_reader_ignores_a_trailing_location_code():
    found = vt.loose_candidates('6/20/202411:13AM 0309', 'mdy', '12')
    assert datetime(2024, 6, 20, 11, 13) in found


def test_calibration_settles_padding_and_seconds_without_being_told():
    stamps = [datetime(2024, 6, 20, 11, 13), datetime(2024, 7, 9, 15, 51)]
    cards = [_cuddeback_card(s) for s in stamps]

    profile = vt.calibrate_card_profile(cards, stamps, 'mdy', '12')

    assert profile.layout == vt.LAYOUT_CARD
    assert profile.padded is False
    assert profile.has_seconds is False


def test_a_card_camera_gives_every_frame_a_time_from_the_one_card():
    stamps = [datetime(2024, 6, 20, 11, 13), datetime(2024, 7, 9, 15, 51)]
    profile = vt.calibrate_card_profile(
        [_cuddeback_card(s) for s in stamps], stamps, 'mdy', '12')

    # A clip: the card, then footage carrying no overlay whatsoever.
    unseen = datetime(2024, 7, 23, 21, 10)
    frames = [_cuddeback_card(unseen)] + [
        _draw_frame('', seed=i + 1) for i in range(5)
    ]

    reading = vt.read_clip(frames, profile)

    assert reading.start == unseen
    assert reading.card_index == 0
    assert reading.per_frame[-1] == unseen + timedelta(seconds=5)


def test_the_card_frame_is_flagged_so_it_is_not_stored_as_a_photo():
    # The card is a black splash with no wildlife on it. Its index has to reach
    # the caller, or every clip gains one junk photo.
    stamps = [datetime(2024, 6, 20, 11, 13), datetime(2024, 7, 9, 15, 51)]
    profile = vt.calibrate_card_profile(
        [_cuddeback_card(s) for s in stamps], stamps, 'mdy', '12')

    frames = [_cuddeback_card(datetime(2024, 7, 23, 21, 10))] + \
        [_draw_frame('', seed=i + 1) for i in range(3)]
    assert vt.read_clip(frames, profile).card_index == 0


def test_a_clip_with_no_readable_card_raises():
    stamps = [datetime(2024, 6, 20, 11, 13), datetime(2024, 7, 9, 15, 51)]
    profile = vt.calibrate_card_profile(
        [_cuddeback_card(s) for s in stamps], stamps, 'mdy', '12')

    with pytest.raises(vt.TimestampError):
        vt.read_clip([_draw_frame('', seed=i) for i in range(4)], profile)


def test_calibration_needs_a_timestamp_for_every_card():
    with pytest.raises(ValueError):
        vt.calibrate_card_profile([_cuddeback_card(datetime(2024, 6, 20, 11, 13))],
                                  [], 'mdy', '12')


def test_a_card_profile_survives_the_round_trip_through_the_database():
    stamps = [datetime(2024, 6, 20, 11, 13), datetime(2024, 7, 9, 15, 51)]
    profile = vt.calibrate_card_profile(
        [_cuddeback_card(s) for s in stamps], stamps, 'mdy', '12')

    restored = vt.CameraProfile.from_json(profile.to_json())
    assert restored.layout == vt.LAYOUT_CARD
    assert restored.padded is False
    assert restored.has_seconds is False

    unseen = datetime(2024, 7, 23, 21, 10)
    frames = [_cuddeback_card(unseen)] + [_draw_frame('', seed=1)]
    assert vt.read_clip(frames, restored).start == unseen
