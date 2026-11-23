from pathlib import Path

import cv2
import numpy as np


def encoded_image(extension: str = ".jpg", width: int = 64, height: int = 40) -> bytes:
    pixels = np.full((height, width, 3), (20, 90, 210), dtype=np.uint8)
    ok, encoded = cv2.imencode(extension, pixels)
    assert ok
    return encoded.tobytes()


def write_video(path: Path, count: int = 8) -> Path:
    codec = "VP80" if path.suffix == ".webm" else "mp4v"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), 8, (64, 40))
    try:
        assert writer.isOpened(), f"Could not open {codec} video encoder"
        for index in range(count):
            writer.write(np.full((40, 64, 3), (20, index * 10, 210), dtype=np.uint8))
    finally:
        writer.release()
    return path
