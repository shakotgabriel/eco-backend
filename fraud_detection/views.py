"""
API views for fraud detection and analytics.
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.db.models import Q, Count, Avg
from datetime import timedelta

from fraud_detection.models import (
    DuplicateBookingAlert,
    SeatScalpingAlert,
    PaymentVelocity,
    FraudAlert,
    NoShowTracking,
    SeatHeatMapData,
    DemandForecast,
)
from fraud_detection.serializers import (
    DuplicateBookingAlertSerializer,
    SeatScalpingAlertSerializer,
    PaymentVelocitySerializer,
    FraudAlertListSerializer,
    FraudAlertDetailSerializer,
    FraudAlertActionSerializer,
    NoShowTrackingSerializer,
    SeatHeatMapDataSerializer,
    DemandForecastSerializer,
)
from fraud_detection.services import FraudDetectionService
from bebeto_backend.permissions import IsStaffOrAbove


class IsAdminOrStaff(permissions.BasePermission):
    """Only admin/staff can access fraud detection."""
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and (
            request.user.role in ['ADMIN', 'MANAGER', 'STAFF']
        )


class DuplicateBookingAlertViewSet(viewsets.ReadOnlyModelViewSet):
    """View and manage duplicate booking alerts."""
    queryset = DuplicateBookingAlert.objects.all()
    serializer_class = DuplicateBookingAlertSerializer
    permission_classes = [IsAdminOrStaff]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'phone_number', 'trip']
    search_fields = ['phone_number']
    ordering_fields = ['created_at', 'time_between_bookings_seconds']
    ordering = ['-created_at']
    
    @action(detail=True, methods=['post'])
    def mark_resolved(self, request, pk=None):
        """Mark alert as resolved."""
        alert = self.get_object()
        alert.status = 'RESOLVED'
        alert.reviewed_by = request.user
        alert.reviewed_at = timezone.now()
        alert.review_notes = request.data.get('notes', '')
        alert.save()
        return Response(DuplicateBookingAlertSerializer(alert).data)
    
    @action(detail=True, methods=['post'])
    def mark_false_positive(self, request, pk=None):
        """Mark as false positive."""
        alert = self.get_object()
        alert.status = 'FALSE_POSITIVE'
        alert.reviewed_by = request.user
        alert.reviewed_at = timezone.now()
        alert.review_notes = request.data.get('notes', '')
        alert.save()
        return Response(DuplicateBookingAlertSerializer(alert).data)


class SeatScalpingAlertViewSet(viewsets.ReadOnlyModelViewSet):
    """View and manage seat scalping alerts."""
    queryset = SeatScalpingAlert.objects.all()
    serializer_class = SeatScalpingAlertSerializer
    permission_classes = [IsAdminOrStaff]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'user']
    search_fields = ['user__username', 'user__email']
    ordering_fields = ['created_at', 'total_book_cancel_cycles']
    ordering = ['-created_at']
    
    @action(detail=True, methods=['post'])
    def mark_flagged(self, request, pk=None):
        """Mark alert as flagged for review."""
        alert = self.get_object()
        alert.status = 'FLAGGED'
        alert.save()
        return Response(SeatScalpingAlertSerializer(alert).data)


class FraudAlertViewSet(viewsets.ModelViewSet):
    """
    Fraud alert queue: browse, review, and take action.
    Staff can view; only admins can take actions.
    """
    queryset = FraudAlert.objects.all()
    permission_classes = [IsAdminOrStaff]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'severity', 'alert_type', 'phone_number']
    search_fields = ['alert_id', 'phone_number', 'user__username']
    ordering_fields = ['created_at', 'severity', 'risk_score']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return FraudAlertDetailSerializer
        return FraudAlertListSerializer
    
    @action(detail=True, methods=['post'])
    def take_action(self, request, pk=None):
        """
        One-click fraud action: BLOCK_ACCOUNT, REFUND, RELEASE, INVESTIGATE.
        """
        alert = self.get_object()
        serializer = FraudAlertActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        action_type = serializer.validated_data['action']
        notes = serializer.validated_data.get('notes', '')
        
        if action_type == 'BLOCK_ACCOUNT':
            results = FraudDetectionService.block_account_and_refund(alert)
            alert.action_taken = 'BLOCK_ACCOUNT'
            alert.action_details = results
        elif action_type == 'REFUND':
            amount = serializer.validated_data.get('amount_to_refund')
            results = FraudDetectionService.block_account_and_refund(alert, amount)
            alert.action_taken = 'REFUND'
            alert.action_details = results
        elif action_type == 'RELEASE':
            alert.status = 'CLEARED'
            alert.action_taken = 'RELEASE'
        elif action_type == 'INVESTIGATE':
            alert.status = 'UNDER_REVIEW'
        
        alert.reviewed_by = request.user
        alert.reviewed_at = timezone.now()
        alert.review_notes = notes
        alert.save()
        
        return Response(FraudAlertDetailSerializer(alert).data, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'])
    def pending_count(self, request):
        """Get count of pending fraud alerts by severity."""
        counts = FraudAlert.objects.filter(
            status='PENDING'
        ).values('severity').annotate(count=Count('id'))
        
        return Response({
            'pending_alerts': dict(counts),
            'total_pending': FraudAlert.objects.filter(status='PENDING').count()
        })
    
    @action(detail=False, methods=['get'])
    def critical_alerts(self, request):
        """Get high and critical severity alerts."""
        alerts = FraudAlert.objects.filter(
            status__in=['PENDING', 'UNDER_REVIEW'],
            severity__in=['HIGH', 'CRITICAL']
        )
        serializer = FraudAlertListSerializer(alerts, many=True)
        return Response(serializer.data)


class NoShowTrackingViewSet(viewsets.ReadOnlyModelViewSet):
    """View no-show tracking and analytics."""
    queryset = NoShowTracking.objects.all()
    serializer_class = NoShowTrackingSerializer
    permission_classes = [IsAdminOrStaff]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['route', 'day_of_week', 'departure_time_slot']
    ordering_fields = ['scheduled_departure', 'marked_no_show_at']
    ordering = ['-scheduled_departure']
    
    @action(detail=False, methods=['get'])
    def no_show_rate(self, request):
        """Get no-show rate by route/time slot."""
        route_id = request.query_params.get('route_id')
        time_slot = request.query_params.get('time_slot')
        days = int(request.query_params.get('days', 30))
        
        from fraud_detection.services import NoShowService
        
        rate_data = NoShowService.get_no_show_rate(
            route_id=route_id,
            time_slot=time_slot,
            days=days
        )
        return Response(rate_data)
    
    @action(detail=False, methods=['get'])
    def by_route_summary(self, request):
        """Get no-show summary by route."""
        cutoff = timezone.now() - timedelta(days=30)
        
        summary = NoShowTracking.objects.filter(
            scheduled_departure__gte=cutoff
        ).values('route').annotate(
            total_no_shows=Count('id'),
            avg_no_show_rate=Avg('passenger_boarded')
        ).order_by('-total_no_shows')
        
        return Response(summary)


class SeatHeatMapViewSet(viewsets.ReadOnlyModelViewSet):
    """View seat heat map analytics."""
    queryset = SeatHeatMapData.objects.all()
    serializer_class = SeatHeatMapDataSerializer
    permission_classes = [IsAdminOrStaff]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['bus', 'route', 'time_period']
    ordering_fields = ['period_start', 'seats_booked']
    ordering = ['-period_start']
    
    @action(detail=False, methods=['get'])
    def most_popular_seats(self, request):
        """Get most popular seat positions across all heat maps."""
        route_id = request.query_params.get('route_id')
        
        query = SeatHeatMapData.objects.all()
        if route_id:
            query = query.filter(route_id=route_id)
        
        # Calculate average booking rates by position
        heat_maps = query.values('time_period').annotate(
            avg_window_booked=Avg('window_seats_pct_booked'),
            avg_aisle_booked=Avg('aisle_seats_pct_booked'),
            avg_middle_booked=Avg('middle_seats_pct_booked'),
            avg_vip_booked=Avg('vip_seats_pct_booked'),
            avg_regular_booked=Avg('regular_seats_pct_booked'),
        )
        
        return Response(heat_maps)


class DemandForecastViewSet(viewsets.ReadOnlyModelViewSet):
    """View demand forecasting data."""
    queryset = DemandForecast.objects.all()
    serializer_class = DemandForecastSerializer
    permission_classes = [IsAdminOrStaff]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['route', 'forecast_date']
    ordering_fields = ['forecast_date', 'confidence_level']
    ordering = ['-forecast_date']
    
    @action(detail=True, methods=['get'])
    def accuracy(self, request, pk=None):
        """Get forecast accuracy for a specific forecast."""
        forecast = self.get_object()
        
        accuracy_data = {
            'forecast_id': forecast.id,
            'forecast_date': forecast.forecast_date,
            'confidence_level': float(forecast.confidence_level),
            'forecast_accuracy': float(forecast.forecast_accuracy) if forecast.forecast_accuracy else None,
            'forecast_data_sample': dict(list(forecast.forecast_data.items())[:5]),
            'actual_results_sample': dict(list(forecast.actual_results.items())[:5]),
        }
        
        return Response(accuracy_data)
    
    @action(detail=False, methods=['get'])
    def by_route_forecast(self, request):
        """Get latest forecast for each route."""
        routes = request.query_params.get('routes', '').split(',')
        
        forecasts = DemandForecast.objects.filter(
            route_id__in=[r for r in routes if r.isdigit()]
        ).order_by('route', '-forecast_date').distinct('route')
        
        serializer = DemandForecastSerializer(forecasts, many=True)
        return Response(serializer.data)


class PaymentHealthCheckViewSet(viewsets.ViewSet):
    """Health check endpoints for payment gateways."""
    permission_classes = [IsAdminOrStaff]
    
    @action(detail=False, methods=['get'])
    def mtn_momo(self, request):
        """Check MTN MoMo gateway health."""
        from fraud_detection.tasks import check_mtn_health
        from celery.result import AsyncResult
        
        task = check_mtn_health.delay()
        result = AsyncResult(task.id).get(timeout=10)
        
        return Response({
            'gateway': 'MTN_MOMO',
            'status': 'healthy' if result.get('status') == 'ok' else 'unhealthy',
            'response_time_ms': result.get('response_time_ms'),
            'last_check': timezone.now().isoformat(),
        })
    
    @action(detail=False, methods=['get'])
    def mpesa(self, request):
        """Check M-Pesa gateway health."""
        from fraud_detection.tasks import check_mpesa_health
        from celery.result import AsyncResult
        
        task = check_mpesa_health.delay()
        result = AsyncResult(task.id).get(timeout=10)
        
        return Response({
            'gateway': 'MPESA',
            'status': 'healthy' if result.get('status') == 'ok' else 'unhealthy',
            'response_time_ms': result.get('response_time_ms'),
            'last_check': timezone.now().isoformat(),
        })
    
    @action(detail=False, methods=['get'])
    def all_gateways(self, request):
        """Check all payment gateway health."""
        from fraud_detection.tasks import check_all_gateway_health
        from celery.result import AsyncResult
        
        task = check_all_gateway_health.delay()
        result = AsyncResult(task.id).get(timeout=15)
        
        return Response(result)
