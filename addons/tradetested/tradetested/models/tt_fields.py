from odoo import tools, api, fields, models, _


class Uuid(fields.Field):
    type = 'uuid'
    column_type = ('uuid', 'uuid')


