# 安全文件加密与分发系统 (v6.0)

本项目提供了一套端到端的安全文件分发方案，专门用于超大体积 (10GB+) 档案数据的加密分发。

## 🏗 系统架构
1.  **Java 服务端** (`geoarch-api`)
    *   **核心逻辑**：采用 `PipedInputStream` + `CipherOutputStream` 实现流式加密。
    *   **动态秘钥**：若绑定了 MAC 地址，则直接以 MAC 为秘钥源，无需生成 `license.txt`。
    *   **加密算法**：AES-256-CBC (核心载荷) + RSA-2048 (授权密钥)。
2.  **Python 客户端** (`file_encryption`)
    *   **安全预览工具** (`viewer_client.py`)：高性能 UI 工具，支持在不完全还原文件的情况下进行预览、编辑、标注。
    *   **一键还原工具** (`extract_client.py`)：命令行/窗口化还原工具，将加密包还原为原始 ZIP。

### 1. 编译客户端
执行以下脚本将 Python 源码打包为各平台独立运行程序（.exe 或 .app）：

*   **Windows 构建**：
    双击运行或在 CMD 中执行：
    ```cmd
    pack\build_client.bat
    ```
*   **Mac/Linux 构建**：
    在终端执行：
    ```bash
    sh pack/build_client.sh
    ```
生成的产物位于 `dist/` 目录下。

### 2. 核心更新 (v6.1 Pro)
*   **智能协议 (Magic Index)**：`.enc` 文件现在包含 `ENC` 魔数头与 `Mode` 字节。
    *   `Mode 1` (MAC-Bound)：自动抓取本机网卡 ID 派生 AES 密钥，实现零人工干预解密。
    *   `Mode 0` (Key-Based)：自动引导用户导入 `license.txt` 授权秘钥。
*   **到期预警**：预览工具开启时会精准计算并弹出到期提醒（天/小时）。
*   **兼容性**：保留了对旧版（无 ENC 头）档案的向下兼容解析能力。

## 安全特性
*   **流式处理**：内存占用恒定，支持 TB 级档案。
*   **硬件绑定**：强制校验 MAC 地址，不可绕过。
*   **动态 IV**：每个加密项拥有独立随机 IV。
*   **无残留预览**：预览工具在关闭时会自动清理内存中的敏感数据。
