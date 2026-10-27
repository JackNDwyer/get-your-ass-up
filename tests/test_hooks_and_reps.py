from types import SimpleNamespace

from plugin.scripts.hook import looks_like_test
from watcher.reps import L_ANKLE, L_HIP, L_KNEE, RepCounter, angle


def test_looks_like_test():
    for command in [
        "pytest -q",
        "uv run pytest tests/",
        "npm test",
        "npm run build",
        "cd app && pnpm test --watch=false",
        "cargo test",
        "go test ./...",
        "make test",
        "npx vitest run",
        "python -m unittest",
    ]:
        assert looks_like_test(command), command
    for command in ["ls -la", "git status", "cat pytest.ini", "grep -r jest src", "npm install", "echo test"]:
        assert not looks_like_test(command), command


def test_angle():
    assert round(angle((0, 1), (0, 0), (1, 0))) == 90
    assert round(angle((0, 1), (0, 0), (0, -1))) == 180


def _leg(knee_angle_deg: float):
    """Landmarks with the left hip-knee-ankle bent to roughly the given angle."""
    import math

    marks = [SimpleNamespace(x=0.0, y=0.0, visibility=0.0) for _ in range(33)]
    marks[L_KNEE] = SimpleNamespace(x=0.5, y=0.5, visibility=1.0)
    marks[L_ANKLE] = SimpleNamespace(x=0.5, y=0.9, visibility=1.0)
    rad = math.radians(knee_angle_deg)
    # hip at the given angle from the knee→ankle direction (straight down)
    marks[L_HIP] = SimpleNamespace(x=0.5 + 0.4 * math.sin(rad), y=0.5 + 0.4 * math.cos(rad), visibility=1.0)
    return marks


def test_rep_counter_counts_full_reps_only():
    counter = RepCounter("squats")
    for a in [175, 160, 120, 90, 100, 130, 170]:  # one full squat
        counter.update(_leg(a))
    assert counter.count == 1
    for a in [170, 140, 125, 140, 170]:  # a half squat never goes below "down"
        counter.update(_leg(a))
    assert counter.count == 1
    for a in [90, 160, 95, 120, 100, 165]:  # wobble at the bottom still counts once per rep
        counter.update(_leg(a))
    assert counter.count == 3


def test_plank_isnt_counted():
    counter = RepCounter("plank")
    assert not counter.countable
    assert not counter.update(_leg(90))


def test_parse_label():
    from vision import parse_label

    assert parse_label("Phone.") == "phone"
    assert parse_label(" exercising\n") == "exercising"
    assert parse_label("The person is at their desk") == "desk"
    assert parse_label("unclear") is None
