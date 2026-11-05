# project/apps/account/signals.py
from django.dispatch import receiver
from allauth.account.signals import user_signed_up
from project.apps.account.models import AccountTemplate
import logging

logger = logging.getLogger(__name__)


@receiver(user_signed_up)
def apply_official_account_templates_on_signup(sender, request, user, **kwargs):
    """用户注册时自动应用官方账户模板"""
    apply_official_account_templates(user)


def apply_official_account_templates(user):
    """应用官方账户模板到用户"""
    from project.apps.account.models import Account, is_valid_beancount_account_path

    # 获取所有官方账户模板
    official_templates = AccountTemplate.objects.filter(is_official=True)

    logger.info(f"为用户 {user.username} 应用 {official_templates.count()} 个官方账户模板")

    for template in official_templates:
        try:
            # 使用合并方式，跳过冲突
            for item in template.items.all():
                # Beancount 不允许根账户，模板中的根账户只作分组
                if not is_valid_beancount_account_path(item.account_path):
                    continue

                # 检查是否已存在相同路径的账户
                existing = Account.objects.filter(owner=user, account=item.account_path).first()

                if not existing:
                    # 创建新账户（Account.save() 会自动创建父账户）
                    Account.objects.create(
                        owner=user,
                        account=item.account_path,
                        enable=item.enable,
                        reconciliation_rule=item.reconciliation_rule,
                        description=item.description or ''
                    )
                    logger.debug(f"为用户 {user.username} 创建账户: {item.account_path}")
        except Exception as e:
            logger.error(f"应用账户模板 {template.name} 失败: {str(e)}")

