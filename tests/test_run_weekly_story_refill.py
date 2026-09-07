from unittest.mock import MagicMock, patch

from pipeline.config import TRACKS

import run_weekly_story_refill


def test_refill_skips_non_story_content_types_without_calling_generate_stories():
    # Real production bug (caused every scheduled run to fail from
    # 2026-09-05 to 2026-09-07): this loop used to call generate_stories/
    # write_stories -- which only know the Script schema -- for EVERY
    # track, including math_explainers (content_type="canvas", needs
    # MathScript's completely different schema) and manifestation
    # (content_type="song", never reads a pre-written queue at all). That
    # wrote Script-shaped JSON into both queues; math_explainers' own
    # MathScript.validate rejected it outright on every run, and
    # manifestation silently accumulated 14 dead files it never read.
    non_story_keys = {k for k, t in TRACKS.items() if t.content_type != "story"}
    assert non_story_keys, "this test needs at least one non-story track to be meaningful"

    with patch("run_weekly_story_refill.GEMINI_API_KEY", "fake-key"), \
         patch("run_weekly_story_refill.generate_stories", return_value=[]) as mock_generate_stories, \
         patch("run_weekly_story_refill.generate_next_episode", return_value=MagicMock()), \
         patch("run_weekly_story_refill.write_stories", return_value=[]):
        run_weekly_story_refill.run()

    called_keys = {c.args[0].key for c in mock_generate_stories.call_args_list}
    assert non_story_keys.isdisjoint(called_keys)
