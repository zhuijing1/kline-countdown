"""打包成单文件 exe（K线倒计时.exe）。

为什么用 Python 而不是直接写 PyInstaller 命令：
.bat 文件被 cmd 按系统代码页（GBK）解析，中文名容易变乱码；
这里用 UTF-8 源码 + subprocess 传参，编码稳定。

用法：双击 build.bat，或 `python build.py`
产物：dist/K线倒计时.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "K线倒计时"
ICON = ROOT / "assets" / "app.ico"
VERSION_FILE = ROOT / "version_info.txt"


def ensure_icon() -> None:
    sys.path.insert(0, str(ROOT))
    from core.icon import ICON_SIZES, write_icon_file, write_png_file

    ICON.parent.mkdir(exist_ok=True)
    write_icon_file(ICON, ICON_SIZES)
    write_png_file(ROOT / "assets" / "app.png", 200)
    print(f"[1/3] 图标已生成：{ICON}")


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[2/3] 安装 PyInstaller …")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pyinstaller"], check=True)
    else:
        print("[2/3] PyInstaller 已就绪")


def build() -> Path:
    for stale in ("build", "dist"):
        shutil.rmtree(ROOT / stale, ignore_errors=True)
    for spec in ROOT.glob("*.spec"):
        spec.unlink()

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",          # 单文件，拷到别的电脑直接双击
        "--windowed",         # 不带控制台窗口
        "--name", APP_NAME,
        "--icon", str(ICON),
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
        "--exclude-module", "pytest",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        str(ROOT / "main.py"),
    ]
    if VERSION_FILE.exists():
        command.insert(-1, "--version-file")
        command.insert(-1, str(VERSION_FILE))

    print("[3/3] 正在打包 …")
    subprocess.run(command, cwd=ROOT, check=True)
    return ROOT / "dist" / f"{APP_NAME}.exe"


def main() -> int:
    ensure_icon()
    ensure_pyinstaller()
    exe = build()
    if not exe.exists():
        print("打包失败：没有找到产物")
        return 1

    # 中间产物（build/ 和 .spec）留着没用，删掉保持目录干净
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    for spec in ROOT.glob("*.spec"):
        spec.unlink()

    size = exe.stat().st_size / (1024 * 1024)
    print(f"\n完成：{exe}（{size:.1f} MB）")
    print("把这一个 exe 拷到其他 Windows 电脑即可直接运行，不需要装 Python。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
