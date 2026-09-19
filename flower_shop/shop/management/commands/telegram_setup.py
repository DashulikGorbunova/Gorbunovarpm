"""Resolve Telegram chat_id after user sends /start to the bot."""
from django.conf import settings
from django.core.management.base import BaseCommand

from shop.alerts import discover_telegram_chat_id, resolve_telegram_chat_id, _send_telegram


class Command(BaseCommand):
    help = 'Find TELEGRAM_CHAT_ID via getUpdates and optionally send a test message.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--test',
            action='store_true',
            help='Send a test alert to the resolved chat',
        )

    def handle(self, *args, **options):
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''
        if not token:
            self.stderr.write(self.style.ERROR('TELEGRAM_BOT_TOKEN is empty'))
            return

        username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'bot')
        self.stdout.write(f'Bot: @{username}')
        self.stdout.write('If chat_id is empty — open the bot in Telegram and send /start')

        chat_id = resolve_telegram_chat_id()
        if not chat_id:
            discovered = discover_telegram_chat_id()
            chat_id = discovered

        if not chat_id:
            self.stderr.write(
                self.style.ERROR(
                    f'No chat found. Write /start to @{username}, then run this command again.'
                )
            )
            return

        self.stdout.write(self.style.SUCCESS(f'TELEGRAM_CHAT_ID={chat_id}'))
        self.stdout.write('Put it into docker-compose / env if you want it fixed.')

        if options['test']:
            ok = _send_telegram(
                '✅ Flower Shop partition alerts connected.\n'
                'CRITICAL / recovery messages will arrive here.'
            )
            if ok:
                self.stdout.write(self.style.SUCCESS('Test message sent.'))
            else:
                self.stderr.write(self.style.ERROR('Failed to send test message.'))
