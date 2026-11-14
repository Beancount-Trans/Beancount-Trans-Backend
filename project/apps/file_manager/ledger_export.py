"""用户账本目录打包导出。"""
from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime
from pathlib import Path

from project.utils.file import BeanFileManager

EXCLUDED_DIRS = {'.git'}
BEAN_SUFFIX = '.bean'


class LedgerExportEmpty(RuntimeError):
    """账本目录不存在或没有任何可导出的账本文件。"""


def build_ledger_zip(user) -> tuple[bytes, str]:
    """把用户当前账本目录打包为 ZIP。

    Args:
        user: 已认证用户

    Returns:
        (zip 字节内容, 下载文件名)

    Raises:
        LedgerExportEmpty: 账本目录不存在或没有任何 .bean 文件
    """
    root = Path(BeanFileManager.get_user_assets_path(user)).resolve()
    if not root.is_dir():
        raise LedgerExportEmpty('账本还没有内容，请先解析并写入账单')

    buffer = io.BytesIO()
    bean_count = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in EXCLUDED_DIRS
                and not (Path(dirpath) / name).is_symlink()
            ]
            for name in filenames:
                full_path = Path(dirpath) / name
                if full_path.is_symlink() or not full_path.is_file():
                    continue
                relative_path = full_path.relative_to(root).as_posix()
                archive.write(full_path, arcname=relative_path)
                if full_path.suffix == BEAN_SUFFIX:
                    bean_count += 1

    if bean_count == 0:
        raise LedgerExportEmpty('账本还没有内容，请先解析并写入账单')

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{user.username}_ledger_{timestamp}.zip'
    return buffer.getvalue(), filename
