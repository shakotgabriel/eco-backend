"""
Celery tasks for fraud detection, monitoring, and health checks.
Includes dead-letter queue with Slack alerting.
"""
import time
import requests
from decimal import Decimal
from django.utils import timezone
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.core.mail import send_mail
from django.conf import settings

from fraud_detection.models import FraudAlert, PaymentVelocity, NoShowTracking, SeatHeatMapData, DemandForecast
from fraud_detection.services import (
    FraudDetectionService, NoShowService, SeatHeatMapService, DemandForecastService
)
from fraud_detection.logging_utils import fraud_logger, celery_logger, payment_logger
from bookings.models import Booking
from payments.models import Payment


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def check_booking_fraud(self, booking_id: int):
    """
    Background task: check booking for fraud patterns.
    Runs after booking creation.
    """
    try:
        booking = Booking.objects.get(id=booking_id)
        phone = str(booking.payment_phone)
        
        celery_logger.log_celery_task(
            'check_booking_fraud',
            self.request.id,
            {'booking_id': booking_id, 'phone': phone}
        )
        
        # 1. Check duplicate booking
        dup_alert = FraudDetectionService.detect_duplicate_booking(phone, booking.trip_id)
        if dup_alert:
            fraud_logger.log_fraud_detection('DUPLICATE_BOOKING', {
                'booking_id': booking_id,
                'trip_id': booking.trip_id,
                'duplicate_alert_id': dup_alert.id
            })
        
        # 2. Check payment velocity
        if FraudDetectionService.check_payment_velocity(phone):
            alert = FraudDetectionService.create_fraud_alert(
                alert_type='VELOCITY_EXCEEDED',
                phone_number=phone,
                severity='MEDIUM',
                user=booking.user,
                booking=booking,
                description=f'Payment velocity exceeded for {phone}',
                risk_score=Decimal('0.7')
            )
            fraud_logger.log_fraud_detection('VELOCITY_EXCEEDED', {
                'booking_id': booking_id,
                'alert_id': alert.alert_id
            })
        
        # 3. Check seat scalping
        if booking.user:
            scalp_alert = FraudDetectionService.detect_seat_scalping(booking.user.id)
            if scalp_alert:
                fraud_logger.log_fraud_detection('SEAT_SCALPING', {
                    'booking_id': booking_id,
                    'user_id': booking.user.id,
                    'alert_id': scalp_alert.id
                })
    
    except Booking.DoesNotExist:
        celery_logger.logger.error(f'Booking {booking_id} not found')
    except Exception as exc:
        # Retry up to 3 times with backoff
        try:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        except MaxRetriesExceededError:
            fraud_logger.logger.error(f'Max retries exceeded for booking fraud check {booking_id}')
            # Send to dead-letter queue
            send_to_dlq('check_booking_fraud', booking_id, str(exc))


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def check_payment_fraud(self, payment_id: int):
    """
    Background task: check payment for fraud patterns.
    Runs after payment creation/update.
    """
    try:
        payment = Payment.objects.get(id=payment_id)
        booking = payment.booking
        phone = str(booking.payment_phone)
        
        celery_logger.log_celery_task(
            'check_payment_fraud',
            self.request.id,
            {'payment_id': payment_id, 'booking_id': booking.id}
        )
        
        # Update payment velocity tracking
        velocity, _ = PaymentVelocity.objects.get_or_create(phone_number=phone)
        velocity.payment_attempts_24h.append(timezone.now().isoformat())
        
        if payment.status == 'COMPLETED':
            velocity.total_amount_24h += payment.amount
        elif payment.status == 'FAILED':
            velocity.failed_attempts_24h += 1
        
        velocity.different_payment_methods.append(payment.method)
        velocity.save()
        
        payment_logger.log_payment_event('VELOCITY_CHECK', payment_id, {
            'phone': phone,
            'is_velocity_exceeded': velocity.is_velocity_exceeded()
        })
    
    except Payment.DoesNotExist:
        celery_logger.logger.error(f'Payment {payment_id} not found')
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        except MaxRetriesExceededError:
            payment_logger.logger.error(f'Max retries exceeded for payment fraud check {payment_id}')
            send_to_dlq('check_payment_fraud', payment_id, str(exc))


@shared_task(bind=True, max_retries=5, default_retry_delay=30)
def check_mtn_health(self):
    """Check MTN MoMo gateway health."""
    try:
        # Call MTN health endpoint
        start_time = time.time()
        
        # In production, would call actual MTN health endpoint
        # For now, simulate
        import random
        response_time = random.randint(50, 500)
        time.sleep(response_time / 1000)  # Simulate delay
        
        result = {
            'status': 'ok',
            'response_time_ms': response_time,
            'gateway': 'MTN_MOMO',
            'timestamp': timezone.now().isoformat()
        }
        
        celery_logger.log_celery_task('check_mtn_health', self.request.id, result)
        return result
    
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=10 ** self.request.retries)
        except MaxRetriesExceededError:
            alert_payment_gateway_failure('MTN_MOMO', str(exc))
            send_to_dlq('check_mtn_health', 'mtn', str(exc))
            return {'status': 'error', 'gateway': 'MTN_MOMO', 'error': str(exc)}


@shared_task(bind=True, max_retries=5, default_retry_delay=30)
def check_mpesa_health(self):
    """Check M-Pesa gateway health."""
    try:
        start_time = time.time()
        
        # Simulate M-Pesa health check
        import random
        response_time = random.randint(50, 500)
        time.sleep(response_time / 1000)
        
        result = {
            'status': 'ok',
            'response_time_ms': response_time,
            'gateway': 'MPESA',
            'timestamp': timezone.now().isoformat()
        }
        
        celery_logger.log_celery_task('check_mpesa_health', self.request.id, result)
        return result
    
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=10 ** self.request.retries)
        except MaxRetriesExceededError:
            alert_payment_gateway_failure('MPESA', str(exc))
            send_to_dlq('check_mpesa_health', 'mpesa', str(exc))
            return {'status': 'error', 'gateway': 'MPESA', 'error': str(exc)}


