# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
import time
from odoo.tools import html2text
from openerp.tools.translate import _
import os
from lxml import etree


class ir_filters(models.Model):
    _inherit = 'ir.filters'

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        if not self._context.get('show_hidden'):
            args.append(('name', 'not like', '~'))
        return super(ir_filters, self).search(args=args, offset=offset, limit=limit, order=order, count=count)


class ir_attachment(models.Model):
    _inherit = 'ir.attachment'

    product_id = fields.Many2one('product.product', 'Product')
    write_date = fields.Datetime('Date Modified', readonly=True)
    write_uid = fields.Many2one('res.users', 'Last Modification User', readonly=True)
    doc_type = fields.Selection([('Manual', 'Manual'), ('Technical', 'Technical'), ('Other', 'Other')], 'Type')


class res_company(models.Model):
    _inherit = 'res.company'

    payment_details = fields.Text('Payment Details')
    extra_logo = fields.Binary('Extra Logo')
    payment_details_text =  fields.Text(compute = '_cal_payment_details_line_breaks', string="payment_details_text")

    @api.multi
    def _cal_payment_details_line_breaks(self):
        for comp in self:
            comp.payment_details_text = comp.overdue_msg.split('\n')

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        if self.env.user.company_id.name != 'Group':
            args.append(('id', '=', self.env.user.company_id.id))
        return super(res_company, self).name_search(name, args, operator, limit)


class mail_thread(models.AbstractModel):
    _inherit = 'mail.thread'

    @api.multi
    def log_note(self, body=False):
        vals = {
            'body': body,
            'model': self._name,
            'res_id': self.id,
            'subtype_id': False,
            'author_id': self.env.user.partner_id.id,
            'type': 'comment'
        }
        return self.env['mail.message'].create(vals)

class mail_message(models.Model):
    _inherit = 'mail.message'

    type = fields.Char('Type')
    body_text = fields.Text(compute='_body_text', string="Content")

    @api.multi
    def _body_text(self):
        for msg in self:
            if msg.body:
                msg.body_text = html2text(msg.body)

    @api.one
    def delete_me(self):
        return self.unlink()


class mail_template(models.Model):
    _inherit = 'mail.template'


class res_country(models.Model):

    @api.multi
    def _get_name_cap(self):
        for country in self:
            country.name_cap = country.name.upper()

    _inherit = 'res.country'
    _rec_name = 'name_cap'

    name_cap = fields.Char(compute = '_get_name_cap', string="Name", store=True)


class res_country_state(models.Model):

    @api.multi
    def _get_name_cap(self):
        for state in self:
            state.name_cap = state.name.upper()

    _inherit = 'res.country.state'
    _rec_name = 'name_cap'

    name_cap = fields.Char(compute = '_get_name_cap', string="Name", store=True)

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        if self._context.get('country_id'):
            args.append(('country_id', '=', self._context['country_id']))
        return super(res_country_state, self).name_search(name=name, args=args, operator=operator, limit=limit)


