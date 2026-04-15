from ocr_hailo.diagnostics import run_checks


def test_run_checks_contains_python() -> None:
    results = run_checks()
    assert any(item.name == "Python" and item.ok for item in results)
