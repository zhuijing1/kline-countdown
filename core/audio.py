"""声音播放：内置合成提示音 + 本地音频文件（wav/mp3），支持音量、重复、间隔。

实现要点
--------
* 内置提示音在首次使用时合成 WAV（正弦+包络），音量直接写入采样值，
  因此不需要额外的音量控制通道；
* 用户导入的 WAV 同样按音量重采样后写入临时文件播放；
* mp3 等其他格式走 WinMM MCI（音量尽力而为，部分格式不支持 setaudio）；
* 播放在独立线程完成，绝不阻塞 UI；复活制/间隔排队，``stop()`` 可打断；
* 文件缺失、解码失败等情况记录在 ``last_error``，由设置界面展示。
"""

from __future__ import annotations

import ctypes
import math
import os
import queue
import tempfile
import threading
import time
import wave
from array import array
from pathlib import Path

try:  # 仅 Windows 有效
    import winsound
except ImportError:  # pragma: no cover - 非 Windows 环境
    winsound = None  # type: ignore[assignment]

from .models import FILE_PREFIX, PRESET_PREFIX, SOUND_PRESET_KEYS

SAMPLE_RATE = 22050


def _winmm():
    try:
        return ctypes.windll.winmm
    except (AttributeError, OSError):  # pragma: no cover
        return None


