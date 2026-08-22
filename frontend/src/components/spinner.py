# target path: frontend/src/components/spinner.py (new file)
"""
Custom loading spinner: a golfer silhouette swing sequence, one frame
visible at a time, crossfading in a loop -- used everywhere the app shows
a dcc.Loading (via its custom_spinner prop) instead of the default spinning
circle.

Frames live in assets/loading/ and are discovered automatically at import
time (sorted by filename) rather than hardcoded, since the user plans to
drop in more mid-swing frames over time -- adding a new image there (named
so it sorts into place, e.g. swing-3.png) is enough on its own, no code
change needed here.

Why the @keyframes rule is generated in Python instead of living as a
fixed block in a CSS file: a "show exactly 1/N of the loop, staggered by
1/N per frame" crossfade needs its opacity keyframe's percentage
breakpoints to depend on N (how many frames there are), and CSS
percentages inside @keyframes can't be parameterized with custom
properties/calc() -- only property *values* can. With a frame count fixed
at 2, a static rule tuned for 2 frames would look wrong (either gaps of
blank/opacity-0 time, or several frames overlapping at once) the moment
more frames get added. So _build_keyframes_css below computes the correct
percentages for however many frames were actually found, and the result
is injected as a real stylesheet via a data: URI html.Link -- the only
dash.html tag that can carry arbitrary raw CSS text without a third-party
component. assets/spinner.css still owns everything that ISN'T
frame-count-dependent (positioning, sizing, animation-name/timing-
function/iteration-count).
"""
import base64
from functools import lru_cache
from pathlib import Path

from dash import html

# frontend/src/assets/loading -- one level up from this file (components/)
# into assets/loading.
_FRAMES_DIR = Path(__file__).resolve().parent.parent / "assets" / "loading"
_FRAME_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")

# How long each individual frame gets on screen, including its own fade in
# and fade out -- the full loop duration is just this times however many
# frames are found. Slow enough to read as a golf swing rather than a
# flicker, fast enough not to feel like a stall.
_SECONDS_PER_FRAME = 1.1


def _discover_frames():
    """Sorted list of /assets/loading/<file> URL paths. Sorted by filename
    so a numbered naming scheme (swing-1.png, swing-2.png, swing-3.png...)
    controls playback order -- if this ever comes back empty (folder
    missing or emptied), the caller falls back to nothing rendered rather
    than erroring, so a missing-assets edge case just means "no spinner
    graphic" instead of a crashed page."""
    if not _FRAMES_DIR.is_dir():
        return []
    files = sorted(
        p.name for p in _FRAMES_DIR.iterdir() if p.suffix.lower() in _FRAME_EXTENSIONS
    )
    return [f"/assets/loading/{name}" for name in files]


@lru_cache(maxsize=None)
def _build_keyframes_css(frame_count):
    """The N-dependent @keyframes t3g-golf-fade rule, as a data: URI
    stylesheet href. Each frame is only ever "on" (non-zero opacity) for
    its own 1/frame_count slice of the cycle -- fading in at the start of
    that slice, holding, fading out at the end -- so with frames staggered
    by animation-delay one slice apart (see golf_swing_spinner), every
    slice of the full loop has exactly one frame visible and the handoffs
    overlap just enough to read as a fade rather than a hard cut or a
    blank flash. Cached per frame_count since it only actually changes
    when images are added to/removed from assets/loading/, not per
    render."""
    onspan_pct = 100.0 / frame_count
    # The fade-in/fade-out edges eat into the on-span symmetrically,
    # capped at 8 points so a tiny frame_count (e.g. 2, onspan=50%)
    # doesn't get a needlessly long fade -- for a larger frame_count the
    # onspan itself shrinks well below that cap and the 25% scaling takes
    # over instead.
    fade_pct = min(onspan_pct * 0.25, 8.0)
    hold_start = fade_pct
    hold_end = onspan_pct - fade_pct

    css = (
        "@keyframes t3g-golf-fade {"
        "0% { opacity: 0; }"
        f"{hold_start:.3f}% {{ opacity: 1; }}"
        f"{hold_end:.3f}% {{ opacity: 1; }}"
        f"{onspan_pct:.3f}% {{ opacity: 0; }}"
        "100% { opacity: 0; }"
        "}"
    )
    encoded = base64.b64encode(css.encode("utf-8")).decode("ascii")
    return f"data:text/css;base64,{encoded}"


def golf_swing_spinner(height="3.5rem"):
    """A dcc.Loading custom_spinner -- pass as
    dcc.Loading(..., custom_spinner=golf_swing_spinner())  instead of
    dcc.Loading(..., type="circle"). height controls the spinner's box
    size; each frame is object-fit:contain within it so frames of
    different aspect ratios (a wider follow-through vs. a narrower
    overhead finish, say) don't distort or jump the box size as they
    cycle."""
    frames = _discover_frames()
    if not frames:
        # No images found (assets/loading empty or missing) -- rather
        # than render an empty box with no loading affordance at all,
        # dash's own built-in spinner markup isn't trivially reusable
        # here, so just show nothing sized to the same box; the
        # dcc.Loading overlay itself still communicates "loading" via its
        # dimmed backdrop.
        return html.Div(className="t3g-golf-spinner", style={"height": height})

    if len(frames) == 1:
        # Nothing to crossfade with just one frame -- show it static
        # rather than animating a single image in and out of existence
        # against itself.
        return html.Div(
            className="t3g-golf-spinner",
            style={"height": height},
            children=html.Img(src=frames[0], className="t3g-golf-spinner-frame t3g-golf-spinner-frame--static"),
        )

    total_duration = _SECONDS_PER_FRAME * len(frames)

    return html.Div(
        className="t3g-golf-spinner",
        style={"height": height},
        children=[
            html.Link(rel="stylesheet", href=_build_keyframes_css(len(frames))),
            *[
                html.Img(
                    src=src,
                    className="t3g-golf-spinner-frame",
                    style={
                        "animationDuration": f"{total_duration}s",
                        # A negative delay starts the animation already
                        # partway through its cycle -- staggering each
                        # frame by its own index/len(frames) share of the
                        # total loop is what makes them take turns being
                        # visible instead of all fading in/out together.
                        "animationDelay": f"{-1 * index * _SECONDS_PER_FRAME}s",
                    },
                )
                for index, src in enumerate(frames)
            ],
        ],
    )