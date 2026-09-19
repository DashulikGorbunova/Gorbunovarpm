"""
Partition alerting with deduplication (lab 03 part 11).

Primary channel: Telegram bot.
Also: optional email / webhook + log file for demos.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger('shop.partitions.alert')


def _state_path() -> Path:
    path = getattr(settings, 'PARTITION_ALERT_STATE_FILE', None)
    if path:
        return Path(path)
    return Path(settings.BASE_DIR) / '.partition_alert_state.json'


def load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {'last_status': None, 'last_missing': [], 'alert_sent': False}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {'last_status': None, 'last_missing': [], 'alert_sent': False}


def save_state(state: dict) -> None:
    path = _state_path()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def format_critical(result: dict) -> str:
    checked = result['checked_at']
    if hasattr(checked, 'strftime'):
        checked = checked.strftime('%Y-%m-%d %H:%M:%S')
    missing = '\n'.join(result['missing']) or '(none)'
    horizon = result.get('horizon_label')
    if not horizon:
        if result.get('horizon_days') is not None:
            horizon = f"{result['horizon_days']} days"
        else:
            horizon = f"{result.get('horizon_months', '?')} months"
    return (
        '🚨 Partition alert\n'
        f"Table: {result['table']}\n"
        f'Missing partitions:\n{missing}\n'
        f'Expected horizon: {horizon}\n'
        f'Checked at:\n{checked}'
    )


def format_ok(result: dict) -> str:
    checked = result['checked_at']
    if hasattr(checked, 'strftime'):
        checked = checked.strftime('%Y-%m-%d %H:%M:%S')
    return (
        '🟢 Partition check OK\n'
        f"Table: {result['table']}\n"
        'All required partitions exist.\n'
        f'Checked at:\n{checked}'
    )


def _telegram_api(method: str, payload: dict | None = None) -> dict:
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN is not configured')
    url = f'https://api.telegram.org/bot{token}/{method}'
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method='POST' if data else 'GET')
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def discover_telegram_chat_id() -> str | None:
    """Pick the most recent private chat from getUpdates (after user /start)."""
    try:
        body = _telegram_api('getUpdates', {'limit': 50, 'timeout': 0})
    except (urllib.error.URLError, RuntimeError, json.JSONDecodeError, TimeoutError) as exc:
        logger.error('Telegram getUpdates failed: %s', exc)
        return None
    if not body.get('ok'):
        logger.error('Telegram getUpdates not ok: %s', body)
        return None
    chat_id = None
    for upd in body.get('result') or []:
        msg = upd.get('message') or upd.get('edited_message') or {}
        chat = msg.get('chat') or {}
        if chat.get('id') is not None:
            chat_id = str(chat['id'])
    return chat_id


def resolve_telegram_chat_id() -> str | None:
    configured = (getattr(settings, 'TELEGRAM_CHAT_ID', '') or '').strip()
    if configured:
        return configured
    discovered = discover_telegram_chat_id()
    if discovered:
        logger.info('Discovered TELEGRAM_CHAT_ID=%s from getUpdates', discovered)
        # persist for next runs inside container volume
        path = Path(settings.BASE_DIR) / '.telegram_chat_id'
        path.write_text(discovered, encoding='utf-8')
        return discovered
    cached = Path(settings.BASE_DIR) / '.telegram_chat_id'
    if cached.exists():
        return cached.read_text(encoding='utf-8').strip() or None
    return None


def _send_telegram(body: str) -> bool:
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''
    if not token:
        logger.warning('TELEGRAM_BOT_TOKEN empty — Telegram skipped')
        return False
    chat_id = resolve_telegram_chat_id()
    if not chat_id:
        logger.error(
            'TELEGRAM_CHAT_ID unknown. Open @%s and send /start, then retry.',
            getattr(settings, 'TELEGRAM_BOT_USERNAME', 'bot'),
        )
        return False
    try:
        result = _telegram_api('sendMessage', {
            'chat_id': chat_id,
            'text': body,
            'disable_web_page_preview': True,
        })
        if not result.get('ok'):
            logger.error('Telegram sendMessage failed: %s', result)
            return False
        logger.info('Telegram alert sent to chat_id=%s', chat_id)
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.error('Telegram send failed: %s', exc)
        return False


def _send_email(subject: str, body: str) -> None:
    if not getattr(settings, 'PARTITION_ALERT_EMAIL_ENABLED', False):
        return
    recipients = getattr(settings, 'PARTITION_ALERT_EMAILS', None) or []
    if not recipients:
        return
    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'partitions@flower-shop.local'),
        recipient_list=list(recipients),
        fail_silently=True,
    )


def _send_webhook(body: str) -> None:
    url = getattr(settings, 'PARTITION_ALERT_WEBHOOK_URL', '') or ''
    if not url:
        return
    data = json.dumps({'text': body, 'message': body}).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info('Webhook alert status=%s', resp.status)
    except urllib.error.URLError as exc:
        logger.error('Webhook alert failed: %s', exc)


def deliver(subject: str, body: str) -> None:
    logger.warning('%s\n%s', subject, body)
    text = f'{subject}\n\n{body}'
    _send_telegram(text)
    _send_email(subject, body)
    _send_webhook(body)
    out = Path(settings.BASE_DIR) / '.last_partition_alert.txt'
    out.write_text(f'{subject}\n\n{body}\n', encoding='utf-8')


def process_health_result(result: dict, *, force: bool = False) -> str:
    """
    Apply dedup rules and send alerts if needed.
    Returns action: 'critical_sent' | 'ok_sent' | 'suppressed' | 'none'
    """
    state = load_state()
    status = result['status']
    missing = result.get('missing') or []

    if status == 'CRITICAL':
        already = (
            state.get('last_status') == 'CRITICAL'
            and state.get('alert_sent')
            and state.get('last_missing') == missing
        )
        if already and not force:
            logger.info('CRITICAL unchanged — alert suppressed')
            return 'suppressed'
        body = format_critical(result)
        deliver(f"[CRITICAL] Missing partitions on {result['table']}", body)
        save_state({
            'last_status': 'CRITICAL',
            'last_missing': missing,
            'alert_sent': True,
            'updated_at': timezone.now().isoformat(),
        })
        return 'critical_sent'

    if state.get('last_status') == 'CRITICAL' or force:
        body = format_ok(result)
        deliver(f"[OK] Partitions healthy on {result['table']}", body)
        save_state({
            'last_status': 'OK',
            'last_missing': [],
            'alert_sent': False,
            'updated_at': timezone.now().isoformat(),
        })
        return 'ok_sent'

    save_state({
        'last_status': 'OK',
        'last_missing': [],
        'alert_sent': False,
        'updated_at': timezone.now().isoformat(),
    })
    return 'none'
