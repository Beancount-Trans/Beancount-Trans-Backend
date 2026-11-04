import re
from django.core.validators import RegexValidator
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from project.models import BaseModel


def _is_valid_account_part(part: str) -> bool:
    """
    验证账户路径的每个部分是否有效
    
    允许：字母、数字、连字符（-）
    不允许：以连字符开头或结尾，不能为空
    
    Args:
        part: 账户路径的一部分（用冒号分隔后的单个部分）
    
    Returns:
        bool: 是否有效
    """
    if not part:
        return False
    # 允许字母、数字和连字符，但不能以连字符开头或结尾
    return bool(re.match(r'^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$', part))


class Account(BaseModel):
    account = models.CharField(max_length=128, help_text="账户路径", verbose_name="账户")
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children', help_text="父账户", verbose_name="父账户")
    owner = models.ForeignKey(User, related_name='accounts', on_delete=models.CASCADE, db_index=True, help_text="属主", verbose_name="属主")
    enable = models.BooleanField(default=True,verbose_name="是否启用",help_text="启用状态")
    
    # 对账周期配置（受控自然语言循环规则，如 every month on the last）
    reconciliation_rule = models.CharField(
        max_length=128,
        blank=True,
        default='',
        verbose_name="对账周期规则",
        help_text="受控自然语言循环规则，如 every month on the last"
    )
    description = models.CharField(max_length=256, blank=True, default='', verbose_name='描述', help_text='描述')

    class Meta:
        unique_together = ['account', 'owner']
        ordering = ['account']
        verbose_name = '账本账户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.account

    def clean(self):
        if not all(_is_valid_account_part(part) for part in self.account.split(':')):
            raise ValidationError("账户路径必须由字母、数字和连字符组成，用冒号分隔，不能以连字符开头或结尾")

        # 验证根账户类型
        root = self.account.split(':')[0]
        valid_roots = ['Assets', 'Liabilities', 'Equity', 'Income', 'Expenses']
        if root not in valid_roots:
            raise ValidationError(f"根账户必须是以下之一: {', '.join(valid_roots)}")

        # 验证对账周期规则（受控自然语言 DSL）
        if self.reconciliation_rule:
            from project.apps.reconciliation.services.recurrence import (
                RecurrenceParser,
                RecurrenceParseError,
            )
            try:
                RecurrenceParser.parse(self.reconciliation_rule)
            except RecurrenceParseError as e:
                raise ValidationError(f"对账周期规则无效：{e}")

    def save(self, *args, **kwargs):
        """保存账户时自动创建父账户，并同步映射状态"""
        # 检查是否是新创建的账户（在 save 之前检查）
        is_new_account = self.pk is None
        
        # 检查enable字段是否发生变化
        enable_changed = False
        account_name_changed = False
        old_account_name = None

        if self.pk:
            try:
                old_instance = Account.objects.get(pk=self.pk)
                enable_changed = old_instance.enable != self.enable
                account_name_changed = old_instance.account != self.account
                if account_name_changed:
                    old_account_name = old_instance.account
            except Account.DoesNotExist:
                pass

        # 始终根据新的账户路径计算并设置父账户
        if ':' in self.account:
            parent_account_path = ':'.join(self.account.split(':')[:-1])
            try:
                # 尝试获取已存在的父账户
                new_parent = Account.objects.get(account=parent_account_path, owner=self.owner)
                # 关键修复：防止账户成为自己的父账户（防止循环引用）
                if new_parent.pk == self.pk:
                    raise ValidationError("账户不能成为自己的父账户")
                # 只有当父账户确实需要改变时才更新
                if self.parent != new_parent:
                    self.parent = new_parent
            except Account.DoesNotExist:
                # 自动创建父账户
                self.parent = self._create_parent_account(parent_account_path)
        else:
            # 根级账户，没有父账户
            self.parent = None

        super().save(*args, **kwargs)

        # 如果enable状态发生变化，同步更新相关映射和待办任务
        if enable_changed:
            self._sync_mappings_enable_status()
            # 如果账户被禁用，取消所有相关的待办任务
            if not self.enable:
                self._cancel_pending_tasks()
            # 如果账户被启用，且配置了对账周期，创建待办任务
            elif self.enable:
                self._create_reconciliation_task_if_needed()
            # 启用/关闭状态同步到账本（以 note 记录，可反复切换）
            if not getattr(self, '_skip_ledger_sync', False):
                self._sync_ledger_state()
        
        # 如果是新创建的账户，且启用且配置了对账周期，创建待办任务
        if is_new_account and self.enable:
            self._create_reconciliation_task_if_needed()

        # 如果账户名称发生变化，同步更新所有子账户的名称
        if account_name_changed and old_account_name:
            self._update_children_account_names(old_account_name)

        # 新账户同步写入账本 open（自动创建的父账户通过 _skip_ledger_sync 跳过）
        if is_new_account and not getattr(self, '_skip_ledger_sync', False):
            self._sync_ledger_open()
            # 创建时即禁用的账户，补一条关闭状态记录
            if not self.enable:
                self._sync_ledger_state()

    def _create_parent_account(self, parent_account_path):
        """递归创建父账户"""
        try:
            # 尝试获取父账户
            parent = Account.objects.get(account=parent_account_path, owner=self.owner)
            return parent
        except Account.DoesNotExist:
            # 如果父账户不存在，递归创建
            if ':' in parent_account_path:
                # 还有更上级的父账户，先创建上级父账户
                grandparent_path = ':'.join(parent_account_path.split(':')[:-1])
                grandparent = self._create_parent_account(grandparent_path)

                # 创建当前父账户（父账户不写入账本）
                parent = Account(
                    account=parent_account_path,
                    owner=self.owner,
                    parent=grandparent,
                    enable=True
                )
                parent._skip_ledger_sync = True
                parent.save()
            else:
                # 这是根级账户，直接创建（父账户不写入账本）
                parent = Account(
                    account=parent_account_path,
                    owner=self.owner,
                    enable=True
                )
                parent._skip_ledger_sync = True
                parent.save()

            return parent

    def _update_children_account_names(self, old_account_name):
        """
        当父账户名称发生变化时，同步更新所有子账户的名称

        Args:
            old_account_name: 旧的账户名称
        """
        try:
            # 获取所有子账户（包括子账户的子账户）
            children = self.children.all()

            for child in children:
                # 关键修复：避免更新自己（防止递归更新）
                if child.pk == self.pk:
                    continue
                    
                old_child_name = child.account
                # 检查子账户名称是否以旧父账户名称为前缀
                if old_child_name.startswith(old_account_name + ':'):
                    # 替换前缀部分
                    new_child_name = self.account + old_child_name[len(old_account_name):]
                    child.account = new_child_name
                    # 递归保存，这会触发子账户的子账户也进行更新
                    child.save()

        except Exception as e:
            # 记录错误但不阻止父账户保存
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"更新子账户名称失败: {str(e)}")

    def _sync_mappings_enable_status(self):
        """同步映射的启用状态与账户状态"""
        # 避免循环导入，使用字符串引用模型
        from django.apps import apps

        try:
            # 获取映射模型
            Expense = apps.get_model('maps', 'Expense')
            Assets = apps.get_model('maps', 'Assets')
            Income = apps.get_model('maps', 'Income')

            # 同步所有相关映射的启用状态
            Expense.objects.filter(expend=self).update(enable=self.enable)
            Assets.objects.filter(assets=self).update(enable=self.enable)
            Income.objects.filter(income=self).update(enable=self.enable)

        except Exception as e:
            # 记录错误但不阻止账户保存
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"同步映射状态失败: {str(e)}")

    def _sync_ledger_open(self):
        """新账户同步写入账本 open（含占位 note）"""
        import logging

        logger = logging.getLogger(__name__)

        try:
            from project.apps.account.ledger_sync import sync_open

            written = sync_open(self)
            if written:
                logger.info(f"账户 {self.account} 已同步账本创建状态")
        except Exception as e:
            # 记录错误但不阻止账户保存
            logger.error(f"同步账户 open 到账本失败: {str(e)}")

    def _sync_ledger_close(self):
        """删除账户时同步写入账本 close"""
        import logging

        logger = logging.getLogger(__name__)

        try:
            from project.apps.account.ledger_sync import sync_close

            written = sync_close(self)
            if written:
                logger.info(f"账户 {self.account} 已同步账本 close")
        except Exception as e:
            # 记录错误但不阻止账户操作
            logger.error(f"同步账户 close 到账本失败: {str(e)}")

    def _sync_ledger_state(self):
        """把启用/关闭状态以 note 形式同步到账本"""
        import logging

        logger = logging.getLogger(__name__)

        try:
            from project.apps.account.ledger_sync import (
                STATE_NOTE_DISABLED,
                STATE_NOTE_ENABLED,
                sync_state,
            )

            state_text = STATE_NOTE_ENABLED if self.enable else STATE_NOTE_DISABLED
            written = sync_state(self, state_text)
            if written:
                logger.info(f"账户 {self.account} 已同步状态「{state_text}」到账本")
        except Exception as e:
            # 记录错误但不阻止账户操作
            logger.error(f"同步账户状态到账本失败: {str(e)}")

    def _create_reconciliation_task_if_needed(self):
        """如果需要，创建对账待办任务
        
        检查账户是否启用、是否配置了对账周期，以及是否已存在待执行的待办任务。
        如果满足条件，创建首个待办任务。
        """
        from datetime import date
        from django.contrib.contenttypes.models import ContentType
        from project.apps.reconciliation.models import ScheduledTask
        import logging
        
        logger = logging.getLogger(__name__)
        
        # 检查账户是否启用
        if not self.enable:
            return
        
        # 检查是否配置了对账周期规则
        if not self.reconciliation_rule:
            return
        
        try:
            account_content_type = ContentType.objects.get_for_model(Account)
            
            # 检查是否已存在待执行的待办任务（避免重复创建）
            existing_task = ScheduledTask.objects.filter(
                task_type='reconciliation',
                content_type=account_content_type,
                object_id=self.id,
                status='pending'
            ).exists()
            
            if existing_task:
                # 已存在待办任务，不重复创建
                return
            
            # 创建首个待办任务，执行日期为今日
            ScheduledTask.objects.create(
                task_type='reconciliation',
                content_type=account_content_type,
                object_id=self.id,
                scheduled_date=date.today(),
                status='pending'
            )
            
            logger.info(f"账户 {self.account} 已创建对账待办任务")
        except Exception as e:
            # 记录错误但不阻止账户保存
            logger.error(f"创建对账待办任务失败: {str(e)}")

    def _cancel_pending_tasks(self):
        """取消账户相关的待办任务"""
        from django.contrib.contenttypes.models import ContentType
        from project.apps.reconciliation.models import ScheduledTask
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            account_content_type = ContentType.objects.get_for_model(Account)
            cancelled_count = ScheduledTask.objects.filter(
                task_type='reconciliation',
                content_type=account_content_type,
                object_id=self.id,
                status='pending'
            ).update(status='cancelled')
            
            if cancelled_count > 0:
                logger.info(f"账户 {self.account} 关闭，已取消 {cancelled_count} 个待办任务")
        except Exception as e:
            # 记录错误但不阻止账户保存
            logger.error(f"取消待办任务失败: {str(e)}")

    def has_children(self):
        """检查是否存在子账户"""
        return self.children.exists()

    def get_account_type(self):
        """获取账户类型"""
        root = self.account.split(':')[0]
        type_mapping = {
            'Assets': '资产账户',
            'Liabilities': '负债账户', 
            'Equity': '权益账户',
            'Income': '收入账户',
            'Expenses': '支出账户'
        }
        return type_mapping.get(root, '未知类型')

    def close(self, migrate_to=None):
        """
        关闭账户，并处理所有相关的映射

        Args:
            migrate_to: 迁移目标账户，如果提供，则将相关映射迁移到此账户
        Returns:
            dict: 包含操作结果的字典
        """
        # 避免循环导入，使用字符串引用模型
        from django.apps import apps

        # 获取映射模型
        Expense = apps.get_model('maps', 'Expense')
        Assets = apps.get_model('maps', 'Assets')
        Income = apps.get_model('maps', 'Income')

        result = {
            'account_closed': True,
            'has_children': self.has_children(),
            'mappings_migrated': False,
            'mappings_disabled': False,
            'migrated_to': None,
            'mapping_counts': {
                'expense': 0,
                'assets': 0,
                'income': 0,
                'total': 0
            }
        }

        # 统计当前映射数量
        expense_count = Expense.objects.filter(expend=self).count()
        assets_count = Assets.objects.filter(assets=self).count()
        income_count = Income.objects.filter(income=self).count()
        total_mappings = expense_count + assets_count + income_count

        result['mapping_counts'] = {
            'expense': expense_count,
            'assets': assets_count,
            'income': income_count,
            'total': total_mappings
        }

        # 处理映射迁移
        if migrate_to:
            # 验证目标账户
            if not migrate_to.enable:
                raise ValidationError("目标账户已禁用，无法进行迁移")

            # 迁移所有类型的映射到目标账户
            Expense.objects.filter(expend=self).update(expend=migrate_to)
            Assets.objects.filter(assets=self).update(assets=migrate_to)
            Income.objects.filter(income=self).update(income=migrate_to)

            result['mappings_migrated'] = True
            result['migrated_to'] = migrate_to.id
        else:
            # 禁用所有相关映射
            Expense.objects.filter(expend=self).update(enable=False)
            Assets.objects.filter(assets=self).update(enable=False)
            Income.objects.filter(income=self).update(enable=False)

            result['mappings_disabled'] = True

        # 取消所有相关的待办任务
        self._cancel_pending_tasks()

        # 关闭当前账户（不关闭子账户）；save() 会同步账本状态 note
        self.enable = False
        self.save()

        return result

    def delete_with_migration(self, migrate_to=None):
        """
        删除账户，并处理所有相关的映射

        Args:
            migrate_to: 迁移目标账户，可选，用于迁移相关映射
        Returns:
            dict: 包含操作结果的字典
        """
        # 检查是否有子账户
        if self.has_children():
            raise ValidationError("存在子账户，无法删除。请先处理子账户")

        # 避免循环导入，使用字符串引用模型
        from django.apps import apps

        # 获取映射模型
        Expense = apps.get_model('maps', 'Expense')
        Assets = apps.get_model('maps', 'Assets')
        Income = apps.get_model('maps', 'Income')

        # 统计映射数量
        expense_count = Expense.objects.filter(expend=self).count()
        assets_count = Assets.objects.filter(assets=self).count()
        income_count = Income.objects.filter(income=self).count()
        total_mappings = expense_count + assets_count + income_count

        result = {
            'account_deleted': True,
            'mappings_migrated': False,
            'migrated_to': None,
            'mapping_counts': {
                'expense': expense_count,
                'assets': assets_count,
                'income': income_count,
                'total': total_mappings
            }
        }

        # 如果有映射数据，必须提供迁移目标
        if total_mappings > 0:
            if not migrate_to:
                raise ValidationError("账户存在映射数据，删除时必须提供迁移目标账户")

            # 验证目标账户
            if not migrate_to.enable:
                raise ValidationError("目标账户已禁用，无法进行迁移")

            if migrate_to == self:
                raise ValidationError("不能将账户迁移到自身")

            # 迁移所有类型的映射到目标账户
            Expense.objects.filter(expend=self).update(expend=migrate_to)
            Assets.objects.filter(assets=self).update(assets=migrate_to)
            Income.objects.filter(income=self).update(income=migrate_to)

            result['mappings_migrated'] = True
            result['migrated_to'] = migrate_to.id

        # 删除账户（先同步账本 close，保留历史）
        self._sync_ledger_close()

        account_id = self.id
        account_name = self.account
        self.delete()

        result['deleted_account'] = {
            'id': account_id,
            'name': account_name
        }

        return result

    def has_reconciliation_cycle(self) -> bool:
        """检查是否配置了对账周期规则"""
        return bool(self.reconciliation_rule)

    def get_reconciliation_cycle_display(self) -> str:
        """获取对账周期的显示文本（由循环规则生成中文描述）"""
        if not self.has_reconciliation_cycle():
            return "未设置"
        from project.apps.reconciliation.services.recurrence import (
            RecurrenceParser,
            RecurrenceParseError,
        )
        try:
            return RecurrenceParser.parse(self.reconciliation_rule).describe()
        except RecurrenceParseError:
            return self.reconciliation_rule

    def is_first_reconciliation(self) -> bool:
        """检查是否首次对账（该账户是否已完成过对账任务）"""
        from django.contrib.contenttypes.models import ContentType
        from project.apps.reconciliation.models import ScheduledTask
        
        content_type = ContentType.objects.get_for_model(Account)
        has_completed = ScheduledTask.objects.filter(
            task_type='reconciliation',
            content_type=content_type,
            object_id=self.id,
            status='completed'
        ).exists()
        
        return not has_completed


