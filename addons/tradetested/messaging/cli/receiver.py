import logging
from kombu import Connection
from ..worker import Worker

import openerp

observers = {}
_logger = logging.getLogger('receiver')
SLEEP_INTERVAL = 5

class ObserverType(type):
    def __init__(cls, name, bases, attrs):
        if cls.queue != '_none_':
            observers[cls.queue] = cls

class Observer(object):
    """Subclass this class to define new MQ Observers """
    __metaclass__ = ObserverType
    queue = '_none_'

    def run(self, env, body, message):
        pass

class Receiver(openerp.cli.Command):
    def run(self, args):
        openerp.tools.config.parse_config(args)
        registry = openerp.modules.registry.RegistryManager.new('odoo')
        with openerp.api.Environment.manage():
            env = openerp.api.Environment(registry.cursor(), openerp.SUPERUSER_ID, {})
            sobj = env['rabbitmq'].search([])
            sobj.ensure_one()

            url = 'amqp://%s:%s@%s:%s//' % (sobj.username, sobj.password, sobj.host, sobj.port)
            with Connection(url) as conn:
                try:
                    worker = Worker(registry, conn, observers)
                    worker.run()
                except KeyboardInterrupt:
                    print('Closing rabbit hole')
                    env.cr.close()
