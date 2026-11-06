"""Git 用户账户托管态判定

账户的 ``open`` 与引用可能落在两类文件里：

- 平台区：``trans/``，由平台写入，不进 Git；
- 用户区：``main.bean``、``account/**``、``{年}/**``，以远端仓库为准。

规则：
- **平台区托管**：账户及其子账户只在平台区出现，平台可以改名、改币种、删除；
- **账本托管**：用户区已有该账户（或子账户）的 ``open`` 或引用，
  改名、改币种、删除都必须回到账本完成。

非 Git 用户不存在该区分，始终返回空（平台全权管理）。
"""
import os

from project.apps.account.ledger_refs import build_account_index, declared_state
from project.apps.account.ledger_sync import _is_git_user, _is_platform_managed
from project.utils.file import BeanFileManager


def ledger_custody_files(user, account, *, index=None) -> list[str]:
    """返回「账本托管」的证据文件（用户区中该账户及其子账户的出现位置）

    参数:
        user: 账户属主
        account: 待判定的账户；判定范围包含其子账户
        index: 可选的账户引用索引（``build_account_index`` 的结果），
            批量调用时复用，避免重复解析账本

    Returns:
        list[str]: 用户区文件的相对路径；空列表表示平台区托管
    """
    if not _is_git_user(user):
        return []

    if index is None:
        index = build_account_index(user)
    if not index:
        return []

    root = os.path.abspath(BeanFileManager.get_user_assets_path(user))
    account_name = account.account
    candidates = [
        name
        for name in index
        if name == account_name or name.startswith(account_name + ':')
    ]

    files = []
    for name in candidates:
        for location in index[name]:
            rel_path = location.get('file')
            if not rel_path:
                continue
            if not _is_platform_managed(user, os.path.join(root, rel_path)):
                files.append(rel_path)

    return sorted(set(files))


def is_ledger_custodied(user, account, *, index=None) -> bool:
    """账户是否由账本托管（改名、改币种、删除都应在账本完成）"""
    return bool(ledger_custody_files(user, account, index=index))


def ledger_closed_accounts(user, *, state=None) -> set:
    """账本中已 close 的账户集合（Git 用户）

    已 close 的账户历史已尘埃落定，平台侧残留记录可以直接清理，
    不需要也不应该再改写账本。
    """
    if not _is_git_user(user):
        return set()

    if state is None:
        state = declared_state(user)
    _opens, closes = state
    return set(closes)


def is_ledger_closed(user, account) -> bool:
    """账户是否已在账本中 close"""
    if not account.owner_id:
        return False
    return account.account in ledger_closed_accounts(user)
