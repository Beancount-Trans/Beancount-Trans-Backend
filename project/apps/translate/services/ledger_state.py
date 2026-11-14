"""账本状态判断：用户账本中是否已经存在交易条目。"""
import logging
import os
import re

from project.utils.file import BeanFileManager

logger = logging.getLogger(__name__)

# 交易指令行：日期 + 非 open/balance 等指令关键字。
# 同时覆盖 * / ! / txn / 单字符 flag，避免漏判。
_TRANSACTION_LINE = re.compile(
    r'(?m)^\d{4}-\d{2}-\d{2}\s+'
    r'(?!(?:open|close|commodity|pad|balance|note|document|price|event|query|custom)\b)\S'
)


def has_any_transactions(user_or_username) -> bool:
    """用户账本中是否已有交易条目。

    用于首期 Aha 的「首次写入」判断：只扫 .bean 文件里的交易指令，
    open / balance / pad 等不算。无法判断时按「已有数据」处理，
    避免对老用户重复触发首期月报。
    """
    if not user_or_username:
        return False

    try:
        root = BeanFileManager.get_user_assets_path(user_or_username)
    except Exception:
        logger.exception('读取用户账本目录失败，按已有数据处理')
        return True

    if not os.path.isdir(root):
        return False

    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith('.bean'):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, 'r', encoding='utf-8') as file_obj:
                    if _TRANSACTION_LINE.search(file_obj.read()):
                        return True
            except OSError:
                continue

    return False
