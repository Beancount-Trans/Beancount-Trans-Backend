"""
对账注释管理服务

用于解析 trans/reconciliation.bean 条目，并支持注释指定行。
"""
import logging
import os
import re
from datetime import timedelta
from typing import Dict, List, Optional, Any, Tuple

from beancount import loader
from beancount.core.data import Transaction, Pad, Balance

from project.utils.file import BeanFileManager
from .entry_matcher import EntryMatcher

logger = logging.getLogger(__name__)


class ReconciliationCommentService:
    """对账注释管理服务

    负责解析 trans/reconciliation.bean 中的对账条目，
    并支持注释指定行（供撤销对账使用）。
    """
    
    @staticmethod
    def _is_entry_commented(entry: Any, file_path: str, original_file_lines: List[str] = None) -> bool:
        """检查条目是否已被注释
        
        Args:
            entry: Beancount 条目
            file_path: 文件路径
            original_file_lines: 文件的原始内容（如果为None，则重新读取文件）
            
        Returns:
            如果条目已被注释返回 True，否则返回 False
        """
        # Beancount 条目有 meta 属性，包含 filename 和 lineno
        if hasattr(entry, 'meta') and entry.meta:
            filename = entry.meta.get('filename')
            lineno = entry.meta.get('lineno')
            
            # 检查文件名是否匹配（处理相对路径）
            if filename and lineno:
                # 标准化路径进行比较
                abs_filename = os.path.abspath(filename)
                abs_file_path = os.path.abspath(file_path)
                
                if abs_filename == abs_file_path:
                    # 使用原始文件内容（如果提供），否则重新读取文件
                    try:
                        if original_file_lines is None:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                lines = f.readlines()
                        else:
                            lines = original_file_lines
                        
                        # 检查从 lineno 开始的前几行，看是否有注释
                        # Beancount 解析器可能会跳过注释行，所以我们需要检查多个可能的位置
                        start_line = lineno - 1  # 转换为0-based索引
                        
                        # Beancount 解析器在解析时可能会跳过注释行，返回的 lineno 指向去注释后的内容位置
                        # 我们需要向前查找，找到实际的注释行
                        # 检查从 start_line 开始向前最多10行，找到第一个包含日期格式的注释行
                        for check_line_idx in range(start_line, max(-1, start_line - 10), -1):
                            if check_line_idx < 0 or check_line_idx >= len(lines):
                                continue
                            
                            check_line = lines[check_line_idx]
                            check_stripped = check_line.lstrip()
                            
                            # 跳过空行
                            if not check_stripped or check_stripped == '\n':
                                continue
                            
                            # 检查是否是条目的开始行（包含日期格式 YYYY-MM-DD）
                            date_pattern = r'\d{4}-\d{2}-\d{2}'
                            if re.search(date_pattern, check_stripped):
                                # 如果这一行被注释了，条目就被注释了
                                if check_stripped.startswith(';'):
                                    return True
                                else:
                                    # 如果这一行未被注释，且是条目的开始行，则条目未被注释
                                    return False
                        
                        # 如果没有找到明确的条目行，默认认为未被注释（保守策略）
                        return False
                    except Exception as e:
                        logger.warning(f"读取文件检查注释状态失败 {file_path}: {e}")
        
        return False
    
    @staticmethod
    def _get_entry_line_numbers(entry: Any, file_path: str) -> List[int]:
        """获取条目在文件中的行号列表
        
        Beancount 条目可能跨多行，返回所有相关行的行号。
        通过读取文件内容来确定条目的实际行号范围。
        
        Args:
            entry: Beancount 条目
            file_path: 文件路径
            
        Returns:
            行号列表（从1开始）
        """
        line_numbers = []
        
        # Beancount 条目有 meta 属性，包含 filename 和 lineno
        if hasattr(entry, 'meta') and entry.meta:
            filename = entry.meta.get('filename')
            lineno = entry.meta.get('lineno')
            
            # 检查文件名是否匹配（处理相对路径、符号链接）
            if filename and lineno:
                # 标准化路径进行比较
                abs_filename = os.path.abspath(os.path.realpath(filename))
                abs_file_path = os.path.abspath(os.path.realpath(file_path))
                
                if abs_filename == abs_file_path:
                    # 读取文件内容，确定条目的实际行数
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                        
                        # Transaction 条目可能跨多行
                        if isinstance(entry, Transaction):
                            # 从 lineno 开始，找到所有相关的行
                            # Transaction 格式：
                            # YYYY-MM-DD * "Payee" "Narration"
                            #     Account1 Amount Currency
                            #     Account2 Amount Currency
                            start_line = lineno - 1  # 转换为0-based索引
                            if start_line < len(lines):
                                line_numbers.append(lineno)  # 日期行
                                # 查找后续的 posting 行（以空格开头）
                                for i in range(start_line + 1, len(lines)):
                                    line = lines[i]
                                    # Posting 行通常以4个空格开头
                                    if line.strip() and (line.startswith('    ') or line.startswith('\t')):
                                        line_numbers.append(i + 1)  # 转换为1-based
                                    elif line.strip() and not line.strip().startswith(';'):
                                        # 遇到非空行且不是注释，可能是下一个条目
                                        break
                        elif isinstance(entry, (Pad, Balance)):
                            # Pad 和 Balance 通常只占一行
                            line_numbers.append(lineno)
                    except Exception as e:
                        logger.warning(f"读取文件确定行号失败 {file_path}: {e}")
                        # 降级：只返回起始行号
                        line_numbers.append(lineno)
        
        return line_numbers
    
    @staticmethod
    def _parse_reconciliation_bean(user) -> Tuple[List[Dict], Dict[int, List[int]]]:
        """解析 trans/reconciliation.bean 文件
        
        Args:
            user: 用户对象
            
        Returns:
            (标准化条目列表, 索引到行号的映射字典)
        """
        reconciliation_path = BeanFileManager.get_reconciliation_bean_path(user)
        
        if not os.path.exists(reconciliation_path):
            logger.debug(f"对账文件不存在: {reconciliation_path}")
            return [], {}
        
        try:
            # 在解析之前，先读取文件的原始内容
            # 这样我们可以在检查注释时使用原始内容
            with open(reconciliation_path, 'r', encoding='utf-8') as f:
                original_file_lines = f.readlines()
            
            entries, errors, options = loader.load_file(reconciliation_path)
            
            if errors:
                logger.warning(f"解析对账文件时有 {len(errors)} 个错误")
            
            normalized_entries = []
            entry_to_lines = {}
            
            abs_reconciliation_path = os.path.abspath(os.path.realpath(reconciliation_path))
            for entry in entries:
                # 只处理 Transaction、Pad、Balance
                if not isinstance(entry, (Transaction, Pad, Balance)):
                    continue
                # 只处理来自 reconciliation.bean 的条目（避免 include 导致路径不一致时遗漏行号）
                if hasattr(entry, 'meta') and entry.meta:
                    entry_filename = entry.meta.get('filename')
                    if entry_filename and os.path.abspath(os.path.realpath(entry_filename)) != abs_reconciliation_path:
                        continue
                
                # 跳过已注释的条目（使用原始文件内容）
                if ReconciliationCommentService._is_entry_commented(entry, reconciliation_path, original_file_lines):
                    continue
                
                normalized = EntryMatcher.normalize_entry(entry)
                if normalized:
                    normalized['_original_entry'] = entry
                    normalized_entries.append(normalized)
                    
                    # 获取行号，使用索引作为键
                    line_numbers = ReconciliationCommentService._get_entry_line_numbers(
                        entry, reconciliation_path
                    )
                    if line_numbers:
                        # 使用 normalized_entries 中的索引作为键
                        entry_index = len(normalized_entries) - 1
                        entry_to_lines[entry_index] = line_numbers
            
            return normalized_entries, entry_to_lines
            
        except Exception as e:
            logger.error(f"解析对账文件失败 {reconciliation_path}: {e}")
            return [], {}
    
    @staticmethod
    def _comment_lines_in_file(file_path: str, line_numbers: List[int]) -> int:
        """注释文件中的指定行
        
        Args:
            file_path: 文件路径
            line_numbers: 要注释的行号列表（从1开始）
            
        Returns:
            实际注释的行数
        """
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return 0
        
        # 读取文件内容
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        commented_count = 0
        line_numbers_set = set(line_numbers)
        
        # 注释指定行（如果还没有被注释）
        for idx, line in enumerate(lines):
            line_num = idx + 1  # 转换为从1开始的行号
            if line_num in line_numbers_set:
                # 检查是否已经被注释
                stripped = line.lstrip()
                if stripped and not stripped.startswith(';'):
                    # 在行的最前面添加注释符号 "; "（分号+空格），保留原有缩进和内容
                    lines[idx] = '; ' + line
                    commented_count += 1
        
        # 写回文件
        if commented_count > 0:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            logger.info(f"已注释 {commented_count} 行在文件 {file_path}")
        
        return commented_count

    @staticmethod
    def cleanup_unused_pads(user_or_username) -> int:
        """注释掉 reconciliation.bean 中已变为「无用」的 pad（保留 balance 断言）

        Beancount 的 pad 插件在某个 pad 未为后续 balance 合成补差交易时报
        "Unused Pad entry"。判定标准由 Beancount 的断言容差决定（例如两位小数的
        balance 容差为 0.01），因此「金额完全一致」与「相差在容差内」都会被识别。
        这里直接依据 loader 的输出进行检测，与 Fava 判定保持一致。

        该方法为 best-effort：内部捕获全部异常并仅记录日志，不向外抛出，
        以免影响调用方的写入流程。

        Args:
            user_or_username: User 对象或 username 字符串

        Returns:
            实际注释的行数；无需清理或失败时返回 0
        """
        try:
            reconciliation_path = BeanFileManager.get_reconciliation_bean_path(user_or_username)
            if not os.path.exists(reconciliation_path):
                return 0

            main_path = BeanFileManager.get_main_bean_path(user_or_username)
            if not os.path.exists(main_path):
                return 0

            _entries, errors, _options = loader.load_file(main_path)

            target_path = os.path.abspath(os.path.realpath(reconciliation_path))
            line_numbers = []
            for error in errors:
                # PadError: (source, message, entry)，仅处理「无用 pad」
                if getattr(error, 'message', None) != 'Unused Pad entry':
                    continue
                pad = getattr(error, 'entry', None)
                if not isinstance(pad, Pad):
                    continue
                meta = getattr(pad, 'meta', None) or {}
                filename = meta.get('filename')
                lineno = meta.get('lineno')
                if not filename or not lineno:
                    continue
                # 只处理 reconciliation.bean 中的 pad（平台托管文件），
                # 不误伤用户自有文件中的 pad
                if os.path.abspath(os.path.realpath(filename)) != target_path:
                    continue
                line_numbers.append(lineno)

            if not line_numbers:
                return 0

            commented = ReconciliationCommentService._comment_lines_in_file(
                reconciliation_path, sorted(set(line_numbers))
            )
            if commented:
                logger.info(
                    f"已注释 {commented} 行无用 pad 在文件 {reconciliation_path}"
                )
            return commented
        except Exception as e:
            logger.warning(f"清理无用 pad 失败: {e}", exc_info=True)
            return 0

    @staticmethod
    def maintain_ledger(user_or_username) -> Dict[str, int]:
        """解析写入后维护对账条目与账本的一致性（best-effort）

        1. 补 pad：对账写入的 balance 断言被后补（日期更早）的交易打破时，
           在断言前一天补一条 pad，把未解释的差额记入分摊账户；
        2. 清 pad：余额落入断言容差后不再起作用的 pad 会被注释掉。

        整体不抛异常，失败只记录日志，避免影响调用方的写入流程。

        Returns:
            {'pads_added': 补 pad 条数, 'pads_commented': 注释 pad 行数}
        """
        pads_added = ReconciliationCommentService.repair_broken_balances(user_or_username)
        pads_commented = ReconciliationCommentService.cleanup_unused_pads(user_or_username)
        return {'pads_added': pads_added, 'pads_commented': pads_commented}

    @staticmethod
    def repair_broken_balances(user_or_username, max_rounds: int = 20) -> int:
        """为被后补交易打破的对账 balance 断言补 pad（best-effort）

        只处理 trans/reconciliation.bean 中的断言。Beancount 按日期而非文件行序
        配对 pad 与 balance，因此新增 pad 直接追加到文件末尾即可生效；若补 pad 后
        断言仍然失败（例如 pad 账户与断言账户相同），则回滚本次追加并跳过。

        Args:
            user_or_username: User 对象或 username 字符串
            max_rounds: 最多补多少条 pad，防止异常数据导致死循环

        Returns:
            实际补上的 pad 条数；无需修复或失败时返回 0
        """
        try:
            reconciliation_path = BeanFileManager.get_reconciliation_bean_path(user_or_username)
            if not os.path.exists(reconciliation_path):
                return 0

            main_path = BeanFileManager.get_main_bean_path(user_or_username)
            if not os.path.exists(main_path):
                return 0

            user = ReconciliationCommentService._resolve_user(user_or_username)
            if user is None:
                return 0

            target_path = os.path.abspath(os.path.realpath(reconciliation_path))
            added = 0
            skipped = set()
            for _ in range(max(1, max_rounds)):
                _entries, errors, _options = loader.load_file(main_path)
                broken = ReconciliationCommentService._find_broken_balance(
                    errors, target_path, skipped
                )
                if broken is None:
                    break

                key = (broken.account, broken.date)
                pad_account = ReconciliationCommentService._resolve_pad_account(
                    user, broken.account, broken.date
                )
                if not pad_account or pad_account == broken.account:
                    logger.warning(
                        "无法为被打断的 balance 断言找到可用的分摊账户，跳过: "
                        f"{broken.date} {broken.account}"
                    )
                    skipped.add(key)
                    continue

                pad_date = broken.date - timedelta(days=1)
                text = (
                    f'; 自动补 pad：balance 断言 {broken.date} {broken.account} 被后补交易打破\n'
                    f'{pad_date} pad {broken.account} {pad_account}\n'
                )
                size_before = os.path.getsize(reconciliation_path)
                if not ReconciliationCommentService._append_text(reconciliation_path, text):
                    break

                _entries2, errors2, _options2 = loader.load_file(main_path)
                if ReconciliationCommentService._has_broken_balance(
                    errors2, target_path, broken
                ):
                    # 补 pad 没能修复断言，回滚本次追加，避免污染账本
                    os.truncate(reconciliation_path, size_before)
                    logger.warning(
                        f"补 pad 后 balance 断言仍失败，已回滚: "
                        f"{broken.date} {broken.account} -> {pad_account}"
                    )
                    skipped.add(key)
                    continue

                logger.info(
                    f"已为被打断的 balance 断言补 pad: "
                    f"{pad_date} pad {broken.account} {pad_account}"
                )
                added += 1

            return added
        except Exception as e:
            logger.warning(f"补对账 pad 失败: {e}", exc_info=True)
            return 0

    @staticmethod
    def _broken_balance_entries(errors, target_path: str) -> List[Balance]:
        """从 loader 错误中筛出位于 reconciliation.bean 的失败 balance 断言"""
        broken: List[Balance] = []
        for error in errors:
            message = getattr(error, 'message', '') or ''
            if not message.startswith('Balance failed for'):
                continue
            entry = getattr(error, 'entry', None)
            if not isinstance(entry, Balance):
                continue
            meta = getattr(entry, 'meta', None) or {}
            filename = meta.get('filename')
            if not filename:
                continue
            if os.path.abspath(os.path.realpath(filename)) != target_path:
                continue
            broken.append(entry)
        return broken

    @staticmethod
    def _find_broken_balance(
        errors, target_path: str, skipped: Optional[set] = None
    ) -> Optional[Balance]:
        """取出最早的、尚未跳过的失败 balance 断言"""
        skipped = skipped or set()
        failed = ReconciliationCommentService._broken_balance_entries(errors, target_path)
        candidates = [
            entry for entry in failed if (entry.account, entry.date) not in skipped
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda entry: (entry.date, (getattr(entry, 'meta', None) or {}).get('lineno', 0)),
        )

    @staticmethod
    def _has_broken_balance(errors, target_path: str, balance: Balance) -> bool:
        """判断指定 balance 断言是否仍然失败"""
        return any(
            entry.account == balance.account and entry.date == balance.date
            for entry in ReconciliationCommentService._broken_balance_entries(errors, target_path)
        )

    @staticmethod
    def _resolve_user(user_or_username):
        """把 username 解析为 User 对象；已是 User 时原样返回"""
        from django.contrib.auth import get_user_model

        if isinstance(user_or_username, str):
            return get_user_model().objects.filter(username=user_or_username).first()
        return user_or_username

    @staticmethod
    def _resolve_pad_account(user, account_name: str, balance_date) -> Optional[str]:
        """确定被打断的 balance 断言应记入哪个分摊账户

        优先复用当次对账写入的 pad / 差额分配账户；取不到时，首次对账回落到
        Equity:Opening-Balances，其余回落到用户配置的兜底账户。
        """
        from django.contrib.contenttypes.models import ContentType
        from project.apps.account.models import Account
        from project.apps.reconciliation.models import ScheduledTask
        from project.apps.translate.models import FormatConfig

        account = Account.objects.filter(owner=user, account=account_name).first()
        if account is None:
            return None

        as_of_date = balance_date - timedelta(days=1)
        content_type = ContentType.objects.get_for_model(Account)
        completed = ScheduledTask.objects.filter(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            status='completed',
        )
        task = completed.filter(as_of_date=as_of_date).order_by('-completed_date', '-id').first()
        if task:
            for entry in task.reconciliation_entries or []:
                if entry.get('type') == 'Pad' and entry.get('source_account'):
                    return entry['source_account']
            items = task.reconciliation_transaction_items or []
            for item in items:
                if item.get('is_auto') and item.get('account'):
                    return item['account']
            for item in items:
                if item.get('account'):
                    return item['account']

        if not completed.filter(as_of_date__lt=as_of_date).exists():
            return 'Equity:Opening-Balances'
        config = FormatConfig.get_user_config(user)
        return config.reconciliation_fallback_account or 'Equity:Adjustments'

    @staticmethod
    def _append_text(file_path: str, text: str) -> bool:
        """在文件末尾追加文本（自动补齐必要的换行）"""
        if not text:
            return False
        needs_newline = False
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            with open(file_path, 'rb') as f:
                f.seek(-1, os.SEEK_END)
                needs_newline = f.read(1) != b'\n'
        payload = ('\n' if needs_newline else '') + text
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(payload)
        return True