class AccountTemplate(BaseModel):
    """账户模板"""
    name = models.CharField(max_length=32, null=False, help_text="模板名称")
    description = models.TextField(blank=True, help_text="模板描述")
    is_public = models.BooleanField(default=False, help_text="是否公开")
    is_official = models.BooleanField(default=False, help_text="是否官方")
    version = models.CharField(max_length=16, blank=True, default="1.0.0", help_text="版本号")
    update_notes = models.TextField(null=True, blank=True, help_text="更新说明")
    owner = models.ForeignKey(User, related_name='account_templates', on_delete=models.CASCADE)

    class Meta:
        db_table = 'account_template'
        verbose_name = '账户模板'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'owner'],
                name='unique_account_template_per_user'
            )
        ]

    def __str__(self):
        return self.name


class AccountTemplateItem(BaseModel):
    """账户模板项"""
    template = models.ForeignKey(AccountTemplate, related_name='items', on_delete=models.CASCADE)
    account_path = models.CharField(max_length=128, null=False, help_text="账户路径")
    enable = models.BooleanField(default=True, help_text="默认启用状态")
    
    # 对账周期配置（受控自然语言循环规则）
    reconciliation_rule = models.CharField(
        max_length=128,
        blank=True,
        default='',
        verbose_name="对账周期规则",
        help_text="受控自然语言循环规则，如 every month on the last"
    )
    description = models.CharField(max_length=256, blank=True, default='', verbose_name='描述', help_text='描述')

    class Meta:
        db_table = 'account_template_item'
        verbose_name = '账户模板项'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=['template', 'account_path'],
                name='unique_account_per_template'
            )
        ]

    def clean(self):
        """验证对账周期规则"""
        if self.reconciliation_rule:
            from project.apps.reconciliation.services.recurrence import (
                RecurrenceParser,
                RecurrenceParseError,
            )
            try:
                RecurrenceParser.parse(self.reconciliation_rule)
            except RecurrenceParseError as e:
                raise ValidationError(f"对账周期规则无效：{e}")

    def __str__(self):
        return f"{self.template.name} - {self.account_path}"
