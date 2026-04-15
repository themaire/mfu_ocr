from ocr_hailo.extraction import write_text_output


def test_write_text_output_creates_file(tmp_path) -> None:
    target = tmp_path / "result.txt"
    returned = write_text_output("bonjour", target)

    assert returned == target
    assert target.read_text(encoding="utf-8") == "bonjour"
