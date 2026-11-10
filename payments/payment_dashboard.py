"""
Payment status visibility dashboard.
Admin view to monitor payment health, failures, timeouts, and retries.
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta

from bebeto_backend.permissions import IsStaffOrAbove
from .models import Payment, MoMoTransaction, MpesaTransaction
from .payment_improvements import PaymentProviderHealth, WebhookRetry, FailedCeleryTask, PaymentRetryLog
from .serializers import PaymentSerializer


class PaymentDashboardViewSet(viewsets.ViewSet):
    """
    Dashboard endpoints for payment monitoring and status visibility.
    
    Endpoints:
    - /api/v1/payments/dashboard/summary/ - Overall stats
    - /api/v1/payments/dashboard/failures/ - Failed payments
    - /api/v1/payments/dashboard/timeouts/ - Timeout transactions
    - /api/v1/payments/dashboard/retries/ - Retry attempts
    - /api/v1/payments/dashboard/provider-health/ - Provider status
    - /api/v1/payments/dashboard/webhook-queue/ - Pending webhooks
    - /api/v1/payments/dashboard/dlq/ - Dead-letter queue
    """
    permission_classes = [IsStaffOrAbove]

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """
        Get overall payment statistics.
        
        Returns: total, completed, failed, pending, timeout stats and counts.
        """
        now = timezone.now()
        last_24h = now - timedelta(hours=24)

        total_payments = Payment.objects.count()
        completed = Payment.objects.filter(status="COMPLETED").count()
        failed = Payment.objects.filter(status="FAILED").count()
        pending = Payment.objects.filter(status="PENDING").count()
        refunded = Payment.objects.filter(status="REFUNDED").count()

        # Last 24h stats
        today_payments = Payment.objects.filter(created_at__gte=last_24h).count()
        today_completed = Payment.objects.filter(status="COMPLETED", created_at__gte=last_24h).count()
        today_failed = Payment.objects.filter(status="FAILED", created_at__gte=last_24h).count()

        # Timeouts
        timeout_momo = MoMoTransaction.objects.filter(momo_status="TIMEOUT").count()
        timeout_mpesa = MpesaTransaction.objects.filter(mpesa_status="TIMEOUT").count()

        # Health
        provider_health = {}
        for health in PaymentProviderHealth.objects.all():
            provider_health[health.provider] = {
                "status": health.status,
                "consecutive_failures": health.consecutive_failures,
                "last_check_at": health.last_check_at,
                "response_time_ms": health.response_time_ms,
            }

        success_rate = (completed / total_payments * 100) if total_payments > 0 else 0

        return Response({
            "summary": {
                "total": total_payments,
                "completed": completed,
                "failed": failed,
                "pending": pending,
                "refunded": refunded,
                "success_rate_percent": round(success_rate, 2),
            },
            "last_24h": {
                "total": today_payments,
                "completed": today_completed,
                "failed": today_failed,
            },
            "timeouts": {
                "momo": timeout_momo,
                "mpesa": timeout_mpesa,
            },
            "provider_health": provider_health,
        })

    @action(detail=False, methods=["get"])
    def failures(self, request):
        """
        Get recent failed payments with details.
        
        Query params:
        - method: Filter by payment method (MTN_MOMO, MPESA, CASH, etc.)
        - hours: Last N hours (default 24)
        - limit: Results limit (default 20)
        """
        method = request.query_params.get("method")
        hours = int(request.query_params.get("hours", 24))
        limit = int(request.query_params.get("limit", 20))

        cutoff = timezone.now() - timedelta(hours=hours)

        queryset = Payment.objects.filter(
            status="FAILED",
            created_at__gte=cutoff
        ).select_related("booking").order_by("-created_at")

        if method:
            queryset = queryset.filter(method=method)

        serializer = PaymentSerializer(queryset[:limit], many=True)
        return Response({
            "total": queryset.count(),
            "results": serializer.data,
        })

    @action(detail=False, methods=["get"])
    def timeouts(self, request):
        """
        Get payment transactions that timed out.
        
        Query params:
        - provider: MTN_MOMO or MPESA
        - hours: Last N hours (default 24)
        """
        provider = request.query_params.get("provider", "").upper()
        hours = int(request.query_params.get("hours", 24))

        cutoff = timezone.now() - timedelta(hours=hours)
        results = []

        if not provider or provider == "MTN_MOMO":
            momo_timeouts = MoMoTransaction.objects.filter(
                momo_status="TIMEOUT",
                created_at__gte=cutoff
            ).select_related("payment__booking").values(
                "reference_id", "payer_msisdn", "created_at", "payment__amount"
            ).order_by("-created_at")[:10]

            results.extend([{
                "provider": "MTN_MOMO",
                "reference_id": tx["reference_id"],
                "phone": tx["payer_msisdn"],
                "amount": tx["payment__amount"],
                "timeout_at": tx["created_at"],
            } for tx in momo_timeouts])

        if not provider or provider == "MPESA":
            mpesa_timeouts = MpesaTransaction.objects.filter(
                mpesa_status="TIMEOUT",
                created_at__gte=cutoff
            ).select_related("payment__booking").values(
                "checkout_request_id", "phone_number", "created_at", "payment__amount"
            ).order_by("-created_at")[:10]

            results.extend([{
                "provider": "MPESA",
                "reference_id": tx["checkout_request_id"],
                "phone": tx["phone_number"],
                "amount": tx["payment__amount"],
                "timeout_at": tx["created_at"],
            } for tx in mpesa_timeouts])

        return Response({
            "total": len(results),
            "results": results,
        })

    @action(detail=False, methods=["get"])
    def retries(self, request):
        """
        Get payment retry history.
        
        Query params:
        - status: PENDING, SUCCEEDED, FAILED
        - hours: Last N hours (default 24)
        """
        status_filter = request.query_params.get("status")
        hours = int(request.query_params.get("hours", 24))

        cutoff = timezone.now() - timedelta(hours=hours)

        queryset = PaymentRetryLog.objects.filter(
            attempted_at__gte=cutoff
        ).select_related("payment__booking").order_by("-attempted_at")

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        results = []
        for log in queryset[:20]:
            results.append({
                "retry_id": log.id,
                "original_payment_id": log.payment_id,
                "retry_attempt": log.retry_attempt_number,
                "attempted_at": log.attempted_at,
                "status": log.status,
                "new_payment_id": log.new_payment_id,
            })

        return Response({
            "total": queryset.count(),
            "results": results,
        })

    @action(detail=False, methods=["get"])
    def provider_health(self, request):
        """Get real-time payment provider health status."""
        health_data = []

        for health in PaymentProviderHealth.objects.all():
            health_data.append({
                "provider": health.provider,
                "status": health.status,
                "consecutive_failures": health.consecutive_failures,
                "last_check_at": health.last_check_at,
                "last_success_at": health.last_success_at,
                "last_error_at": health.last_error_at,
                "error_message": health.error_message,
                "response_time_ms": health.response_time_ms,
            })

        return Response({"providers": health_data})

    @action(detail=False, methods=["get"])
    def webhook_queue(self, request):
        """
        Get pending webhook retries in the queue.
        
        Query params:
        - status: PENDING, DELIVERED, FAILED, ABANDONED
        - webhook_type: MOMO_CALLBACK, MPESA_CALLBACK, AT_CALLBACK
        """
        status_filter = request.query_params.get("status", "PENDING")
        webhook_type = request.query_params.get("webhook_type")

        queryset = WebhookRetry.objects.filter(status=status_filter).order_by("next_retry_at")

        if webhook_type:
            queryset = queryset.filter(webhook_type=webhook_type)

        results = []
        for webhook in queryset[:50]:
            results.append({
                "id": webhook.id,
                "webhook_type": webhook.webhook_type,
                "transaction_ref": webhook.transaction_ref,
                "attempt_count": webhook.attempt_count,
                "max_attempts": webhook.max_attempts,
                "next_retry_at": webhook.next_retry_at,
                "last_error": webhook.last_error,
            })

        return Response({
            "total": queryset.count(),
            "results": results,
        })

    @action(detail=False, methods=["get"])
    def dlq(self, request):
        """
        Get dead-letter queue tasks that failed.
        
        Query params:
        - status: PENDING, RESOLVED, ABANDONED
        """
        status_filter = request.query_params.get("status", "PENDING")

        queryset = FailedCeleryTask.objects.filter(status=status_filter).order_by("-created_at")

        results = []
        for task in queryset[:50]:
            results.append({
                "id": task.id,
                "task_name": task.task_name,
                "attempt_count": task.attempt_count,
                "max_retries": task.max_retries,
                "error_message": task.error_message[:200],  # Truncate long errors
                "next_retry_at": task.next_retry_at,
                "created_at": task.created_at,
            })

        return Response({
            "total": queryset.count(),
            "results": results,
        })

    @action(detail=False, methods=["post"])
    def retry_webhook(self, request):
        """
        Manually retry a specific webhook from the queue.
        
        POST /api/v1/payments/dashboard/retry-webhook/
        { "webhook_id": <id> }
        """
        webhook_id = request.data.get("webhook_id")

        try:
            webhook = WebhookRetry.objects.get(pk=webhook_id)
        except WebhookRetry.DoesNotExist:
            return Response(
                {"detail": "Webhook not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Trigger immediate retry
        from .payment_tasks import retry_webhook_delivery
        retry_webhook_delivery.delay(webhook_id)

        return Response({"detail": f"Webhook {webhook_id} queued for immediate retry."})

    @action(detail=False, methods=["post"])
    def retry_dlq_task(self, request):
        """
        Manually retry a specific failed task from DLQ.
        
        POST /api/v1/payments/dashboard/retry-dlq-task/
        { "task_id": <id> }
        """
        task_id = request.data.get("task_id")

        try:
            task_record = FailedCeleryTask.objects.get(pk=task_id)
        except FailedCeleryTask.DoesNotExist:
            return Response(
                {"detail": "Task not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Trigger immediate retry
        from .payment_tasks import retry_failed_celery_tasks
        retry_failed_celery_tasks.delay()

        return Response({"detail": f"Task {task_id} queued for retry."})
