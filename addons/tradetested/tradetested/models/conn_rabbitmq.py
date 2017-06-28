# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _

import kombu
import time
import logging
_logger = logging.getLogger('RabbitMQ')

class rabbitmq(models.Model):
    _name = 'rabbitmq'
    _rec_name = 'host'

    host = fields.Char('Host')
    port = fields.Integer('port', default=5672)
    username = fields.Char('Username')
    password = fields.Char('Password')

    _connection = False

    @api.model
    def get_connection_string(self):
        objs = self.search([])
        if len(objs) <= 0:
            return 'memory://test'
        sobj = objs[0]
        if sobj:
            return 'amqp://%s:%s@%s:%s//' % (sobj.username, sobj.password, sobj.host, sobj.port)
        else:
            return 'memory://test'

    @api.multi
    def test_connection(self):
        self._get_connection(reinit=True)
        return True

    @api.multi
    def _get_connection(self, reinit=False):
        if reinit or not (self._connection and self._connection.connected):
            self._connection = kombu.Connection(self.get_connection_string())
        return self._connection

    @api.multi
    def push(self, queue, message):
        self._get_connection().SimpleQueue(queue).put(message)
        return True

    @api.multi
    def test_push(self):
        return self.push('test_queue', 'Msg ' + time.strftime('%Y-%m-%d %H:%M:%S'))





