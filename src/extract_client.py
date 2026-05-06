import os
import sys
from datetime import datetime

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

import crypto_utils


def print_progress(order_no, current, total):
    """
    在控制台打印不换行的实时进度条
    """
    percent = int(100 * current / total) if total > 0 else 0
    bar_length = 20
    filled_length = int(bar_length * percent / 100)
    bar = '#' * filled_length + '.' * (bar_length - filled_length)
    sys.stdout.write(f"\r[*] 处理进度 [{order_no}]: [{bar}] {percent}%")
    sys.stdout.flush()


def decrypt_file(encrypted_file_path, license_key=None):
    """
    [核心] 流式解压还原逻辑：采用分块读取与即时解密，支持 TB 级超大档案且内存平稳。
    """
    try:
        if not os.path.exists(encrypted_file_path):
            print(f"\033[91m错误：找不到目标加密文件 {encrypted_file_path}\033[0m")
            return

        file_size = os.path.getsize(encrypted_file_path)
        local_macs = crypto_utils.get_local_macs()

        # 1. 密钥准备
        ak = None
        if license_key:
            # 尝试解密授权码
            raw_key = crypto_utils.rsa_decrypt(license_key)
            if raw_key:
                ak = crypto_utils.derive_aes_key(raw_key)

        # 2. 物理结构步进：尝试解密 Metadata 以确定正确的 Key
        target_ak = None
        target_iv = None

        with open(encrypted_file_path, "rb") as f_in:
            iv = f_in.read(16)
            head = f_in.read(4)  # 跳过协议头 ENC\x01

            # 确定待选 Key 列表 (授权码优先，硬件指纹兜底)
            candidates = []
            if ak: candidates.append(ak)
            for m in local_macs:
                candidates.append(crypto_utils.derive_aes_key(crypto_utils.norm(m)))

            # 移除重复
            unique_candidates = []
            for k in candidates:
                if k not in unique_candidates: unique_candidates.append(k)

            # Metadata 试解密 (读取前 4KB 进行 Key 校验)
            meta_chunk_raw = f_in.read(4096)
            decrypted_meta = None
            active_cipher = None

            for cand_ak in unique_candidates:
                try:
                    # 注意：此处不能使用 unpad，因为填充在全文件的末尾，而不是这 4KB 的末尾
                    c = AES.new(cand_ak, AES.MODE_CBC, iv)
                    test_data = c.decrypt(meta_chunk_raw)
                    # 校验：前 4 字节为 int (加工单号长度)，应为合理值 (如 < 100)
                    olen_check = int.from_bytes(test_data[0:4], 'big')
                    if 0 < olen_check < 100:
                        decrypted_meta = test_data
                        active_cipher = c  # 保持当前 Cipher 状态，用于后续连续解密
                        break
                except:
                    continue

            if not decrypted_meta:
                print("\033[91m错误：安全审查失败。密钥不正确或非本机授权。\033[0m")
                print(f"系统检测到本机物理标识数量: {len(local_macs)}")
                return

            # 3. 解析 Metadata
            offset = 0
            olen = int.from_bytes(decrypted_meta[offset:offset + 4], 'big');
            offset += 4
            order_no = decrypted_meta[offset:offset + olen].decode('utf-8', errors='ignore');
            offset += olen
            mlen = int.from_bytes(decrypted_meta[offset:offset + 4], 'big');
            offset += 4
            bound_mac = decrypted_meta[offset:offset + mlen].decode('utf-8', errors='ignore');
            offset += mlen
            elen = int.from_bytes(decrypted_meta[offset:offset + 4], 'big');
            offset += 4
            ev = decrypted_meta[offset:offset + elen].decode('utf-8', errors='ignore');
            offset += elen

            # 4. 显式校验
            if bound_mac:
                if not any(crypto_utils.norm(bound_mac) == crypto_utils.norm(m) for m in local_macs):
                    print(f"\033[91m错误：硬件锁定。该包仅授权给: {bound_mac}\033[0m")
                    return

            if ev:
                try:
                    if datetime.now() > datetime.strptime(ev, "%Y-%m-%d %H:%M:%S"):
                        print(f"\033[91m错误：解密锁定！该档案授权已于 {ev} 到期。\033[0m")
                        return
                except:
                    pass

            print(f"[*] 身份认证通过！加工单号: {order_no}")

            # 5. 执行全量流式还原
            target_dir = os.path.dirname(os.path.abspath(encrypted_file_path))
            output_file = os.path.join(target_dir, f"{order_no}.zip")

            with open(output_file, "wb") as f_out:
                # 写入第一块中 Metadata 之后、4KB 之内的 ZIP 数据
                zip_start_idx = decrypted_meta.find(b'PK\x03\x04')
                if zip_start_idx != -1:
                    f_out.write(decrypted_meta[zip_start_idx:])

                # 分块读取后续数据 (优化：4MB 每块提升吞吐量)
                chunk_size = 4 * 1024 * 1024
                processed_bytes = 16 + 4 + 4096
                last_percent = -1

                while True:
                    chunk = f_in.read(chunk_size)
                    if not chunk: break
                    processed_bytes += len(chunk)

                    # 使用同一个 active_cipher 保持 CBC 链式状态
                    dec_chunk = active_cipher.decrypt(chunk)

                    # 如果是最后一块，执行 unpad
                    if processed_bytes >= file_size:
                        try:
                            dec_chunk = unpad(dec_chunk, 16)
                        except:
                            pass

                    f_out.write(dec_chunk)

                    # 优化：限制进度打印频率，仅当百分比变化时才刷新控制台
                    curr_percent = int(100 * processed_bytes / file_size)
                    if curr_percent != last_percent:
                        print_progress(order_no, processed_bytes, file_size)
                        last_percent = curr_percent
            print()

        # 6. 完成后续处理
        os.remove(encrypted_file_path)
        print(f"\n\033[92m[√] 还原任务成功完成！已还原至: \033[4m{os.path.abspath(output_file)}\033[0m")

    except Exception as e:
        print(f"\n\033[91m还原发生致命错误: {e}\033[0m")


if __name__ == "__main__":
    print("=" * 60)
    print("      加工档案安全还原工作站 (一键还原模式) - v6.5 Pro      ")
    print("=" * 60)

    current_macs = crypto_utils.get_local_macs()
    if current_macs:
        print(f"[*] 当前本机 ID: {current_macs[0]} (若有多个网卡将自动轮询试错)")
    print("-" * 60)

    # 支持命令行参数与交互式输入
    if len(sys.argv) > 1:
        path = sys.argv[1]
        key = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        print("\033[94m[第一步]\033[0m 请将加工包 \033[1m.enc\033[0m 文件拖入此处并回车：")
        path = input(">> ").strip(' "\'')

        print("\n\033[94m[第二步]\033[0m 请将授权码文件 (\033[1mlicense.txt\033[0m) 拖入此处并回车：")
        print("\033[90m(若是硬件绑定模式，请直接按回车跳过)\033[0m")
        key_input = input(">> ").strip(' "\'')

        if key_input and os.path.exists(key_input) and os.path.isfile(key_input):
            try:
                with open(key_input, "r", encoding="utf-8", errors='ignore') as f:
                    key = f.read().strip()
                print(f"[*] 已自动读取授权文件内容。")
            except:
                key = key_input
        else:
            key = key_input

    if path:
        decrypt_file(path, key)

    input("\n任务结束，请按回车键退出程序...")
