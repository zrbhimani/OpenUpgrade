# -*- coding: utf-8 -*-
import pytz
import re
from nameparser.parser import HumanName
from datetime import datetime

from Crypto.Cipher import AES
import base64


def convert_tz(given_date, tz_name):
    if not tz_name:
        return given_date
    return pytz.timezone('UTC').localize(given_date, is_dst=False).astimezone(pytz.timezone(tz_name))


def utc_to_nz(dt, format=False):
    if type(dt) in [str, unicode]:
        dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
    dt = pytz.utc.localize(dt, is_dst=False).astimezone(pytz.timezone('Pacific/Auckland'))
    if format:
        return dt.strftime(format)
    else:
        return dt


def convert_to_utc(given_date, tz_name):
    if not tz_name:
        return given_date
    return pytz.timezone(tz_name).localize(given_date, is_dst=False).astimezone(pytz.timezone('UTC'))


def ean_checksum(ean):
    code = list(ean)
    if len(code) != 13:
        return -1

    oddsum = evensum = total = 0
    code = code[:-1]  # Remove checksum
    for i in range(len(code)):
        if i % 2 == 0:
            evensum += int(code[i])
        else:
            oddsum += int(code[i])
    total = oddsum * 3 + evensum
    return int((10 - total % 10) % 10)


def check_ean(ean):
    return re.match("^\d+$", ean) and ean_checksum(ean) == int(ean[-1])


def strip_address(vals):
    if vals.get('name'):
        hn = HumanName(vals['name'])
        if vals['name'].startswith('^'):
            vals['name'] = vals['name'].replace('^', '')
        elif vals['name'].startswith('The'):
            pass
        else:
            hn.capitalize()
            formatted_name = (hn.title and hn.title + ' ') + (hn.first and hn.first + ' ') + (hn.middle and hn.middle + ' ') + (hn.last and hn.last + ' ') + (hn.suffix and hn.suffix + ' ')
            formatted_name = formatted_name.strip()
            if formatted_name:
                vals['name'] = formatted_name
        vals['first_name'] = hn.first
        vals['name'] = vals['name'].strip()

    for fld in filter(lambda x: vals.get(x), ['street', 'street2', 'city', 'zip', 'bill_street', 'bill_street2', 'bill_city', 'bill_zip', 'ship_street', 'ship_street2', 'ship_city', 'ship_zip']):
        if vals[fld].startswith('^'):
            vals[fld] = vals[fld].replace('^', '')
        else:
            vals[fld] = vals[fld].upper()
        vals[fld] = vals[fld].strip()

    for fld in filter(lambda x: vals.get(x), ['phone', 'email', 'tt_company_name', 'delivery_instructions']):
        vals[fld] = vals[fld].strip()

    if vals.get('phone'):
        vals['phone'] = re.sub("[^0-9]", "", vals['phone'])

    return vals


def strip_sale_address(vals):
    for fld in filter(lambda x: vals.get(x), ['street', 'street2', 'city', 'zip', 'bill_street', 'bill_street2', 'bill_city', 'bill_zip', 'ship_street', 'ship_street2', 'ship_city', 'ship_zip']):
        if vals[fld].startswith('^'):
            vals[fld] = vals[fld].replace('^', '')
        else:
            vals[fld] = vals[fld].upper()
        vals[fld] = vals[fld].strip()

    for fld in filter(lambda x: vals.get(x), ['phone', 'email', 'tt_company_name', 'delivery_instructions', 'client_order_ref', 'carrier_tracking_ref']):
        vals[fld] = vals[fld].strip()
        if fld == 'delivery_instructions':
            vals[fld] = vals[fld].replace('\n', ' ')

    return vals


def strip_address(vals):
    for fld in filter(lambda x: vals.get(x), ['street', 'street2', 'city', 'zip', 'bill_street', 'bill_street2', 'bill_city', 'bill_zip', 'ship_street', 'ship_street2', 'ship_city', 'ship_zip']):
        if vals[fld].startswith('^'):
            vals[fld] = vals[fld].replace('^', '')
        else:
            vals[fld] = vals[fld].upper()
        vals[fld] = vals[fld].strip()

    for fld in filter(lambda x: vals.get(x), ['phone', 'email', 'tt_company_name', 'delivery_instructions']):
        vals[fld] = vals[fld].strip()
    return vals


def clean(str, input='ISO-8859-1', output='utf-8'):
    str = str.encode(output, 'ignore')
    str = unicode(str, 'ascii', 'ignore')
    str = str.replace('\00', '').decode(input).encode(output, 'ignore')
    str = unicode(str, 'ascii', 'ignore')
    return str


def luhn(n):
    try:
        r = [int(ch) for ch in str(n)][::-1]
        return (sum(r[0::2]) + sum(sum(divmod(d * 2, 10)) for d in r[1::2])) % 10 == 0
    except Exception, e:
        return False


magento_update_fields = [
    'default_code',
    'name',
    'magento_short_description',
    'shipping_description',
    'description',
    'weight',
    'mgento_page_title',
    'shipping_group',
    'state',
    'list_price',
    'special_from_date',
    'special_to_date',
    'special_price',
    'ebay_title_appendix',
    'magento_enable',
    'company_id',
    'trademe_id',
]

