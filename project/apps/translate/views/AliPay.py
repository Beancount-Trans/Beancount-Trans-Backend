# project/apps/translate/views/AliPay.py
import re
# import logging

from typing import Dict, Optional

from project.apps.translate.utils import ASSETS_OTHER, BILL_ALI, EXPENSES_OTHER, pattern

ALIPAY_FALLBACK_PAYMENT_MARKERS = (
    "亲情卡",
    "他人代付账户",
    "已购买商品抵扣",
)


def alipay_uses_fallback_payment_method(payment_method: str) -> bool:
    pm = payment_method or ""
    return any(marker in pm for marker in ALIPAY_FALLBACK_PAYMENT_MARKERS)


ALIPAY_PAYMENT_STATUSES = frozenset({
    "交易成功",
    "支付成功",
    "代付成功",
    "还款成功",
    "等待确认收货",
    "缴费中",
    "冻结成功",
    "放款成功",
})


def alipay_is_refund_row(row: Dict) -> bool:
    return (
        row.get("bill_identifier") == BILL_ALI
        and row.get("transaction_status") == "退款成功"
    )


def alipay_is_payment_row(row: Dict) -> bool:
    return (
        row.get("bill_identifier") == BILL_ALI
        and row.get("transaction_status") in ALIPAY_PAYMENT_STATUSES
    )


def alipay_parent_uuid(row: Dict) -> Optional[str]:
    uid = (row.get("uuid") or "").strip()
    if "_" not in uid:
        return None
    return uid.split("_", 1)[0]


# alipay_csvfile_identifier = "------------------------------------------------------------------------------------"

# class AliPayInitStrategy(InitStrategy):
#     def init(self, bill, **kwargs):
#         import itertools
#         bill = itertools.islice(bill, 24, None)  # 跳过前24行
#         records = []

#         try:
#             for row in bill:
#                 transaction_type = "/" if row[5] == "不计收支" else row[5]
#                 payment_method = "余额" if row[7].strip() == '' else row[7].strip()
#                 notes = "/" if row[11].strip() == '' else row[11].strip()

#                 record = {
#                     'transaction_time': row[0].strip(),  # 交易时间
#                     'transaction_category': row[1].strip(),  # 交易类型
#                     'counterparty': row[2].strip(),  # 交易对方
#                     'commodity': row[4].strip(),  # 商品
#                     'transaction_type': transaction_type.strip(),  # 收支类型（收入/支出/不计收支）
#                     'amount': row[6].strip(),  # 金额
#                     'payment_method': payment_method.split('&')[0],  # 支付方式
#                     'transaction_status': row[8].strip(),  # 交易状态
#                     'notes': notes,  # 备注
#                     'bill_identifier': BILL_ALI,  # 账单类型
#                     'uuid': row[9].strip(),  # 交易单号
#                     'discount': True if "&" in payment_method else False  # 支付方式
#                 }
#                 records.append(record)

#         except UnicodeDecodeError as e:
#             logging.error(f"Unicode decode error at row={row}: {str(e)}")
#         except Exception as e:
#             logging.error(f"Unexpected error: {str(e)}")

#         return records


# def alipay_ignore(self, data):
#     if data['bill_identifier'] == BILL_ALI and data['transaction_status'] in ["退款成功", "交易关闭", "解冻成功", "信用服务使用成功", "已关闭", "还款失败", "等待付款", "芝麻免押下单成功"]:
#         return True
#     elif data['bill_identifier'] == BILL_ALI and  re.match(pattern["余额宝"], data['commodity']):
#         return True
#     else:
#         return False

# def alipay_fund_ignore(self, data):
#     if data['bill_identifier'] == BILL_ALI:
#         return data['transaction_category'] in ["转账收款到余额宝", "余额宝-自动转入", "余额宝-单次转入"]


def alipay_get_expense_account(self, assets, ownerid):
    from project.apps.translate.services.account_resolution import resolve_payment_account

    # 未匹配的付款方式不能静默记到支付宝余额：既会掩盖问题，也会让资产重复计算。
    # 落到 Assets:Other 后，审核页可提示，也能触发资产账户自动补齐。
    return resolve_payment_account(
        ownerid,
        self.key,
        bill=self.bill,
        fallback=ASSETS_OTHER,
        mappings=self._asset_mappings,
    )


def alipay_get_income_account(self, assets, ownerid):
    from project.apps.translate.services.account_resolution import resolve_payment_account

    return resolve_payment_account(
        ownerid,
        self.key,
        bill=self.bill,
        fallback=ASSETS_OTHER,
        mappings=self._asset_mappings,
    )


