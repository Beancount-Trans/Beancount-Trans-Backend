"""
对账服务

处理对账逻辑，包括差额处理、指令生成、待办状态更新等。
"""
import logging
import os
from decimal import Decimal
from datetime import date, timedelta
from typing import List, Dict, Optional, Any
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from beancount import loader
from beancount.core.data import Transaction, Pad, Balance

from project.utils.file import BeanFileManager
from .balance_calculation_service import BalanceCalculationService
from .recurrence import RecurrenceCalculator, RecurrenceParser
from .account_currency_service import AccountCurrencyService
from .entry_matcher import EntryMatcher

logger = logging.getLogger(__name__)


class ReconciliationService:
    """对账服务：处理差额逻辑和指令生成"""
    
    @staticmethod
    @transaction.atomic
    def execute_reconciliation(
        task,
        actual_balance: Decimal,
        currency: str = 'CNY',
        transaction_items: Optional[List[Dict]] = None,
        as_of_date: Optional[date] = None
    ) -> Dict:
        """
        执行对账的核心逻辑（带事务保护）
        
        Args:
            task: ScheduledTask 对象
            actual_balance: 实际余额
            currency: 币种，默认 CNY
            transaction_items: 动态表单数据（用于处理差额）
            as_of_date: 对账截止日期，必须由前端提供
            
        Returns:
            包含生成的指令和下一个待办 ID 的字典
        """
        from project.apps.reconciliation.models import ScheduledTask
        from project.apps.account.models import Account
        
        # 1. 获取账户信息
        account = task.content_object  # Account 对象
        if not isinstance(account, Account):
            raise ValueError("待办任务关联的对象不是 Account 类型")
        
        # 2. 验证 as_of_date 必须由前端提供
        if as_of_date is None:
            raise ValueError("as_of_date 必须由前端提供")
        
        # 3. 计算预期余额（使用 as_of_date）
        balances = BalanceCalculationService.calculate_balance(
            task.content_object.owner, 
            account.account,
            as_of_date=as_of_date
        )
        expected_balance = balances.get(currency, Decimal('0.00'))
        
        # 4. 计算差额
        difference = actual_balance - expected_balance
        
        # 5. 根据 as_of_date 计算指令日期
        # transaction/pad 指令日期 = as_of_date
        # balance 指令日期 = as_of_date + 1 day
        transaction_date = as_of_date
        balance_date = as_of_date + timedelta(days=1)
        
        # 6. 生成指令
        # 指令生成顺序：transaction → pad → balance（按需求要求）
        directives = []
        
        if difference == 0:
            # 无差额：仅生成 balance
            directives.append(
                BalanceCalculationService.generate_balance_directive(
                    account.account, actual_balance, balance_date, currency
                )
            )
        else:
            # 有差额：处理 transaction_items
            # 6.1 处理 transaction 条目（手动添加的）
            if not transaction_items:
                transaction_items = []
            
            auto_item = None
            total_allocated = Decimal('0.00')
            transaction_directives = []
            
            for item in transaction_items:
                if item.get('is_auto'):
                    auto_item = item
                else:
                    # 如果金额为空，说明该账户是要自动分配剩余金额的，不该再为该账户生成 transactions 指令
                    amount = item.get('amount')
                    if amount is None:
                        # 金额为空时，跳过该条目，不生成 transaction 指令
                        continue
                    amount = Decimal(str(amount))
                    total_allocated += amount
                    # 使用条目指定的日期，如果未指定则使用 as_of_date
                    item_date = item.get('date') or transaction_date
                    # 生成 transaction 指令
                    transaction_directives.append(
                        ReconciliationService._generate_transaction_directive(
                            task.content_object.owner, account.account, item['account'], amount, currency, item_date
                        )
                    )
            
            # 6.2 处理自动计算条目或剩余差额
            # 在复式记账中：实际余额 + 差额分配 = 预期余额
            # 所以：差额分配 = 预期余额 - 实际余额 = -difference
            # 剩余差额 = -difference - total_allocated
            remaining = -difference - total_allocated
            
            pad_directive = None
            auto_transaction_directive = None
            if auto_item:
                # 有自动计算条目
                if abs(remaining) == Decimal('0.01'):
                    # 金额为正负 0.01 时，使用 transaction 而不是 pad
                    # 使用 remaining 保留正负号（0.01 或 -0.01）
                    auto_transaction_directive = ReconciliationService._generate_transaction_directive(
                        task.content_object.owner, account.account, auto_item['account'], remaining, currency, transaction_date
                    )
                else:
                    # 其他情况使用 pad 兜底
                    # pad 指令会自动处理剩余差额，无需额外的 transaction 指令
                    pad_directive = BalanceCalculationService.generate_pad_directive(
                        account.account, auto_item['account'], transaction_date
                    )
            
            # 6.3 按顺序组装指令：transaction → pad → balance
            directives.extend(transaction_directives)  # 先添加手动 transaction
            if auto_transaction_directive:
                directives.append(auto_transaction_directive)  # 添加自动计算的 transaction（0.01 情况）
            if pad_directive:
                directives.append(pad_directive)  # 再添加 pad（非 0.01 情况）
            directives.append(
                BalanceCalculationService.generate_balance_directive(
                    account.account, actual_balance, balance_date, currency
                )
            )  # 最后添加 balance
        
        # 7. 写入 .bean 文件（在事务外执行）
        file_info = None
        try:
            file_info = ReconciliationService._append_directives(
                task.content_object.owner, 
                directives
            )
        except Exception as e:
            # 文件写入失败，直接抛出异常，不执行数据库操作
            logger.error(f"写入对账文件失败: {e}")
            raise
        
        # 8-9. 在事务中更新数据库（如果失败会自动回滚数据库，但需要手动回滚文件）
        try:
            # 8. 解析文件、提取当次条目、存入 reconciliation_entries（用于撤销时内容匹配）
            task.reconciliation_entries = ReconciliationService._extract_and_serialize_entries(
                task.content_object.owner, directives, as_of_date, account.account
            )
            # 8b. 序列化并存入 reconciliation_transaction_items（用于撤销后预填）
            task.reconciliation_transaction_items = ReconciliationService._serialize_transaction_items(
                transaction_items
            )
            # 9. 更新待办状态
            today = date.today()
            task.status = 'completed'
            task.completed_date = today
            task.as_of_date = as_of_date  # 保存账本对账日期，用于防止重复对账
            task.save()
            
            # 10. 创建下一个待办（基于 rrule 引擎，dtstart 取当前待办的 scheduled_date）
            next_task = None
            if account.reconciliation_rule and task.scheduled_date:
                try:
                    spec = RecurrenceParser.parse(account.reconciliation_rule)
                    next_date = RecurrenceCalculator.next_occurrence(
                        spec, task.scheduled_date, task.scheduled_date
                    )
                    
                    # 如果计算出的日期是今天或之前，循环延后直到日期为明天之后
                    while next_date is not None and next_date <= today:
                        next_date = RecurrenceCalculator.next_occurrence(
                            spec, task.scheduled_date, next_date
                        )
                    
                    if next_date is not None:
                        next_task = ScheduledTask.objects.create(
                            task_type='reconciliation',
                            content_type=ContentType.objects.get_for_model(Account),
                            object_id=account.id,
                            scheduled_date=next_date,
                            status='pending'
                        )
                except Exception as e:
                    logger.error(f"创建下一个待办失败: {e}")
                    # 注意：这里不抛出异常，因为创建下一个待办失败不应该影响主流程
            
            return {
                'status': 'success',
                'directives': directives,
                'next_task_id': next_task.id if next_task else None
            }
        
        except Exception as e:
            # 数据库操作失败，回滚文件写入
            if file_info:
                try:
                    ReconciliationService._rollback_file_write(file_info)
                except Exception as rollback_error:
                    logger.error(f"回滚文件写入失败: {rollback_error}", exc_info=True)
            
            # 重新抛出原始异常
            raise
    
    @staticmethod
    def _serialize_transaction_items(transaction_items: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """将 transaction_items 转为可 JSON 存储的格式（amount/date 转为 str）。"""
        if not transaction_items:
            return []
        result = []
        for item in transaction_items:
            row = {
                'account': item.get('account', ''),
                'is_auto': item.get('is_auto', False),
            }
            amount = item.get('amount')
            row['amount'] = str(amount) if amount is not None else None
            d = item.get('date')
            if hasattr(d, 'isoformat'):
                row['date'] = d.isoformat()
            else:
                row['date'] = d
            result.append(row)
        return result

    @staticmethod
    def _serialize_entry_for_storage(normalized: Dict) -> Dict:
        """将标准化条目转为 JSON 可存储格式（date -> isoformat, Decimal -> str）"""
        result = {}
        for k, v in normalized.items():
            if k == '_original_entry':
                continue
            if isinstance(v, date):
                result[k] = v.isoformat()
            elif isinstance(v, Decimal):
                result[k] = str(v)
            elif isinstance(v, list):
                new_list = []
                for item in v:
                    if isinstance(item, dict):
                        new_item = {}
                        for kk, vv in item.items():
                            if isinstance(vv, Decimal):
                                new_item[kk] = str(vv)
                            elif isinstance(vv, date):
                                new_item[kk] = vv.isoformat()
                            else:
                                new_item[kk] = vv
                        new_list.append(new_item)
                    else:
                        new_list.append(item)
                result[k] = new_list
            else:
                result[k] = v
        return result
    
    @staticmethod
    def _extract_and_serialize_entries(
        user,
        directives: List[str],
        as_of_date: date,
        reconciliation_account: str,
    ) -> Optional[List[Dict]]:
        """
        解析 trans/reconciliation.bean，提取本次写入的条目，转为 JSON 可存储格式。
        
        Args:
            user: 用户对象
            directives: 本次写入的指令列表（用于确定条目数量）
            as_of_date: 对账截止日期
            
        Returns:
            JSON 可序列化的条目列表，失败时返回 None
        """
        reconciliation_path = BeanFileManager.get_reconciliation_bean_path(user)
        if not os.path.exists(reconciliation_path):
            return None
        try:
            entries, errors, options = loader.load_file(reconciliation_path)
            if errors:
                logger.warning(f"解析对账文件时有 {len(errors)} 个错误")
            # 筛选 Transaction、Pad、Balance，且日期在本次写入的指令日期集合中
            # （前端允许 transaction_items 的 date <= as_of_date，因此不能只限定为 as_of_date/as_of_date+1）
            from datetime import datetime

            directive_dates = set()
            for d in directives:
                # 指令通常以 YYYY-MM-DD 开头
                if not d:
                    continue
                prefix = d.strip()[:10]
                try:
                    directive_dates.add(datetime.strptime(prefix, '%Y-%m-%d').date())
                except Exception:
                    continue

            # 兜底：至少包含 as_of_date 与 balance_date，避免解析异常导致空集合
            balance_date = as_of_date + timedelta(days=1)
            directive_dates.add(as_of_date)
            directive_dates.add(balance_date)

            our_entries = []
            for entry in entries:
                if not isinstance(entry, (Transaction, Pad, Balance)):
                    continue
                entry_date = getattr(entry, 'date', None)
                if entry_date not in directive_dates:
                    continue
                # 账户过滤：只提取本账户相关条目，避免同用户多账户同日对账导致混入
                if isinstance(entry, (Pad, Balance)):
                    if getattr(entry, 'account', None) != reconciliation_account:
                        continue
                elif isinstance(entry, Transaction):
                    # 对账条目：Transaction 有 "Beancount-Trans" 和 "对账调整"
                    # 且 postings 中必须包含对账账户（from_account）
                    has_reconciliation_account = any(
                        getattr(p, 'account', None) == reconciliation_account for p in getattr(entry, 'postings', []) or []
                    )
                    if not has_reconciliation_account:
                        continue

                # 对账条目：Transaction 需标识来自平台写入
                if isinstance(entry, Transaction):
                    if (getattr(entry, 'payee', None) != 'Beancount-Trans' or
                            getattr(entry, 'narration', None) != '对账调整'):
                        continue
                normalized = EntryMatcher.normalize_entry(entry)
                if normalized:
                    our_entries.append(normalized)
            # 取最后 N 条（本次写入的指令数 = 条目数）
            n = len(directives)
            if len(our_entries) >= n:
                our_entries = our_entries[-n:]
            serialized = []
            for norm in our_entries:
                ser = ReconciliationService._serialize_entry_for_storage(norm)
                serialized.append(ser)
            return serialized if serialized else None
        except Exception as e:
            logger.warning(f"提取对账条目失败: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _deserialize_entry_for_match(stored: Dict) -> Dict:
        """将存储的条目转为可与 Beancount 解析结果匹配的格式"""
        from datetime import datetime
        result = {}
        for k, v in stored.items():
            if k in ('date',) and isinstance(v, str):
                try:
                    result[k] = datetime.strptime(v, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    result[k] = v
            elif k == 'postings' and isinstance(v, list):
                new_postings = []
                for p in v:
                    if isinstance(p, dict):
                        np = {}
                        for pk, pv in p.items():
                            if pk in ('amount',) and isinstance(pv, str):
                                try:
                                    np[pk] = Decimal(pv)
                                except Exception:
                                    np[pk] = pv
                            else:
                                np[pk] = pv
                        new_postings.append(np)
                    else:
                        new_postings.append(p)
                result[k] = new_postings
            elif k == 'amount' and isinstance(v, str):
                try:
                    result[k] = Decimal(v)
                except Exception:
                    result[k] = v
            else:
                result[k] = v
        return result
    
    @staticmethod
    def revoke_reconciliation(task) -> Dict[str, Any]:
        """
        撤销对账：注释账本中的当次条目、更新任务状态、更新或新建待办。
        
        Args:
            task: 已完成的 ScheduledTask（对账类型）
            
        Returns:
            {'new_task_id': int, 'entries_commented': int, 'message': str}
        """
        from project.apps.reconciliation.models import ScheduledTask
        from project.apps.account.models import Account
        from .reconciliation_comment_service import ReconciliationCommentService
        
        account = task.content_object
        if not isinstance(account, Account):
            raise ValueError("待办任务关联的对象不是 Account 类型")
        if task.task_type != 'reconciliation' or task.status != 'completed':
            raise ValueError("只能撤销已完成的对账任务")

        # 只允许撤销最近一次：该账户下按 as_of_date 降序的第一条 completed 必须是当前 task
        content_type = ContentType.objects.get_for_model(Account)
        latest_completed = ScheduledTask.objects.filter(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            status='completed',
            as_of_date__isnull=False
        ).order_by('-as_of_date').first()
        if latest_completed and latest_completed.id != task.id:
            raise ValueError("仅支持撤销最近一次对账，更早的对账请在 Fava 中处理")

        user = account.owner
        today = date.today()
        entries_commented = 0
        message = ""
        
        # 1. 注释账本（基于内容匹配）
        if task.reconciliation_entries:
            platform_entries, entry_to_lines = ReconciliationCommentService._parse_reconciliation_bean(user)
            stored_entries = [
                ReconciliationService._deserialize_entry_for_match(e)
                for e in task.reconciliation_entries
            ]
            matched_pairs = EntryMatcher.match_entry_lists(stored_entries, platform_entries)
            all_line_numbers = []
            for _stored, _stored_idx, _platform_entry, platform_idx in matched_pairs:
                line_numbers = entry_to_lines.get(platform_idx, [])
                all_line_numbers.extend(line_numbers)
            unique_lines = sorted(set(all_line_numbers))
            reconciliation_path = BeanFileManager.get_reconciliation_bean_path(user)
            entries_commented = ReconciliationCommentService._comment_lines_in_file(
                reconciliation_path, unique_lines
            )
            message = f"已注释 {entries_commented} 行"
        else:
            message = "该记录未存储条目信息，无法自动注释账本，请手动检查 trans/reconciliation.bean"

        # 注释断言后，此前自动补的 pad 可能失去后续断言而变为无用 pad，一并清理
        ReconciliationCommentService.cleanup_unused_pads(user)

        # 2. 更新任务状态
        task.status = 'revoked'
        task.save(update_fields=['status', 'modified'])
        
        # 3. 更新或新建待办：保证永远只有一个 pending 待办且 scheduled_date 为当天，并关联被撤销任务
        existing_pending = ScheduledTask.objects.filter(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            status='pending'
        ).first()

        if existing_pending:
            existing_pending.scheduled_date = today
            existing_pending.revoked_task = task
            existing_pending.save(update_fields=['scheduled_date', 'revoked_task', 'modified'])
            new_task_id = existing_pending.id
        else:
            new_task = ScheduledTask.objects.create(
                task_type='reconciliation',
                content_type=content_type,
                object_id=account.id,
                scheduled_date=today,
                status='pending',
                revoked_task=task
            )
            new_task_id = new_task.id
        
        return {
            'new_task_id': new_task_id,
            'entries_commented': entries_commented,
            'message': message
        }
    
    @staticmethod
    def _generate_transaction_directive(
        user,
        from_account: str,
        to_account: str,
        amount: Decimal,
        currency: str,
        transaction_date: date
    ) -> str:
        """生成 transaction 指令
        
        如果目标账户不支持源货币，会自动选择合适的货币并使用 @@ 语法进行转换。
        
        示例（货币相同）：
        2026-01-20 * "Beancount-Trans" "对账调整"
          Expenses:Food 3.00 CNY
          Assets:Savings:Web:WechatFund
        
        示例（货币转换）：
        2026-01-19 * "Beancount-Trans" "对账调整"
          Expenses:Food:Dinner 68.00 CNY @@ 68.00 COIN
          Assets:Savings:Recharge:LiangLiangJiaDao
        
        Args:
            user: 用户对象（用于访问账本文件）
            from_account: 源账户（对账账户）
            to_account: 目标账户
            amount: 金额
            currency: 源货币（对账账户的货币）
            transaction_date: 交易日期
            
        Returns:
            Beancount transaction 指令字符串
        """
        # 获取目标账户的合适货币
        target_currency = AccountCurrencyService.select_currency_for_account(
            user, to_account, currency
        )
        
        # 如果目标货币与源货币相同，使用原有格式
        if target_currency == currency:
            return f'''{transaction_date} * "Beancount-Trans" "对账调整"
    {to_account} {amount} {currency}
    {from_account}'''
        
        # 如果目标货币与源货币不同，使用 @@ 语法进行转换
        # @@ 后面的金额使用绝对值
        return f'''{transaction_date} * "Beancount-Trans" "对账调整"
    {to_account} {amount} {target_currency} @@ {abs(amount)} {currency}
    {from_account}'''
    
    @staticmethod
    def _append_directives(user, directives: List[str]) -> Dict[str, Any]:
        """将指令追加到 trans/reconciliation.bean 文件
        
        对账指令作为交易记录，统一写入 trans/reconciliation.bean 文件。
        首次创建文件时，会自动添加到 trans/main.bean 的 include 列表中。
        
        Args:
            user: 用户对象
            directives: 指令列表
            
        Returns:
            dict: 包含文件路径和写入位置信息的字典，用于回滚
        """
        reconciliation_path = BeanFileManager.get_reconciliation_bean_path(user)
        
        # 确保 trans 目录存在
        trans_dir = os.path.dirname(reconciliation_path)
        os.makedirs(trans_dir, exist_ok=True)
        
        # 检查文件是否已存在（首次创建）
        is_new_file = not os.path.exists(reconciliation_path)
        
        # 记录写入前的位置（用于回滚）
        file_size_before = 0
        if os.path.exists(reconciliation_path):
            file_size_before = os.path.getsize(reconciliation_path)
        
        try:
            # 追加指令到文件末尾
            with open(reconciliation_path, 'a', encoding='utf-8') as f:
                if is_new_file:
                    # 首次创建时添加文件头注释
                    f.write("; Trans directory - Auto-generated includes\n")
                    f.write("; This file is automatically generated by the platform\n\n")
                
                for directive in directives:
                    f.write(directive)
                    f.write('\n')
                f.write('\n')
            
            # 如果是新文件，确保 trans/main.bean 包含它
            if is_new_file:
                BeanFileManager.ensure_reconciliation_bean_included(user)
                logger.info(f"已创建对账文件并添加到 trans/main.bean: {reconciliation_path}")
            
            logger.info(f"已写入 {len(directives)} 条对账指令到 {reconciliation_path}")
            
            # 返回文件信息，用于回滚
            return {
                'file_path': reconciliation_path,
                'file_size_before': file_size_before,
                'is_new_file': is_new_file
            }
        except IOError as e:
            logger.error(f"写入对账文件失败: {e}")
            raise ValueError(f"写入对账文件失败: {str(e)}")
    
    @staticmethod
    def _rollback_file_write(file_info: Dict[str, Any]):
        """回滚文件写入操作
        
        Args:
            file_info: 由 _append_directives 返回的文件信息字典
        """
        file_path = file_info['file_path']
        file_size_before = file_info['file_size_before']
        is_new_file = file_info['is_new_file']
        
        try:
            if is_new_file:
                # 如果是新文件，直接删除
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"已回滚：删除新创建的对账文件 {file_path}")
            else:
                # 如果是追加到现有文件，截断到写入前的大小
                if os.path.exists(file_path):
                    with open(file_path, 'r+', encoding='utf-8') as f:
                        f.truncate(file_size_before)
                    logger.info(f"已回滚：将对账文件截断到写入前的大小 {file_path}")
        except Exception as e:
            logger.error(f"回滚文件写入失败: {e}", exc_info=True)
            # 回滚失败不应该阻止异常传播，只记录错误
