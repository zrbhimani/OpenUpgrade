from kombu.mixins import ConsumerMixin
from kombu.log import get_logger
from kombu import Exchange, Queue
import psycopg2
import openerp

task_exchange = Exchange('tasks', type='direct')
task_queues = []

logger = get_logger(__name__)


class Worker(ConsumerMixin):

    def __init__(self, registry, connection, observers,):
        self.connection = connection
        self.observers = {}
        self.registry = registry
        self.retries = 5
        for key, worker in observers.iteritems():
            task_queues.append(Queue(key, task_exchange, routing_key=key))
            self.observers[key] = worker()


    def get_consumers(self, Consumer, channel):
        return [Consumer(queues=task_queues, accept=['json'], callbacks=[self.process_task])]

    def process_task(self, body, message):
        try:
            key = message.delivery_info['routing_key']
            with self.registry.cursor() as cr:
                env = openerp.api.Environment(cr, openerp.SUPERUSER_ID, {})
                self.observers[key].run(env, body, message)
            self.retries = 5
        except psycopg2.InterfaceError as exc:
            self.registry = openerp.modules.registry.RegistryManager.new('odoo')
            if self.retries > 0:
                self.process_task(body, message)
            else:
                self.send_dlq(message, exc)
        except psycopg2.OperationalError as exc:
            sleep(0.5)
            if self.retries > 0:
                self.process_task(body, message)
            else:
                self.send_dlq(message, exc)
        # except Exception as exc:
        #     self.send_dlq(message, exc)
        message.ack()

    def send_dlq(self, message, exc):
        logger.error('task raised exception: %r', exc)
        # message.headers['exception'] = exc
        message.headers['original_routing_key'] = message.delivery_info['routing_key']
        message.delivery_info['routing_key'] = 'odoo.bus.errors'
        producer = self.connection.SimpleQueue('odoo.bus.errors').producer
        producer.channel.basic_publish(message, routing_key='odoo.bus.errors')