def alipay_get_balance_account(self, data, assets, ownerid, fallback_account='Equity:Adjustments'):
    from project.apps.translate.services.account_resolution import resolve_payment_account

    # account = "Unknown-Account"  # 方便排查问题
    account = self.account
    if self.type == "转账收款到余额宝":
        account = assets["ALIPAY"]
    elif self.type == "余额宝-自动转入":
        account = assets["ALIPAY"]
    elif self.type == "余额宝-转出到余额":
        account = assets["ALIFUND"]
    elif self.type == "余额宝-单次转入":
        account = resolve_payment_account(
            ownerid,
            data['payment_method'],
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    elif self.type == "余额宝-转出到银行卡":
        account = assets["ALIFUND"]
    elif self.type == "充值-普通充值":
        account = ASSETS_OTHER  # 支付宝账单中银行卡充值到余额时没有任何银行的信息，需要手动对账
    elif self.type == "提现-实时提现" or self.type == "提现-快速提现":  # 提现到支付宝余额（交易对方信息由对端分支解析）
        account = assets["ALIPAY"]
    elif alipay_uses_fallback_payment_method(data['payment_method']):
        account = fallback_account
    else:
        # 提现最大颗粒度只到具体银行，同银行多张卡时退回 Assets:Other 人工对账
        account = resolve_payment_account(
            ownerid,
            data['payment_method'],
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    return account


def alipay_get_balance_expense(self, data, assets, ownerid):
    # expend = "Unknown-Expend"  # 方便排查问题
    expend = self.expend
    if data.get("transaction_status") == "退款成功" or (
        isinstance(self.type, str) and "退款" in self.type
    ):
        return EXPENSES_OTHER
    if self.type == "转账收款到余额宝":
        expend = assets["ALIFUND"]
    elif self.type == "余额宝-自动转入":
        expend = assets["ALIFUND"]
    elif self.type == "余额宝-转出到余额":
        expend = assets["ALIPAY"]
    elif self.type == "余额宝-单次转入":
        expend = assets["ALIFUND"]
    elif self.type == "余额宝-转出到银行卡":
        from project.apps.translate.services.account_resolution import resolve_payment_account

        expend = resolve_payment_account(
            ownerid,
            data['payment_method'],
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    elif self.type == "充值-普通充值":
        expend = assets["ALIPAY"]  # 支付宝账单中银行卡充值到余额时没有任何银行的信息，需要手动对账
    elif self.type == "提现-实时提现" or self.type == "提现-快速提现":
        from project.apps.translate.services.account_resolution import resolve_account_from_text

        result = resolve_account_from_text(ownerid, data['counterparty'] + "储蓄卡", bill=self.bill)
        expend = result.account if result.ok else ASSETS_OTHER
    elif re.match(pattern["花呗主动还款"], self.type) or re.match(pattern["花呗自动还款"], self.type):  # 账单类型匹配"花呗主动还款-2022年09月账单"
        expend = assets["HUABEI"]
    elif ("蚂蚁借呗放款至银行卡" in self.type) or ("蚂蚁借呗还款" in self.type):
        expend = assets["JIEBEI"]
    elif ("备用金取出至余额" in self.type) or ("备用金归还" in self.type):
        expend = assets["BEIYONGJIN"]
    elif self.type == "信用卡还款":
        from project.apps.translate.services.account_resolution import resolve_account_from_text

        result = resolve_account_from_text(ownerid, data['counterparty'] + "信用卡", bill=self.bill)
        expend = result.account if result.ok else ASSETS_OTHER
    elif re.match(pattern["基金"], self.type):
        from project.apps.translate.services.account_resolution import resolve_payment_account

        result = data['commodity'][data['commodity'].index("卖出至") + len("卖出至"):]
        expend = resolve_payment_account(
            ownerid,
            result,
            bill=self.bill,
            fallback=ASSETS_OTHER,
            mappings=self._asset_mappings,
        )
    else:
        expend = ASSETS_OTHER
    return expend


def alipay_get_type(data):
    return data['commodity']


def alipay_initalize_key(data):
    key = data['payment_method']
    if "&" in key:  # 用于解决支付宝中"&[红包]"导致无法被匹配的问题
        sub_strings = key.split("&")
        key = sub_strings[0]
    return key


def alipay_get_uuid(data):
    return data['uuid']


def alipay_get_status(data):
    return "ALiPay - " + data['transaction_status']


def alipay_get_amount(data):
    return "{:.2f}".format(float(data['amount']))  # 支付宝账单格式为"10.00"，直接以数字形式返回即可


def alipay_get_note(data):
    if data['commodity'] == "Transfer":
        return "Transfer(无备注转账)"
    elif data['commodity'] == "发普通红包":
        return "发普通红包"
    elif data['commodity'] == "收到普通红包":
        return "收到普通红包"
    return data['commodity']


def alipay_get_tag(data):
    pass


def alipay_get_commission(data):
    return data['notes'][4:]


def alipay_get_discount(data):
    return data['discount']


# IgnoreData.alipay_ignore = alipay_ignore
# IgnoreData.alipay_fund_ignore = alipay_fund_ignore