product_status = {
    '': '',
    'draft': 'In Development',
    'sellable': 'Normal',
    'end': 'End of Lifecycle',
    'obsolete': 'Obsolete',
}

purchase_states = {
    'draft': 'Draft PO',
    'sent': 'RFQ Sent',
    'confirmed': 'Waiting Approval',
    'approved': 'Purchase Order',
    'to approve': 'To Approve',
    'purchase': 'Purchase Order',
    'done': 'Done',
    'cancel': 'Cancelled',
    'except_picking': 'Shipping Exception',
    'except_invoice': 'Invoice Exception',
}

payment_methods = [
    ('eftpos', 'EFTPOS'),
    ('cash', 'Cash'),
    ('paypal', 'Paypal'),
    ('credit_card_auto', 'Credit Card Auto'),
    ('bank_deposit', 'Bank Deposit'),
    ('pay_now', 'Pay Now'),
    ('voucher', 'Voucher'),
    ('cheque', 'Cheque'),
    ('credit_account', 'Credit Account'),
    ('transfer', 'Transfer'),
    ('credit_card', 'Credit Card Manual'),
]

PAYMENT_METHODS_DICT = {
    'bank_deposit': 'Bank Deposit',
    'cash': 'Cash',
    'cheque': 'Cheque',
    'credit_account': 'Credit Account',
    'credit_card': 'Credit Card Manual',
    'credit_card_auto': 'Credit Card Auto',
    'eftpos': 'EFTPOS',
    'pay_now': 'Pay Now',
    'paypal': 'Paypal',
    'transfer': 'Transfer',
    'voucher': 'Voucher'
}

SALE_STATES = [
    ('quote', 'Quote'),
    ('draft', 'Order'),
    ('cancel', 'Cancelled'),
    ('sale', 'Processing'),
    ('done', 'Done'),
]
# TODO From db remove deprecreated order states : waiting_date, manual, shipping_except, invoice_except and progress is not sale

SALE_STATES_DICT = {'quote': 'Quote', 'draft': 'Order', 'cancel': 'Cancelled', 'sale': 'Processing', 'done': 'Done', }

DELIVERY_STATES = [
    ('draft', 'Draft'),
    ('cancel', 'Cancelled'),
    ('waiting', 'Waiting Another Operation'),
    ('confirmed', 'Waiting Availability'),
    ('partially_available', 'Partially Available'),
    ('assigned', 'Ready to Deliver'),
    ('processing', 'Processing'),
    ('done', 'Delivered')
]

DELIVERY_STATES_RETURN = [
    ('draft_return', 'Draft (Return)'),
    ('cancel_return', 'Cancelled (Return)'),
    ('waiting_return', 'Waiting Another Operation (Return)'),
    ('confirmed_return', 'Waiting Availability (Return)'),
    ('partially_available_return', 'Partially Available (Return)'),
    ('assigned_return', 'Ready to Deliver (Return)'),
    ('processing_return', 'Processing (Return)'),
    ('done_return', 'Delivered (Return)')
]

DELIVERY_STATES_DICT = {
    'draft': 'Draft',
    'cancel': 'Cancelled',
    'waiting': 'Waiting Another Operation',
    'confirmed': 'Waiting Availability',
    'partially_available': 'Partially Available',
    'assigned': 'Ready to Deliver',
    'processing': 'Processing',
    'done': 'Delivered',
}

CASE_STATES_DICT = {
    'draft': 'New',
    'cancel': 'Cancelled',
    'open': 'In Progress',
    'resolved': 'Resolved',
    'pending': 'Pending',
    'done': 'Closed'
}

stock_status = {
    'draft': 'Draft',
    'auto': 'Waiting Another Operation',
    'confirmed': 'Waiting Availability',
    'assigned': 'Ready to Deliver',
    'processing': 'Processing',
    'done': 'Delivered',
    'cancel': 'Cancelled',
}

sale_channels = [
    ('not_defined', 'Not defined'),
    ('phone', 'Phone'),
    ('showroom', 'Showroom'),
    ('website', 'Website'),
    ('trademe', 'Trade Me'),
    ('ebay', 'eBay'),
    ('daily_deal', 'Daily Deal'),
    ('wholesale', 'Wholesale')
]

trademe_sale_types = [('na', 'N/a'), ('buy_now', 'Buy Now'), ('auction', 'Auction'), ('fixed_price_offer', 'Fixed Price Offer')]

CASE_REF_LIST = [('res.partner', 'Partner'), ('product.product', 'Product'), ('sale.order', 'Sales Order'), ('stock.picking', 'Delivery Order')]
CASE_PRIORITIES = [('5', 'Lowest'), ('4', 'Low'), ('3', 'Normal'), ('2', 'High'), ('1', 'Highest')]
CASE_DEADLINES = [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (11, 11),
                  (12, 12), (13, 13), (14, 14), (15, 15), (16, 16), (17, 17), (18, 18), (19, 19), (20, 20), (21, 21),
                  (22, 22), (23, 23), (24, 24), (25, 25), (26, 26), (27, 27), (28, 28), (29, 29), (30, 30)]


def decrypt_farmlands_card(encrypted, passphrase, iv):
    # Usage print(bytes.decode(decrypt(code,pass,iv)))
    aes = AES.new(passphrase, AES.MODE_CBC, iv)
    return aes.decrypt(base64.b64decode(encrypted))
