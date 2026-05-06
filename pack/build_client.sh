#!/bin/bash
# MacOS/Linux 打包脚本
# 请先确保安装了 pyinstaller: pip install pyinstaller pycryptodome

echo "开始打包解密客户端 (Unix/Mac)..."

# 清理旧的构建文件
rm -rf build dist

# 检查并安装依赖 (增加了 GUI 所需库)
python3 -m pip install pyinstaller pycryptodome Pillow pymupdf --quiet

echo ">>> 正在打包：1. 一键还原工具 (命令行版)..."
python3 -m PyInstaller --onefile --console --name "一键还原工具" src/extract_client.py

echo ">>> 正在打包：2. 安全预览工具 (图形界面版)..."
# 注意：窗口程序使用 --noconsole 隐藏黑窗口
python3 -m PyInstaller --onefile --noconsole --name "安全预览工具" src/viewer_client.py

echo "============================================================"
echo "打包完成！请在 dist 目录下查找：'一键还原工具' 和 '安全预览工具'"
echo "============================================================"
