import subprocess

import pytest

from pipeline.canvas_video import render_piece_video, synthesize_narration_lines

# A minimal synthetic piece for testing the render pipeline's mechanics
# (window.__LINE_TIMES__ injection, __seek-driven frame capture, ffmpeg mux)
# without paying the render cost of a real multi-minute piece like
# math_pieces/gauss_trick. Draws a color that changes with window.__LINE_TIMES__
# so a real bug in the injection would be visible, not just "didn't crash".
MINIMAL_PIECE_HTML = """<title>Test Piece</title>
<style>
  html, body { margin: 0; padding: 0; }
  #stage { position: relative; width: 100%; height: 100vh; min-height: 200px; }
  canvas#c { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
</style>
<div id="stage"><canvas id="c"></canvas></div>
<script src="CANVAS_LIB_PATH"></script>
<script>
(function () {
  const CP = window.CanvasPiece;
  const canvas = document.getElementById('c');
  const stage = document.getElementById('stage');
  const { ctx } = CP.setupCanvas(canvas, stage);
  const LT = window.__LINE_TIMES__ || [{ start: 0, end: 1 }];
  const total = LT[LT.length - 1].end;

  function draw(t) {
    const w = stage.clientWidth, h = stage.clientHeight;
    ctx.clearRect(0, 0, w, h);
    const p = CP.clamp01(t / total);
    ctx.fillStyle = `rgb(${Math.round(p * 255)}, 100, 100)`;
    ctx.fillRect(0, 0, w, h);
  }

  CP.createTimeline({ draw, total });
})();
</script>
"""


def probe_video(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-show_entries", "stream=width,height,codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def probe_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def probe_resolution(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def test_synthesize_narration_lines_computes_real_offsets(tmp_path):
    lines = ["Hello world.", "This is a second line of narration."]
    narration_path, line_times = synthesize_narration_lines(lines, tmp_path / "audio", voice="en-US-GuyNeural")

    assert narration_path.exists()
    assert len(line_times) == 2
    assert line_times[0].start == 0.0
    assert line_times[0].end > 0.0
    # second line starts exactly where the first ends -- real cumulative offsets
    assert line_times[1].start == line_times[0].end
    assert line_times[1].end > line_times[1].start

    total = probe_duration(narration_path)
    assert total == pytest.approx(line_times[1].end, abs=0.5)


def test_render_piece_video_produces_synced_video(tmp_path):
    import shutil
    from pipeline.config import ROOT_DIR
    canvas_lib = tmp_path / "canvas_lib.js"
    shutil.copy(ROOT_DIR / "pipeline" / "canvas_lib" / "helpers.js", canvas_lib)

    piece_html = tmp_path / "piece.html"
    piece_html.write_text(MINIMAL_PIECE_HTML.replace("CANVAS_LIB_PATH", "canvas_lib.js"))

    lines = ["Hello there.", "A quick second beat."]
    narration_path, line_times = synthesize_narration_lines(lines, tmp_path / "audio", voice="en-US-GuyNeural")

    out_path = tmp_path / "out.mp4"
    render_piece_video(piece_html, line_times, narration_path, out_path, resolution=(320, 568), fps=10)

    assert out_path.exists()
    info = probe_video(out_path)
    assert "video" in info and "audio" in info
    assert probe_resolution(out_path) == (320, 568)
    # rendered duration should be close to narration length + the tail buffer
    duration = probe_duration(out_path)
    assert duration == pytest.approx(line_times[-1].end + 1.0, abs=0.5)


def test_render_piece_video_mixes_in_music(tmp_path):
    # Regression test: render_piece_video used to only mux narration, with
    # no way to layer background music at all -- every rendered piece was
    # silent except for the voice track.
    import shutil
    from pipeline.config import ROOT_DIR
    canvas_lib = tmp_path / "canvas_lib.js"
    shutil.copy(ROOT_DIR / "pipeline" / "canvas_lib" / "helpers.js", canvas_lib)

    piece_html = tmp_path / "piece.html"
    piece_html.write_text(MINIMAL_PIECE_HTML.replace("CANVAS_LIB_PATH", "canvas_lib.js"))

    lines = ["Hello there."]
    narration_path, line_times = synthesize_narration_lines(lines, tmp_path / "audio", voice="en-US-GuyNeural")

    music_path = tmp_path / "music.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=220:duration=3", str(music_path)],
        check=True,
    )

    out_path = tmp_path / "out_with_music.mp4"
    render_piece_video(
        piece_html, line_times, narration_path, out_path,
        resolution=(320, 568), fps=10, music_path=music_path,
    )

    assert out_path.exists()
    info = probe_video(out_path)
    assert "video" in info and "audio" in info
