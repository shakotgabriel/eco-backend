"""
Structured JSON logging with request ID tracking and Sentry integration.
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional
from django.utils import timezone
from django.conf import settings
import contextvars

# Context var to store request ID across async boundaries
request_id_context = contextvars.ContextVar('request_id', default=None)


class RequestIDMiddleware:
    """Add request ID to all requests and store in context."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Get or generate request ID
        request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        request.id = request_id
        request_id_context.set(request_id)
        
        response = self.get_response(request)
        response['X-Request-ID'] = request_id
        return response


class StructuredJSONFormatter(logging.Formatter):
    """Format logs as structured JSON with request ID and context."""
    
    def format(self, record):
        log_data = {
            'timestamp': timezone.now().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'request_id': request_id_context.get(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': self.formatException(record.exc_info)
            }
        
        # Add custom fields
        if hasattr(record, 'extra_data'):
            log_data['extra'] = record.extra_data
        
        return json.dumps(log_data)


class StructuredLogger:
    """Wrapper for structured logging with request ID."""
    
    def __init__(self, logger_name: str):
        self.logger = logging.getLogger(logger_name)
    
    def log_booking_event(self, event_type: str, booking_id: int, details: Dict[str, Any]):
        """Log booking-related events."""
        extra_data = {
            'event_type': event_type,
            'booking_id': booking_id,
            'request_id': request_id_context.get(),
            **details
        }
        
        self.logger.info(
            f'Booking event: {event_type}',
            extra={'extra_data': extra_data}
        )
        
        # Send to Sentry if relevant
        if event_type in ['FRAUD_DETECTED', 'PAYMENT_FAILED']:
            self._send_to_sentry('warning', extra_data)
    
    def log_payment_event(self, event_type: str, payment_id: int, details: Dict[str, Any]):
        """Log payment-related events (critical path)."""
        extra_data = {
            'event_type': event_type,
            'payment_id': payment_id,
            'request_id': request_id_context.get(),
            **details
        }
        
        level = 'error' if event_type in ['PAYMENT_FAILED', 'GATEWAY_ERROR'] else 'info'
        
        if level == 'error':
            self.logger.error(
                f'Payment event: {event_type}',
                extra={'extra_data': extra_data}
            )
            self._send_to_sentry('error', extra_data)
        else:
            self.logger.info(
                f'Payment event: {event_type}',
                extra={'extra_data': extra_data}
            )
    
    def log_fraud_detection(self, alert_type: str, details: Dict[str, Any]):
        """Log fraud detection events."""
        extra_data = {
            'alert_type': alert_type,
            'request_id': request_id_context.get(),
            'timestamp': timezone.now().isoformat(),
            **details
        }
        
        self.logger.warning(
            f'Fraud alert: {alert_type}',
            extra={'extra_data': extra_data}
        )
        
        self._send_to_sentry('warning', extra_data)
    
    def log_celery_task(self, task_name: str, task_id: str, details: Dict[str, Any]):
        """Log Celery task execution (flows request ID from API)."""
        extra_data = {
            'task_name': task_name,
            'task_id': task_id,
            'request_id': request_id_context.get(),
            **details
        }
        
        self.logger.info(
            f'Celery task: {task_name}',
            extra={'extra_data': extra_data}
        )
    
    @staticmethod
    def _send_to_sentry(level: str, data: Dict[str, Any]):
        """Send event to Sentry if configured."""
        try:
            import sentry_sdk
            
            with sentry_sdk.push_scope() as scope:
                for key, value in data.items():
                    scope.set_context(key, {'value': str(value)})
                
                if level == 'error':
                    sentry_sdk.capture_message(f"Error: {data.get('event_type', 'unknown')}", level='error')
                elif level == 'warning':
                    sentry_sdk.capture_message(f"Warning: {data.get('alert_type', 'unknown')}", level='warning')
        except ImportError:
            pass


# Global logger instances for different domains
booking_logger = StructuredLogger('bebeto.booking')
payment_logger = StructuredLogger('bebeto.payment')
fraud_logger = StructuredLogger('bebeto.fraud')
celery_logger = StructuredLogger('bebeto.celery')
