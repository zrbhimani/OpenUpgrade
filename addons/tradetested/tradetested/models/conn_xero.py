# -*- encoding: utf-8 -*-

from odoo import tools, api, fields, models, _
import time
from odoo.exceptions import except_orm, ValidationError
from xero.auth import PrivateCredentials
from xero import Xero
from datetime import datetime
import logging
import json

from xero.exceptions import XeroBadRequest

_logger = logging.getLogger('Xero')

_xero_inv_status = {
    'sent': 'DRAFT',
    'draft': 'DRAFT',
    'proforma': 'SUBMITTED',
    'cancel': 'DELETED',
    'purchase': 'AUTHORISED',
    'approved': 'AUTHORISED',
    'open': 'AUTHORISED',
    'paid': 'AUTHORISED',
    'cancel': 'VOIDED'
}
_xero_inv_type = {'': '', 'out_invoice': 'ACCREC', 'in_invoice': 'ACCPAY'}

_xero_pay_type = {'': '', 'receipt': 'ACCRECPAYMENT', 'payment': 'ACCPAYPAYMENT'}

_xero_pay_state = {'': '', 'posted': 'AUTHORISED'}


class xero_configuration(models.Model):
    _name = "xero.configuration"
    _xero = False

    name = fields.Char('Organization', size=128, readonly=True, states={'draft': [('readonly', False)]})
    consumer_key = fields.Char('Consumer Key', size=64, readonly=True, states={'draft': [('readonly', False)]})
    consumer_secret = fields.Char('Consumer Secret', size=64, readonly=True, states={'draft': [('readonly', False)]})
    rsa_key = fields.Text('RSA Private Key (.pem)', size=1024, readonly=True, states={'draft': [('readonly', False)]})

    company_id = fields.Many2one('res.company', 'Company')
    state = fields.Selection([('draft', 'New'), ('connected', 'Connected')], 'Connection Status', size=300, default='draft')

    @api.multi
    def authenticate(self):
        sobj = self[0]
        credentials = PrivateCredentials(sobj.consumer_key, sobj.rsa_key)
        self._xero = Xero(credentials)

        organisations = self._xero.organisations.all()
        if organisations:

            if organisations[0].get('Name'):
                sobj.write({'state': 'connected', 'name': organisations[0]['Name']})
        return True

    @api.multi
    def reset(self):
        self._xero = False
        return self.write({'state': 'draft'})

    @api.multi
    def export_partner(self):
        sobj = self[0]
        if not self._xero:
            self._xero = Xero(PrivateCredentials(sobj.consumer_key, sobj.rsa_key))

        partner = self._context['export_partner']

        matching_found = self._xero.contacts.filter(Name=partner.name)
        if matching_found:
            if matching_found[0].get('ContactID'):
                partner.write({'xero_id': matching_found[0]['ContactID']})
        else:
            partner_vals = {
                'Name': partner.name,
                'Website': partner.website,
                'IsCustomer': partner.customer,
                'IsSupplier': partner.supplier
            }
            if partner.email:
                partner_vals['EmailAddress'] = partner.email

            resp = self._xero.contacts.put(partner_vals)
            if resp and resp[0].get('ContactID'):
                partner.write({'xero_id': resp[0]['ContactID']})
        self._cr.commit()
        return True

    @api.multi
    def export_products(self):
        sobj = self[0]
        if not self._xero:
            self._xero = Xero(PrivateCredentials(sobj.consumer_key, sobj.rsa_key))

        prod_pool = self.env['product.product']

        if 'export_product_id' in self._context:
            prod_to_export = [prod_pool.browse(self._context['export_product_id'])]
            matching_found = self._xero.items.filter(Code=prod_to_export[0].default_code)
            if matching_found:
                if matching_found[0].get('ItemID'):
                    prod_to_export[0].write({'xero_id': matching_found[0]['ItemID']})
                    return True
        else:
            for item in self._xero.items.all():
                prod_id = prod_pool.search([('default_code', '=', item['Code']), ('company_id', '=', sobj.company_id.id)])
                if prod_id:
                    prod_id[0].write({'xero_id': item['ItemID']})
            self._cr.commit()

            prod_ids = prod_pool.search([('xero_id', '=', False), ('company_id', '=', sobj.company_id.id)])
            prod_to_export = prod_ids

        prod_list = []
        for product in prod_to_export:
            if product.default_code and len(product.default_code) < 30:
                product_vals = {
                    'Description': product.name,
                    'Code': product.default_code,

                    'PurchaseDetails': {'UnitPrice': product.standard_price},
                    'SalesDetails': {'UnitPrice': product.list_price},
                }
                prod_list.append(product_vals)

        if not prod_list:
            return

        try:
            resp = self._xero.items.put(prod_list)
            for rec in resp:
                if rec.get('ItemID'):
                    matching_prod_id = prod_pool.search([('default_code', '=', rec['Code'])])
                    if matching_prod_id:
                        matching_prod_id.write({'xero_id': rec['ItemID']})
        except XeroBadRequest, xe:
            raise except_orm('Error:', xe.errors)

        return True

    @api.multi
    def export_purchase_orders(self):
        sobj = self[0]
        if not self._xero:
            self._xero = Xero(PrivateCredentials(sobj.consumer_key, sobj.rsa_key))

        tax_pool = self.env['account.tax']
        cur_pool = self.env['res.currency']

        self._cr.execute("SELECT id from purchase_order WHERE state not in ('draft','cancel') and company_id=%s and (xero_export_at is null or xero_export_at < write_date)" % sobj.company_id.id)
        purchase_ids = [x[0] for x in self._cr.fetchall()]

        purchase_create_list = []
        for purchase in self.env['purchase.order'].browse(purchase_ids):
            if not purchase.partner_id.commercial_partner_id.xero_id:
                self.with_context({'export_partner': purchase.partner_id.commercial_partner_id}).export_partner()
                purchase.refresh()

            if str(purchase.partner_id.commercial_partner_id.xero_id) == 'False':
                continue

            if not purchase.xero_id:
                # Check to see if already created
                resp = self._xero.invoices.filter(InvoiceNumber__contains=purchase.name)
                if resp:
                    purchase.write({'xero_id': resp[0]['InvoiceID']})
                    purchase.refresh()

            purchase_data = {
                'Contact': {'ContactID': purchase.partner_id.commercial_partner_id.xero_id},
                'CurrencyRate': '1.000000',
                'Status': _xero_inv_status[purchase.state],
                'LineItems': [],
                'SubTotal': str(purchase.amount_untaxed),
                # 'AmountDue'       : str(purchase.residual),
                'Type': 'ACCPAY',
                'TotalTax': str(purchase.amount_tax),
                'LineAmountTypes': 'Exclusive',
                'InvoiceNumber': purchase.name,
                # 'AmountPaid'      : str(purchase.amount_total - purchase.residual),
                # 'Total'           : str(purchase.amount_total)
            }
            if purchase.xero_id:
                purchase_data['InvoiceID'] = purchase.xero_id

            if purchase.date_order:
                purchase_data['Date'] = datetime.strptime(purchase.date_order, '%Y-%m-%d %H:%M:%S')
                purchase_data['DueDate'] = datetime.strptime(purchase.date_order, '%Y-%m-%d %H:%M:%S')

            for line in purchase.order_line:
                taxes = line.taxes_id.compute_all(line.price_unit, line.order_id.currency_id, line.product_qty, line.product_id, line.order_id.partner_id)['taxes']
                tax_amount = 0
                for tax in taxes:
                    tax_amount += tax.get('amount')
                cur = line.order_id.currency_id
                tax_amount = cur.round(tax_amount)

                if line.product_id:
                    account_code = line.product_id.categ_id.property_account_expense_categ_id.code
                else:
                    account_code = '220000'

                line_data = {
                    'TaxType': line.taxes_id and line.taxes_id[0].xero_tax_type or 'NONE',
                    'Quantity': str(abs(line.product_qty)),
                    'TaxAmount': tax_amount,
                    'Description': line.name,
                    'LineAmount': str(line.price_subtotal),
                    'AccountCode': account_code,
                    'UnitAmount': line.product_qty < 0 and str(line.price_unit * -1) or str(line.price_unit)
                }

                if line.product_id.default_code and len(line.product_id.default_code) < 30:
                    if line.product_id.xero_id:
                        line_data['ItemCode'] = line.product_id.default_code
                    else:
                        self._context['export_product_id'] = line.product_id.id
                        self.export_products()
                        line_data['ItemCode'] = line.product_id.default_code

                purchase_data['LineItems'].append(line_data)
            if purchase_data.get('InvoiceID'):
                pass
            else:
                purchase_create_list.append(purchase_data)
            if not purchase_create_list:
                continue
            print len(purchase_create_list)
            try:
                resp = self._xero.invoices.put(purchase_create_list)
                purchase_create_list = []
                if resp:
                    for rec in resp:
                        if rec.get('InvoiceID'):
                            purchase.write({'xero_id': rec['InvoiceID'], 'xero_export_at': time.strftime('%Y-%m-%d %H:%M:%S')})
            except XeroBadRequest, xe:
                raise except_orm('Error: %s' % purchase.name, xe.errors)
            # except Exception, xe:
            #     print dir(xe)
            #     raise except_orm('Error: %s' % purchase.name, xe.errors)

            self._cr.commit()

        return True


# for payment_line in purchase.payment_ids:
#                 if not payment_line.xero_id:
#                     voucher = self.env['account.voucher'].search([('move_id','=',payment_line.move_id.id)])
#                     payment_data = {
#                                         'PaymentType'  : _xero_pay_type[voucher.type],
#                                         'Status'       : _xero_pay_state[voucher.state],
#                                         'Account'      : {'Code': payment_line.account_id.code},
#                                         'Invoice'      : {'InvoiceID' : purchase.xero_id},
#                                         'Amount'       : payment_line.credit,
#                                         #'CurrencyCode' : voucher.currency_id.name
#                     }
#                     payment_resp = self._xero.payments.put(payment_data)
#                     if payment_resp:
#                         payment_line.write({'xero_id': payment_resp[0]['PaymentID']})


class account_tax(models.Model):
    _inherit = 'account.tax'

    xero_tax_type = fields.Char('Tax Type', size=64)