class ir_attachment(models.Model):

    _fileext_to_type = {
        '7z': 'archive',
        'aac': 'audio',
        'ace': 'archive',
        'ai': 'vector',
        'aiff': 'audio',
        'apk': 'archive',
        'app': 'binary',
        'as': 'script',
        'asf': 'video',
        'ass': 'text',
        'avi': 'video',
        'bat': 'script',
        'bin': 'binary',
        'bmp': 'image',
        'bzip2': 'archive',
        'c': 'script',
        'cab': 'archive',
        'cc': 'script',
        'ccd': 'disk',
        'cdi': 'disk',
        'cdr': 'vector',
        'cer': 'certificate',
        'cgm': 'vector',
        'cmd': 'script',
        'coffee': 'script',
        'com': 'binary',
        'cpp': 'script',
        'crl': 'certificate',
        'crt': 'certificate',
        'cs': 'script',
        'csr': 'certificate',
        'css': 'html',
        'csv': 'spreadsheet',
        'cue': 'disk',
        'd': 'script',
        'dds': 'image',
        'deb': 'archive',
        'der': 'certificate',
        'djvu': 'image',
        'dmg': 'archive',
        'dng': 'image',
        'doc': 'document',
        'docx': 'document',
        'dvi': 'print',
        'eot': 'font',
        'eps': 'vector',
        'exe': 'binary',
        'exr': 'image',
        'flac': 'audio',
        'flv': 'video',
        'gif': 'webimage',
        'gz': 'archive',
        'gzip': 'archive',
        'h': 'script',
        'htm': 'html',
        'html': 'html',
        'ico': 'image',
        'icon': 'image',
        'img': 'disk',
        'iso': 'disk',
        'jar': 'archive',
        'java': 'script',
        'jp2': 'image',
        'jpe': 'webimage',
        'jpeg': 'webimage',
        'jpg': 'webimage',
        'jpx': 'image',
        'js': 'script',
        'key': 'presentation',
        'keynote': 'presentation',
        'lisp': 'script',
        'lz': 'archive',
        'lzip': 'archive',
        'm': 'script',
        'm4a': 'audio',
        'm4v': 'video',
        'mds': 'disk',
        'mdx': 'disk',
        'mid': 'audio',
        'midi': 'audio',
        'mkv': 'video',
        'mng': 'image',
        'mp2': 'audio',
        'mp3': 'audio',
        'mp4': 'video',
        'mpe': 'video',
        'mpeg': 'video',
        'mpg': 'video',
        'nrg': 'disk',
        'numbers': 'spreadsheet',
        'odg': 'vector',
        'odm': 'document',
        'odp': 'presentation',
        'ods': 'spreadsheet',
        'odt': 'document',
        'ogg': 'audio',
        'ogm': 'video',
        'otf': 'font',
        'p12': 'certificate',
        'pak': 'archive',
        'pbm': 'image',
        'pdf': 'print',
        'pem': 'certificate',
        'pfx': 'certificate',
        'pgf': 'image',
        'pgm': 'image',
        'pk3': 'archive',
        'pk4': 'archive',
        'pl': 'script',
        'png': 'webimage',
        'pnm': 'image',
        'ppm': 'image',
        'pps': 'presentation',
        'ppt': 'presentation',
        'ps': 'print',
        'psd': 'image',
        'psp': 'image',
        'py': 'script',
        'r': 'script',
        'ra': 'audio',
        'rar': 'archive',
        'rb': 'script',
        'rpm': 'archive',
        'rtf': 'text',
        'sh': 'script',
        'sub': 'disk',
        'svg': 'vector',
        'sxc': 'spreadsheet',
        'sxd': 'vector',
        'tar': 'archive',
        'tga': 'image',
        'tif': 'image',
        'tiff': 'image',
        'ttf': 'font',
        'txt': 'text',
        'vbs': 'script',
        'vc': 'spreadsheet',
        'vml': 'vector',
        'wav': 'audio',
        'webp': 'image',
        'wma': 'audio',
        'wmv': 'video',
        'woff': 'font',
        'xar': 'vector',
        'xbm': 'image',
        'xcf': 'image',
        'xhtml': 'html',
        'xls': 'spreadsheet',
        'xlsx': 'spreadsheet',
        'xml': 'html',
        'zip': 'archive'
    }

    _inherit = 'ir.attachment'

    write_date = fields.Datetime('Date Modified', readonly=True)
    write_uid = fields.Many2one('res.users', 'Last Modification User', readonly=True)

    product_id = fields.Many2one('product.product', 'Product')
    doc_type = fields.Selection([('Manual', 'Manual'), ('Technical', 'Technical'), ('Other', 'Other')], 'Type')

    product_ids = fields.Many2many('product.product', 'rel_product_document', 'document_id', 'product_id', 'Products')
    categ_ids = fields.Many2many('product.category', 'rel_product_category_document', 'document_id', 'category_id', 'Categories')
    type = fields.Selection([('url', 'URL'), ('binary', 'File'), ], 'Type', help="Binary File or URL", required=True, change_default=True)

    file_type_icon = fields.Char(compute='get_attachment_type', string='File Type Icon')

    @api.multi
    def get_attachment_type(self):
        for attachment in self:
            fileext = os.path.splitext(attachment.datas_fname or '')[1].lower()[1:]
            attachment.file_type_icon = self._fileext_to_type.get(fileext, 'unknown')

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        resp = super(ir_attachment, self).fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if self.env['res.users'].browse(self.env.uid).team_id.name == 'Catalogue & Sourcing':
            doc = etree.XML(resp['arch'])
            for node in doc.xpath("//form") + doc.xpath("//kanban") + doc.xpath("//tree"):
                node.set('create', '1')
            resp['arch'] = etree.tostring(doc)
        return resp



class act_window(models.Model):
    _inherit = 'ir.actions.act_window'


    domain = fields.Char('Domain Value', size=512, help="Optional domain filtering of the destination data, as a Python expression")