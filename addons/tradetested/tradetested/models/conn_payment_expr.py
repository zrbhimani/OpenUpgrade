# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo.exceptions import UserError
from odoo.addons.tradetested.lib import ecs

import requests
import logging
import base64
from Crypto.Cipher import AES
import hashlib
from xml.dom import minidom
from datetime import datetime

_logger = logging.getLogger('PaymentExpress')

BS = 16
pad = lambda s: s + (BS - len(s) % BS) * chr(BS - len(s) % BS)
unpad = lambda s: s[:-ord(s[len(s) - 1:])]
IV = "0" * 16

def encrypt(data):
    if 'encryption_key' not in tools.config.options:
        _logger.warning('Please specify encryption_key parameter in configuration file')
        return data
    key = hashlib.sha256(tools.config['encryption_key']).digest()
    cipher = AES.new(key, AES.MODE_CBC, IV)
    return base64.encodestring(cipher.encrypt(pad(data)))

def decrypt(data):
    if 'encryption_key' not in tools.config.options:
        _logger.warning('Please specify encryption_key parameter in configuration file')
        return data
    key = hashlib.sha256(tools.config['encryption_key']).digest()
    cipher = AES.new(key, AES.MODE_CBC, IV)
    return unpad(cipher.decrypt(base64.decodestring(data)))

def xml_to_dict(xml):
    try:
        dom = minidom.parseString(xml)
    except:
        # is not an XML object, so return raw text
        return xml
    else:
        return ecs.unmarshal(dom)


