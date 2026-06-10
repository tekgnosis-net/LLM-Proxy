from app.routes.logs_routes import deframe_docker_log


def _frame(stream, text):
    b = text.encode()
    return bytes([stream, 0, 0, 0]) + len(b).to_bytes(4, "big") + b


def test_deframe_two_complete_frames():
    buf = _frame(1, "line a\n") + _frame(2, "line b\n")
    out, rem = deframe_docker_log(buf)
    assert out == ["line a\n", "line b\n"] and rem == b""


def test_deframe_keeps_incomplete_tail():
    full = _frame(1, "hello\n")
    out, rem = deframe_docker_log(full[:5])           # partial header
    assert out == [] and rem == full[:5]
    out2, rem2 = deframe_docker_log(rem + full[5:])   # completes it
    assert out2 == ["hello\n"] and rem2 == b""


def test_deframe_partial_payload():
    full = _frame(1, "abcdef")
    out, rem = deframe_docker_log(full[:10])           # header + 2 of 6 payload bytes
    assert out == [] and rem == full[:10]
