import base64
import hashlib
import re
import subprocess
import sys
import uuid

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

# RSA 私钥 (2048位) - 保持与 Java 服务端对齐
PRIVATE_KEY_PEM = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAx+NEwesvAq/5x+yoNPPimxyA5D/USS8PHauSaeZOtcOXMvyT
TpwR/DkAq65fknTMhOnRQbVZfwKwx4c7O0Yp9fd9nYSOguwkq+Jt854Zh7nFz76z
A/yUHgM95YFg2sKO3at3cjndKRhGvkdm8MTA9BsEy2G2xxUqv0isZ0VuxstzrUs1
zcx79u4tVts+ybWUz7+5GPBRJtPa7nMWJrlrriBtQrUZ6ctVqe9bQ1eLb3TVNAiq
dUtGaPnCnKTay1fy6D8M79sVB/JKpYGSTEY8FLJviRcNcrJ8i5XEtIiJ2s687wIz
BesrVLqqJLBU4udRezv89rpuyGmkzAocz0XChwIDAQABAoIBAFfVFCn3y2TtaZ8B
bRozjn/lAvgI2iG8VXKOqRvykOSKyP4lAR+aMcb4T0ShLyq+Ov4udf5fDy/hwUcz
s4HEv3xu31ofXLXHyQdkTDcv4f0DUxoJrETUsEsN1p1p/+KwejGKfkaJiZZcrf0/
h8rFUhoRY0ZxLe0F7o42A1evqVEdX1VJzZryZcRN5iW2DLG7c9TR01WqB+DkcskO
CpV17gkq5NEOUN0b+yJdNzcMYcc+il3C/sv4tcj4m4v7poFWt9GMNmUQFslfLPX3
DHawTd4j0qAK9FJ+7R4cj2RWAO4ytaxsaVWeI8pgv6QIXdZZcpvtPS8jijD69YLM
zjZCtTkCgYEA2y2sZRNhjEIB+FgeWBsNHQL+MAjbI7R6v9aehMu45+RbvlCtPkbc
+UQpMslnKlTFi2uQ1X0Als+kDgTHbvRCHUA3CXV8sThXmC2VbLiVaIidrSJdVtmz
yP3f/yruYuDpz/OXtM4hMGjfOirLp5Pl1CogpdE4fHb6YTNA3YvreFkCgYEA6Xfz
qwin1URfqE+Odl0DnhbxedknmvxaSoBqDz3v6LB7dr2yMqmWHZSEaNODyOYXNRXT
wB+56Tznzo3+4vCDHkTqbwpazL/SLvZi/SUGgSCQOt7mTPHIW53f0vrHAK6g8XPR
GYLaf3q1kBZpRrzgAuzS6lVuBZqinF3d+Sd5td8CgYBk/vPck4S0s8ninQBGixiM
0M8+ZSZNmqGheo0LFjD7MiAQX26lLtQuTHlLfMD8IZnxt7xCk9pMpButlggsGYPJ
pMh3pFqz8wlyBzc+pQO76o/1sssd9S1CJbItC6RTjd5Pw6iZWQ60Fu8eB5BWhPE3
xb3LitAjklOnrI1sSUhU6QKBgQCi9F14IHd8jNezk6vdA7kVq+/p218gd0jSPWVJ
tDJymFPkoizx3ZpwlQwCWrfeDnNeUxjUPZC2shMeAdBJOBRcmT+EN5b+2Fhs/P5E
sIYktMTWwmO+ivgMslnaWb1yxXCCdxMYmlPFrLFzm6DphcVZZVElzHEZqkAbogzf
7eSuwwKBgDZWI75d8SEMPszx9iXZmjAT66DPVehADMn0z0mPoxAgbE3pW3KNnXXX
XomxgzjYOLJ0nnes8MY0yY5U8G4n/q6w4nIHdb9A9yyhBpOnkFo1cWKVfu0Vm1bC
HlSJyNawt/34oR58ph9ZMENLgg/9rJPbUYGn5M9JNADsCSHkaRVs
-----END RSA PRIVATE KEY-----"""


def get_local_macs():
    """
    [增强型] 获取本地所有真实物理网卡的 MAC 地址列表
    """
    macs = set()
    # 规则：匹配标准的 12 位 16 进制 MAC 地址格式
    mac_regex = r'([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}|[0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})'

    # 方式 1: uuid.getnode (基础兜底)
    try:
        node = uuid.getnode()
        if not (node >> 40) & 1:
            macs.add(':'.join(("%012X" % node)[i:i + 2] for i in range(0, 12, 2)))
    except:
        pass

    # 方式 2: 命令行提取 (跨平台补强)
    commands = []
    if sys.platform == 'win32':
        commands = ['getmac /v', 'ipconfig /all']
    else:
        commands = ['ifconfig', 'ip link', 'networksetup -listallhardwareports']

    for cmd in commands:
        try:
            # 尝试不同编码读取输出 (针对 Windows 中文版)
            raw_out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
            try:
                out = raw_out.decode('gbk')
            except:
                out = raw_out.decode('utf-8', errors='ignore')

            found = re.findall(mac_regex, out)
            for m in found:
                # 统一为冒号分隔的大写格式
                fmt_m = m.replace('-', ':').upper()
                # 过滤掉无效地址和虚拟网卡常见特征 (可选)
                if fmt_m != '00:00:00:00:00:00' and fmt_m != 'FF:FF:FF:FF:FF:FF':
                    macs.add(fmt_m)
        except:
            pass

    if not macs:
        try:
            macs.add(':'.join(("%012X" % uuid.getnode())[i:i + 2] for i in range(0, 12, 2)))
        except:
            pass
    return sorted(list(macs))


def norm(m):
    """
    归一化 MAC 地址，去除非字母数字并转小写，与后端保持一致
    """
    if not m: return ""
    return ''.join(filter(str.isalnum, m)).lower()


def rsa_decrypt(license_key):
    """
    使用内置 RSA 私钥解密授权码
    """
    try:
        kr = RSA.import_key(PRIVATE_KEY_PEM)
        cr = PKCS1_v1_5.new(kr)
        decoded_b64 = base64.b64decode(license_key.strip().replace(" ", "").replace("\n", ""))
        ob = cr.decrypt(decoded_b64, sentinel=None)
        return ob.decode('utf-8') if ob else None
    except:
        return None


def derive_aes_key(source_str):
    """
    从原始字符串（MAC 或加工单号）派生 32 字节 AES 密钥
    """
    return hashlib.sha256(source_str.encode('utf-8')).digest()
