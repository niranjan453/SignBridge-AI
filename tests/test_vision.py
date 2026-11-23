import sys
from dataclasses import replace
from time import perf_counter
from types import SimpleNamespace

import numpy as np
import pytest

from app.landmarks import MediaPipeHands, load_landmarks
from app.vision import CameraFrame, InvalidVisionInput, ModelInput, VisionProcessor
from test_backend import API
from vision_fixtures import encoded_image, write_video


@pytest.mark.parametrize("extension,mime", [(".jpg", "image/jpeg"), (".png", "image/png")])
def test_decode_resize_color_and_normalization(extension, mime):
    processor = VisionProcessor(model_input=ModelInput(max_side=32))
    result = processor.image(CameraFrame(encoded_image(extension), mime, 7, 123))
    assert result.pixels.shape == (20, 32, 3)
    assert result.pixels.dtype == np.float32 and not result.pixels.flags.writeable
    assert result.pixels[0, 0] == pytest.approx([210 / 255, 90 / 255, 20 / 255], abs=0.02)
    assert (result.source_width, result.source_height, result.sequence, result.timestamp_ms) == (64, 40, 7, 123)
    assert result.landmarks.status == "disabled"
    assert result.preprocessing_ms >= 0 and result.landmark_ms >= 0


def test_rotation_and_bgr_without_normalization():
    processor = VisionProcessor(model_input=ModelInput(color="bgr", normalize=False, rotation=90))
    result = processor.image(CameraFrame(encoded_image(".png"), "image/png", 0, 0))
    assert (result.width, result.height) == (40, 64)
    assert result.pixels.dtype == np.uint8
    assert tuple(result.pixels[0, 0]) == (20, 90, 210)


@pytest.mark.parametrize("image,mime", [
    (b"not a jpeg", "image/jpeg"),
    (encoded_image(".jpg")[:-9], "image/jpeg"),
    (encoded_image(".png")[:-20], "image/png"),
    (encoded_image(".png"), "image/jpeg"),
    (encoded_image(".png"), "image/gif"),
    (encoded_image(".png", width=64, height=8), "image/png"),
])
def test_invalid_images(image, mime):
    with pytest.raises(InvalidVisionInput):
        VisionProcessor().image(CameraFrame(image, mime, 0, 0))


def test_oversized_image_rejected_before_opencv_decode(monkeypatch):
    def unexpected(*args):
        pytest.fail("OpenCV decode must not run for an oversized image")

    packet = CameraFrame(encoded_image(".png"), "image/png", 0, 0)
    monkeypatch.setattr("app.vision.cv2.imdecode", unexpected)
    with pytest.raises(InvalidVisionInput, match="dimensions"):
        VisionProcessor(max_pixels=1000).image(packet)


@pytest.mark.parametrize("suffix", [".mp4", ".webm"])
def test_real_video_metadata_and_sampling(tmp_path, suffix):
    path = write_video(tmp_path / ("clip" + suffix))
    video = VisionProcessor(sample_fps=2).video(path, perf_counter() + 5)
    assert video.video.fps == pytest.approx(8)
    assert video.video.frame_count == 8
    assert video.video.duration_seconds == pytest.approx(1)
    assert [sample.sequence for sample in video.frames] == [0, 4]
    assert [sample.timestamp_ms for sample in video.frames] == [0, 500]


def test_video_limits_and_invalid_codec(tmp_path):
    path = write_video(tmp_path / "sample.mp4")
    processor = VisionProcessor()
    for limits in [{"max_video_frames": 4}, {"max_video_seconds": 0.5}, {"max_video_bytes": 1}]:
        with pytest.raises(InvalidVisionInput):
            replace(processor, **limits).video(path, perf_counter() + 5)
    assert len(replace(processor, max_video_samples=1).video(path, perf_counter() + 5).frames) == 1
    with pytest.raises(TimeoutError):
        processor.video(path, perf_counter() - 1)
    invalid = tmp_path / "invalid.mp4"
    invalid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    with pytest.raises(InvalidVisionInput, match="codec"):
        processor.video(invalid, perf_counter() + 5)