class AudioPlayer:
    def __init__(self) -> None:
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._generation = 0
        self._thread = threading.Thread(target=self._worker, name="kcd-audio", daemon=True)
        self._thread.start()
        self.last_error: str | None = None
        self._cache_dir = Path(tempfile.gettempdir()) / "kline-countdown-audio"
        self._mci_seq = 0
        self._mci_lock = threading.Lock()

    # ---------------- 内部工具 ----------------

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            if job["generation"] != self._generation:
                continue
            source = job["source"]
            volume = job["volume"]
            repeat = max(1, int(job["repeat"]))
            interval = max(0.0, float(job["interval"]))
            for index in range(repeat):
                if job["generation"] != self._generation:
                    break
                self._play_once(source, volume)
                if index < repeat - 1 and interval > 0:
                    time.sleep(interval)

    def _play_once(self, source: str, volume: float) -> bool:
        try:
            if not source:
                return True
            if source.startswith(PRESET_PREFIX):
                path = self._preset_wav(source[len(PRESET_PREFIX):], volume)
            elif source.startswith(FILE_PREFIX):
                path = source[len(FILE_PREFIX):]
                if not Path(path).is_file():
                    self.last_error = f"音频文件不存在：{path}"
                    return False
                if path.lower().endswith(".wav"):
                    path = self._scaled_wav(path, volume)
                else:
                    return self._play_mci(path, volume)
            else:
                path = source  # 兼容直接传路径
                if not Path(path).is_file():
                    self.last_error = f"音频文件不存在：{path}"
                    return False
                if not path.lower().endswith(".wav"):
                    return self._play_mci(path, volume)
            if winsound is None:
                self.last_error = "当前平台不支持音频播放"
                return False
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return True
        except Exception as exc:  # noqa: BLE001 - 播放失败不应影响倒计时
            self.last_error = f"播放失败：{exc}"
            return False

    # ---------------- 合成 / 重采样 ----------------

    def _preset_wav(self, key: str, volume: float) -> str:
        if key not in SOUND_PRESET_KEYS:
            self.last_error = f"未知内置提示音：{key}"
            key = "ding"
        bucket = int(round(volume * 20))
        path = self._cache_dir / f"preset-{key}-{bucket:02d}.wav"
        if not path.exists():
            frames = _synth_preset(key)
            self._write_wav(path, frames)
        return str(path)

    def _scaled_wav(self, source: str, volume: float) -> str:
        bucket = int(round(volume * 20))
        src = Path(source)
        stat = src.stat()
        key = f"{abs(hash(src.resolve())) & 0xffffffff:x}-{int(stat.st_mtime)}-{bucket:02d}"
        path = self._cache_dir / f"file-{key}.wav"
        if path.exists():
            return str(path)
        with wave.open(str(src), "rb") as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
        if width != 2:
            path = str(src)  # 非 16bit 用原文件播放，音量由系统决定
            return path
        samples = array("h")
        samples.frombytes(frames)
        if channels > 1:
            down = array("h")
            for i in range(0, len(samples) - channels + 1, channels):
                total = sum(samples[i: i + channels])
                down.append(int(total / channels))
            samples = down
        gain = volume
        for i, value in enumerate(samples):
            scaled = int(value * gain)
            if scaled > 32767:
                scaled = 32767
            elif scaled < -32768:
                scaled = -32768
            samples[i] = scaled
        self._write_wav(path, samples.tobytes())
        return str(path)

    def _write_wav(self, path: Path, frames: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.wav")
        with wave.open(str(tmp), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(frames)
        os.replace(tmp, path)

    def _play_mci(self, path: str, volume: float) -> bool:
        lib = _winmm()
        if lib is None:
            self.last_error = "WinMM 不可用，无法播放该格式"
            return False
        with self._mci_lock:
            self._mci_seq += 1
            alias = f"kcd{self._mci_seq}"
            try:
                if lib.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, None) != 0:
                    if lib.mciSendStringW(f'open "{path}" alias {alias}', None, 0, None) != 0:
                        self.last_error = f"无法打开音频文件：{path}"
                        return False
                if 0 <= volume <= 1:
                    lib.mciSendStringW(f"setaudio {alias} volume to {int(volume * 1000)}", None, 0, None)
                if lib.mciSendStringW(f"play {alias}", None, 0, None) != 0:
                    self.last_error = f"播放失败：{path}"
                    lib.mciSendStringW(f"close {alias}", None, 0, None)
                    return False
                # 立即关闭即可（异步播放进行中），结束自动回收
                lib.mciSendStringW(f"close {alias}", None, 0, None)
                return True
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"播放失败：{exc}"
                return False

    # ---------------- 对外接口 ----------------

    def play(self, source: str, volume: float = 0.8, repeat: int = 1, interval: float = 0.5) -> None:
        if not source:
            return
        self.last_error = None
        self._queue.put(
            {
                "source": source,
                "volume": max(0.0, min(1.0, float(volume))),
                "repeat": max(1, int(repeat)),
                "interval": interval,
                "generation": self._generation,
            }
        )

    def stop(self) -> None:
        """打断所有尚未开始的播放并停止当前声音（换 K 线/全局静音时调用）。"""
        self._generation += 1
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        self.stop()
        self._queue.put(None)


# --------------------------------------------------------------------------
# 内置提示音合成
# --------------------------------------------------------------------------


def _env(position: float, length: float, attack: float = 0.01, release: float = 0.06) -> float:
    """线性攻击 + 指数衰减包络，避免爆音。"""
    if position < attack:
        return max(0.0, position / attack)
    left = max(0.0, length - position)
    return max(0.0, min(1.0, left / release))


def _tone(freq: float, duration: float, gain: float = 0.75) -> bytes:
    count = int(SAMPLE_RATE * duration)
    out = array("h")
    for i in range(count):
        t = i / SAMPLE_RATE
        value = math.sin(2.0 * math.pi * freq * t) * _env(t, duration) * gain
        out.append(int(value * 32767 * 0.9))
    return out.tobytes()


def _presets() -> dict[str, bytes]:
    return {
        "ding": b"".join([_tone(1180, 0.16), b"\x00\x00" * 100]),
        "beep": _tone(880, 0.12),
        "tick": _tone(1560, 0.05),
        "alarm": b"".join([_tone(720, 0.14), _tone(880, 0.14), _tone(720, 0.14)]),
    }


def _synth_preset(key: str) -> bytes:
    presets = _presets()
    return presets.get(key, presets["ding"])


