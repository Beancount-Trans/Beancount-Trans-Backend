# project/apps/translate/views/WeChat.py
# import logging
import re

from project.apps.translate.utils import ASSETS_OTHER, BILL_WECHAT

# wechat_csvfile_identifier = "微信支付账单明细"


# class WeChatInitStrategy(InitStrategy):
#     def init(self, bill, **kwargs):
#         import itertools
#         bill = itertools.islice(bill, 16, None)
#         records = []
#         try:
#             for row in bill:
#                 record = {
#                     'transaction_time': row[0],  # 交易时间
#                     'transaction_category': row[1],  # 交易类型
#                     'counterparty': row[2],  # 交易对方
#                     'commodity': row[3],  # 商品
#                     'transaction_type': row[4],  # 收支类型（收入/支出/不计收支）
#                     'amount': row[5],  # 金额
#                     'payment_method': row[6],  # 支付方式
#                     'transaction_status': row[7],  # 交易状态
#                     'notes': row[10],  # 备注
#                     'bill_identifier': BILL_WECHAT,  # 账单类型
#                     'uuid': row[8],  # 交易单号
#                     'discount': False
#                 }
#                 records.append(record)
#         except UnicodeDecodeError as e:
#             logging.error("Unicode decode error at row=%s: %s", row, e)
#         except Exception as e:
#             logging.error("Unexpected error: %s", e)

#         return records

def wechatpay_string_convert_to_csv(data):
    """接收字符串，返回CSV格式文件

    Args:
        data (string): _description_
        card_number (string): 储蓄卡/信用卡 完整的卡号

    Returns:
        csv: _description_
    """
    pass

def wechatpay_ignore(self, data):
    if data["bill_identifier"] == BILL_WECHAT:
        return data["transaction_status"] in ["已全额退款", "对方已退还"]
    # data["transaction_status"].startswith("已退款")


def wechatpay_get_expense_account(self, assets, ownerid):
    from project.apps.translate.services.account_resolution import resolve_payment_account

    return resolve_payment_account(
        ownerid,
        self.key,
        bill=self.bill,
        fallback=ASSETS_OTHER,
        mappings=self._asset_mappings,
    )


def wechatpay_get_income_account(self, assets, ownerid):
    from project.apps.translate.services.account_resolution import resolve_payment_account

    key = "零钱通" if self.status == "已转入零钱通" else self.key
    return resolve_payment_account(
        ownerid,
        key,
        bill=self.bill,
        fallback=ASSETS_OTHER,
        mappings=self._asset_mappings,
    )


def wechatpay_get_balance_account(self, data, assets, ownerid):
    from project.apps.translate.services.account_resolution import (
        resolve_account_from_text,
        resolve_payment_account,
    )

    # account = "Unknown-Account"  # 方便排查问题
    account = self.account
    if self.type == "零钱提现":
        account = resolve_payment_account(
            ownerid,
            data['counterparty'],
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    elif self.type == "零钱充值":
        account = assets["WECHATPAY"]
    elif self.type == "零钱通转出":
        index = data['transaction_category'].find("到")
        account = resolve_payment_account(
            ownerid,
            data['transaction_category'][index + 1:],  # 例如"建设银行(5522)"
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    elif self.type == "转入零钱通":
        account = assets["WECHATFUND"]
    elif self.type == "信用卡还款":
        account = resolve_payment_account(
            ownerid,
            data['payment_method'],
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    elif self.type == "购买理财通":
        result = resolve_account_from_text(
            ownerid, data['payment_method'] + "储蓄卡", bill=self.bill
        )
        account = result.account if result.ok else ASSETS_OTHER
    return account


def wechatpay_get_balance_expense(self, data, assets, ownerid):
    # expend = "Unknown-Expend"  # 方便排查问题
    expend = self.expend
    if self.type == "零钱提现":
        expend = assets["WECHATPAY"]
    elif self.type == "零钱充值":
        from project.apps.translate.services.account_resolution import resolve_payment_account

        expend = resolve_payment_account(
            ownerid,
            data['counterparty'],
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    elif self.type == "零钱通转出":
        expend = assets["WECHATFUND"]
    elif self.type == "转入零钱通":
        from project.apps.translate.services.account_resolution import resolve_payment_account

        index = data['transaction_category'].find("来自")
        expend = resolve_payment_account(
            ownerid,
            data['transaction_category'][index + 2:],  # 例如"建设银行(5522)"
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    elif self.type == "信用卡还款":
        from project.apps.translate.services.account_resolution import resolve_account_from_text

        result = resolve_account_from_text(
            ownerid,
            data['counterparty'][:data['counterparty'].index("还款")],
            bill=self.bill,
        )
        expend = result.account if result.ok else ASSETS_OTHER
    elif self.type == "购买理财通":
        expend = ASSETS_OTHER
    return expend


def wechatpay_get_type(data):
    return data['transaction_category'][:5]


def wechatpay_initalize_key(self, data):
    return data['payment_method']


def wechatpay_get_uuid(data):
    return data['uuid'].rstrip("\t")


def wechatpay_get_status(data):
    return "WeChat - " + data['transaction_status']


def wechatpay_get_amount(data):
    """微信金额列：旧版 CSV 多为 ¥/￥ 前缀；新版 xlsx 导出常为纯数字字符串。"""
    raw = str(data.get("amount", "") or "").strip().strip("\t").strip('"').replace("\ufeff", "")
    if not raw:
        return "0.00"
    if raw[0] in "¥￥":
        raw = raw[1:].strip()
    return "{:.2f}".format(float(raw))


def wechatpay_get_note(data):
    if data['transaction_type'] == "/":  # 收支为/时，备注为交易类型
        return data['transaction_category']
    return data['commodity']


def wechatpay_get_tag(data):
    import re
    from project.apps.translate.services.beancount_header_tags import is_valid_beancount_tag_path

    notes = wechatpay_get_note(data)
    tag_match = re.search(r'(?:^|\s)(#\S+)', notes or '')
    if tag_match:
        path = tag_match.group(1).lstrip('#')
        if is_valid_beancount_tag_path(path):
            return tag_match.group(1).strip()
    caret_match = re.search(r'(?:^|\s)(\^\S+)', notes or '')
    if caret_match:
        return caret_match.group(1).strip()
    return None


def wechatpay_get_commission(data):
    return data['notes'][4:]


def wechatpay_get_discount(data):
    return data['discount']


# IgnoreData.wechatpay_ignore = wechatpay_ignore