def test_video_api_cleans_up_success_and_failure(client, tmp_path, monkeypatch):
    uploaded = []
    original = VisionProcessor.video

    def capture_path(self, path, deadline):
        uploaded.append(path)
        return original(self, path, deadline)

    monkeypatch.setattr(VisionProcessor, "video", capture_path)
    url = f"{API}/translations/sign-to-text"
    headers = {"Content-Type": "video/mp4"}
    valid = write_video(tmp_path / "upload.mp4").read_bytes()
    assert client.post(url, content=valid, headers=headers).json()["vision"]["frame_count"] == 8
    result = client.post(url, content=b"\x00\x00\x00\x18ftypmp42", headers=headers)
    assert result.status_code == 422
    assert result.json()["error"]["details"]["error"]["code"] == "invalid_input"
    assert len(uploaded) == 2
    assert all(not path.parent.exists() for path in uploaded)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_landmark_adapter_controlled_detections(monkeypatch, tmp_path, count):
    points = [SimpleNamespace(x=-0.05, y=0.5, z=-0.1)] * 21
    categories = [[SimpleNamespace(category_name="Left", score=0.9)],
                  [SimpleNamespace(category_name="Right", score=0.8)]]
    calls = {}
    detector = SimpleNamespace(
        detect=lambda image: SimpleNamespace(hand_landmarks=[points] * count, handedness=categories[:count]),
        close=lambda: calls.update(closed=True),
    )

    def open_detector(options):
        calls.update(options)
        return detector

    module = SimpleNamespace(
        Image=lambda **kwargs: kwargs, ImageFormat=SimpleNamespace(SRGB="rgb"),
        tasks=SimpleNamespace(
            BaseOptions=SimpleNamespace(Delegate=SimpleNamespace(CPU="cpu")),
            vision=SimpleNamespace(HandLandmarker=SimpleNamespace(create_from_options=open_detector),
                                   HandLandmarkerOptions=lambda **kwargs: kwargs,
                                   RunningMode=SimpleNamespace(IMAGE="image")),
        ),
    )

    class BaseOptions:
        Delegate = SimpleNamespace(CPU="cpu")

        def __init__(self, **kwargs):
            self.options = kwargs

    module.tasks.BaseOptions = BaseOptions
    monkeypatch.setitem(sys.modules, "mediapipe", module)
    adapter = MediaPipeHands.open(tmp_path / "model.task", 0.7, 0.6)
    frame = VisionProcessor(landmarks=adapter).image(CameraFrame(encoded_image(), "image/jpeg", 0, 0))
    assert frame.landmarks.status == "ok"
    assert len(frame.landmarks.hands) == count
    assert [hand.handedness for hand in frame.landmarks.hands] == ["Left", "Right"][:count]
    assert all(len(hand.points) == 21 and hand.points[0].z == -0.1 for hand in frame.landmarks.hands)
    assert all(hand.points[0].x == -0.05 for hand in frame.landmarks.hands)
    assert calls["num_hands"] == 2
    assert calls["min_hand_detection_confidence"] == 0.7
    assert calls["min_hand_presence_confidence"] == 0.6
    adapter.close()
    assert calls["closed"] is True


def test_landmark_initialization_and_detection_failure(monkeypatch, tmp_path):
    def unavailable(*args):
        raise RuntimeError("private detector path")

    monkeypatch.setattr(MediaPipeHands, "open", unavailable)
    rgb = np.zeros((16, 16, 3), dtype=np.uint8)
    fallback = load_landmarks(tmp_path / "missing.task", 0.5, 0.5)
    assert fallback.extract(rgb).status == "unavailable"
    adapter = MediaPipeHands(SimpleNamespace(Image=unavailable, ImageFormat=SimpleNamespace(SRGB="rgb")), None)
    result = adapter.extract(rgb)
    assert result.status == "unavailable" and result.hands == ()