class cc_payment_express(models.Model):
    _passwords = {}
    _name = 'cc.payment.express'
    _rec_name = 'company_id'

    host = fields.Char('PXPost URL', size=1024, default='https://sec.paymentexpress.com/pxpost.aspx')
    username = fields.Char('Username', size=512)
    password = fields.Char('Password', size=512)
    password_dummy = fields.Char(size=512, string="Password")
    company_id = fields.Many2one('res.company', 'Company')

    @api.model
    def create(self, vals):
        if vals.get('password_dummy'):
            vals['password'] = encrypt(vals['password_dummy'])
            vals.__delitem__('password_dummy')
            self._passwords = {}
        return super(cc_payment_express, self).create(vals)

    @api.multi
    def write(self, vals):
        if vals.get('password_dummy'):
            vals['password'] = encrypt(vals['password_dummy'])
            vals.__delitem__('password_dummy')
            self._passwords = {}
        return super(cc_payment_express, self).write(vals)

    @api.model
    def transaction_purchase(self, data):
        if 'company_id' not in data:
            raise UserError('Company is required')

        sobjs = self.search([('company_id', '=', data['company_id'])])
        if not sobjs:
            raise UserError('No configuration for this company')

        sobj = sobjs[0]

        if sobj.id not in self._passwords:
            self._passwords[sobj.id] = decrypt(sobj.password)

        password = self._passwords[sobj.id]

        cur_code = sobj.company_id.currency_id.name
        merchant_ref = 'Order ' + data['order_number']

        capture_amount = data['cc_capture_amount']

        # if Capture amount is without decimal part (Cents ) then convert it to integer
        decimal_part = capture_amount % 1
        if decimal_part > 0:
            capture_amount = "%0.2f" % capture_amount
        else:
            capture_amount = "%d" % capture_amount

        _logger.info('Capturing %s' % capture_amount)

        request_xml = "<Txn>"
        request_xml += "<PostUsername>" + sobj.username + "</PostUsername>"
        request_xml += "<PostPassword>" + password + "</PostPassword>"
        request_xml += "<CardHolderName>" + data['cc_holder'] + "</CardHolderName>"
        request_xml += "<CardNumber>" + data['cc_number'] + "</CardNumber>"
        request_xml += "<Amount>" + capture_amount + "</Amount>"
        request_xml += "<DateExpiry>" + data['cc_expiry'] + "</DateExpiry>"
        request_xml += "<Cvc2>" + data['cc_cvc'] + "</Cvc2>"
        request_xml += "<Cvc2Presence>1</Cvc2Presence>"
        request_xml += "<InputCurrency>" + cur_code + "</InputCurrency>"
        request_xml += "<TxnType>" + 'Purchase' + "</TxnType>"
        request_xml += "<TxnId>" + data['txn_ref'] + "</TxnId>"
        request_xml += "<MerchantReference>" + merchant_ref + "</MerchantReference>"
        request_xml += "</Txn>"

        response = requests.post(sobj.host, request_xml, verify=False)

        response_bag = xml_to_dict(response.text)

        txn = response_bag.Txn

        _logger.info('Response: %s' % txn.ResponseText)

        if txn.Success != '1':
            raise UserError('DPS Declined,  \n' + txn.ResponseText)

        self.env['cc.transaction'].create({
            'cc_id': sobj.id,
            'datesettlement': hasattr(txn.Transaction, 'DateSettlement') and datetime.strptime(txn.Transaction.DateSettlement, '%Y%m%d').strftime('%Y-%m-%d') or False,
            'amount': float(txn.Transaction.Amount) or 0.0,
            'currency': txn.Transaction.InputCurrencyName or False,
            'txntype': txn.Transaction.TxnType,
            'cardnumber': txn.Transaction.CardNumber,
            'dpstxnref': txn.Transaction.DpsTxnRef,
            'responsetext': txn.ResponseText,
            'order_id': data['order_id'],
            'payment_id': data['payment_id'],
        })
        return (txn.Transaction.CardNumber, txn.Transaction.DpsTxnRef)

    @api.model
    def transaction_refund(self, data):

        txn_pool = self.env['cc.transaction']
        if 'company_id' not in data:
            raise UserError('Company is required')

        sobjs = self.search([('company_id', '=', data['company_id'])])
        if not sobjs:
            raise UserError('No configuration for this company')

        sobj = sobjs[0]

        if sobj.id not in self._passwords:
            self._passwords[sobj.id] = decrypt(sobj.password)

        password = self._passwords[sobj.id]

        merchant_ref = 'Refund ' + data['order_number']
        refund_amount_total = data['cc_refund_amount']

        txn_ids = txn_pool.search([('order_id', '=', data['order_id']), ('txntype', '=', 'Purchase')], order='id')
        if not txn_ids:
            raise UserError('There is no history of CC Txn for this order, so can not process refund')

        txn_resp = ''
        for cc_txn in txn_ids:  # txn_pool.browse(cr, uid, txn_ids)

            if (cc_txn.amount - cc_txn.amount_refunded) < 0.01:
                continue

            refund_amount = min(cc_txn.amount - cc_txn.amount_refunded, refund_amount_total)
            refund_amount_total -= refund_amount

            decimal_part = refund_amount % 1
            if decimal_part > 0:
                refund_amount_str = "%0.2f" % refund_amount
            else:
                refund_amount_str = "%d" % refund_amount

            _logger.info('Refunding %s' % refund_amount)

            request_xml = "<Txn>"
            request_xml += "<PostUsername>" + sobj.username + "</PostUsername>"
            request_xml += "<PostPassword>" + password + "</PostPassword>"
            request_xml += "<Amount>" + refund_amount_str + "</Amount>"
            request_xml += "<TxnType>" + 'Refund' + "</TxnType>"
            request_xml += "<DpsTxnRef>" + cc_txn.dpstxnref + "</DpsTxnRef>"
            request_xml += "<MerchantReference>" + merchant_ref + "</MerchantReference>"

            response = requests.post(sobj.host, request_xml, verify=False)

            response_bag = xml_to_dict(response.text)

            txn = response_bag.Txn

            _logger.info('Response: %s' % txn.ResponseText)

            if txn.Success != '1':
                raise UserError(_('DPS Declined', 'DPS  \n' + txn.ResponseText))

            cc_txn.write({'amount_refunded': refund_amount + cc_txn.amount_refunded})
            txn_pool.create({
                'cc_id': sobj.id,
                'datesettlement': hasattr(txn.Transaction, 'DateSettlement') and datetime.strptime(txn.Transaction.DateSettlement, '%Y%m%d').strftime('%Y-%m-%d') or False,
                'amount': float(txn.Transaction.Amount) or 0.0,
                'currency': txn.Transaction.InputCurrencyName or False,
                'txntype': txn.Transaction.TxnType,
                'cardnumber': txn.Transaction.CardNumber,
                'dpstxnref': txn.Transaction.DpsTxnRef,
                'responsetext': txn.ResponseText,
                'order_id': data['order_id'],
                'payment_id': data['payment_id'],
            })

            txn_resp += ' CC:%s, DPS Ref:%s, Amount: %s' % (txn.Transaction.CardNumber, txn.Transaction.DpsTxnRef, refund_amount)

            if refund_amount_total < 0.01:
                break

        return txn_resp.strip()


class cc_transaction(models.Model):
    _name = 'cc.transaction'
    _order = 'datesettlement desc'

    cc_id = fields.Many2one('cc.payment.express', 'Payment Acc.')
    datesettlement = fields.Date('DateSettlement')
    amount = fields.Float('Amount')
    currency = fields.Char('Currency Code', size=4)
    txntype = fields.Char('TxnType', size=64)
    cardnumber = fields.Char('Card Number', size=20)
    dpstxnref = fields.Char('DpsTxnRef', size=64)
    responsetext = fields.Char('Response Text')
    payment_id = fields.Many2one('sale.order.payment', string="Payment", ondelete='restrict')
    order_id = fields.Many2one('sale.order', string="Order", ondelete='restrict')
    amount_refunded = fields.Float('Amount Refunded')