@shared_task(bind=True, max_retries=5, default_retry_delay=30)
def check_all_gateway_health(self):
    """Check all payment gateways concurrently."""
    from celery import chord
    
    callback = aggregate_gateway_health.s()
    header = [check_mtn_health.s(), check_mpesa_health.s()]
    
    return chord(header)(callback)


@shared_task
def aggregate_gateway_health(results):
    """Aggregate health check results."""
    return {
        'all_gateways': results,
        'timestamp': timezone.now().isoformat(),
        'status': 'healthy' if all(r.get('status') == 'ok' for r in results) else 'degraded'
    }


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_seat_heat_maps(self):
    """Generate seat heat maps for all bus/route combinations."""
    try:
        from buses.models import Bus
        from routes.models import Route
        
        period_end = timezone.now()
        period_start = period_end - timezone.timedelta(days=1)
        
        buses = Bus.objects.filter(status='ACTIVE')
        routes = Route.objects.filter(is_active=True)
        
        count = 0
        for bus in buses:
            for route in routes:
                heat_map = SeatHeatMapService.generate_seat_heat_map(
                    bus.id, route.id, period_start, period_end
                )
                count += 1
        
        celery_logger.log_celery_task('generate_seat_heat_maps', self.request.id, {
            'heat_maps_generated': count
        })
    
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        except MaxRetriesExceededError:
            celery_logger.logger.error(f'Max retries for heat map generation: {exc}')
            send_to_dlq('generate_seat_heat_maps', None, str(exc))


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_demand_forecasts(self):
    """Generate demand forecasts for all routes."""
    try:
        from routes.models import Route
        
        routes = Route.objects.filter(is_active=True)
        
        count = 0
        for route in routes:
            forecast = DemandForecastService.generate_forecast(route.id)
            
            # Update old forecasts with actuals
            old_forecasts = DemandForecast.objects.filter(
                route=route,
                forecast_date__lt=timezone.now().date()
            )
            for old in old_forecasts:
                DemandForecastService.update_forecast_with_actuals(old)
            
            count += 1
        
        celery_logger.log_celery_task('generate_demand_forecasts', self.request.id, {
            'forecasts_generated': count
        })
    
    except Exception as exc:
        try:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        except MaxRetriesExceededError:
            celery_logger.logger.error(f'Max retries for demand forecasts: {exc}')
            send_to_dlq('generate_demand_forecasts', None, str(exc))


@shared_task
def alert_fraud_team(alert_id: str):
    """Send alert to fraud team via Slack."""
    try:
        alert = FraudAlert.objects.get(alert_id=alert_id)
        
        slack_message = f"""
🚨 **Fraud Alert: {alert.alert_id}**
Type: {alert.get_alert_type_display()}
Severity: {alert.get_severity_display()}
Phone: {alert.phone_number}
Risk Score: {alert.risk_score}
Description: {alert.description}
        """
        
        _send_slack_message(slack_message)
        
    except FraudAlert.DoesNotExist:
        pass


@shared_task
def process_dead_letter_queue():
    """Process dead-letter queue and send Slack alerts."""
    # In production, would have a proper DLQ backend (Redis, RabbitMQ)
    # For now, query failed tasks
    
    try:
        from django_celery_results.models import TaskResult
        
        failed_tasks = TaskResult.objects.filter(
            status='FAILURE',
            task_name__in=[
                'fraud_detection.tasks.check_booking_fraud',
                'fraud_detection.tasks.check_payment_fraud',
                'fraud_detection.tasks.check_mtn_health',
                'fraud_detection.tasks.check_mpesa_health',
            ]
        ).order_by('-date_done')[:10]
        
        for task in failed_tasks:
            slack_message = f"""
❌ **Celery Task Failed (DLQ)**
Task: {task.task_name}
Task ID: {task.task_id}
Error: {task.traceback[:200] if task.traceback else 'Unknown'}
Failed at: {task.date_done}
            """
            _send_slack_message(slack_message)
    except ImportError:
        pass


def send_to_dlq(task_name: str, task_arg, error: str):
    """Send failed task to dead-letter queue."""
    dlq_entry = {
        'task_name': task_name,
        'task_arg': task_arg,
        'error': error,
        'timestamp': timezone.now().isoformat(),
        'status': 'pending_retry'
    }
    
    # In production, would write to Redis or queue system
    # For now, just log
    celery_logger.logger.error(f'DLQ Entry: {dlq_entry}')


def alert_payment_gateway_failure(gateway: str, error: str):
    """Alert on payment gateway failure."""
    message = f"""
⚠️ **Payment Gateway Alert**
Gateway: {gateway}
Error: {error}
Timestamp: {timezone.now().isoformat()}
    """
    _send_slack_message(message)
    payment_logger.log_payment_event('GATEWAY_ERROR', 0, {'gateway': gateway, 'error': error})


def _send_slack_message(message: str):
    """Send message to Slack webhook."""
    webhook_url = getattr(settings, 'SLACK_WEBHOOK_URL', None)
    if not webhook_url:
        return
    
    try:
        requests.post(webhook_url, json={'text': message}, timeout=5)
    except Exception as e:
        celery_logger.logger.error(f'Failed to send Slack message: {e}')
