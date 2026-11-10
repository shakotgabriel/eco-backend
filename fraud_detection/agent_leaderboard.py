"""
Agent leaderboard and analytics for fraud_detection module.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status, permissions
from django.db.models import Count, Sum, Avg, Q
from django.utils import timezone
from datetime import timedelta

from agents.models import Agent, AgentSale, CommissionTier
from bookings.models import Booking
from fraud_detection.logging_utils import fraud_logger


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def agent_leaderboard(request):
    """Get agent leaderboard ranked by bookings/sales."""
    period_days = int(request.query_params.get('days', 30))
    sort_by = request.query_params.get('sort_by', 'sales_count')  # sales_count, revenue, commission
    
    cutoff = timezone.now() - timedelta(days=period_days)
    
    # Get all agents with their sales metrics
    agents = Agent.objects.filter(
        status='ACTIVE'
    ).annotate(
        sales_count=Count('agent_sales__id', filter=Q(agent_sales__booking__created_at__gte=cutoff)),
        total_revenue=Sum('agent_sales__commission_earned', filter=Q(agent_sales__booking__created_at__gte=cutoff)),
        avg_commission=Avg('agent_sales__commission_pct', filter=Q(agent_sales__booking__created_at__gte=cutoff)),
    ).select_related('current_tier')
    
    # Sort
    if sort_by == 'revenue':
        agents = agents.order_by('-total_revenue')
    elif sort_by == 'commission':
        agents = agents.order_by('-avg_commission')
    else:
        agents = agents.order_by('-sales_count')
    
    # Format response
    leaderboard = []
    for rank, agent in enumerate(agents, 1):
        leaderboard.append({
            'rank': rank,
            'agent_code': agent.agent_code,
            'business_name': agent.business_name,
            'city': agent.city.name if agent.city else 'N/A',
            'tier': agent.current_tier.name if agent.current_tier else 'Bronze',
            'sales_count': agent.sales_count or 0,
            'total_revenue': float(agent.total_revenue or 0),
            'avg_commission_pct': float(agent.avg_commission or 0),
            'commission_tier_pct': agent.commission_pct,
            'phone': str(agent.phone),
        })
    
    fraud_logger.log_booking_event('AGENT_LEADERBOARD_VIEW', 0, {
        'period_days': period_days,
        'sort_by': sort_by,
        'agent_count': len(leaderboard)
    })
    
    return Response({
        'leaderboard': leaderboard,
        'period_days': period_days,
        'as_of': timezone.now().isoformat(),
        'total_agents': len(leaderboard)
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def agent_detail_drill_down(request, agent_id):
    """Get detailed booking history for a specific agent."""
    agent = Agent.objects.get(id=agent_id)
    period_days = int(request.query_params.get('days', 30))
    cutoff = timezone.now() - timedelta(days=period_days)
    
    # Get bookings for this agent
    agent_sales = AgentSale.objects.filter(
        agent=agent,
        booking__created_at__gte=cutoff
    ).select_related('booking__trip__route', 'booking__user').order_by('-booking__created_at')
    
    bookings_data = []
    for sale in agent_sales:
        booking = sale.booking
        bookings_data.append({
            'booking_reference': booking.booking_reference,
            'payer_name': booking.payer_name,
            'phone': str(booking.payment_phone),
            'route': f"{booking.trip.route.origin} → {booking.trip.route.destination}",
            'seats': booking.number_of_seats,
            'total_price': float(booking.total_price),
            'commission_earned': float(sale.commission_earned),
            'commission_pct': float(sale.commission_pct),
            'status': booking.status,
            'created_at': booking.created_at.isoformat(),
        })
    
    # Summary stats
    stats = agent_sales.aggregate(
        total_bookings=Count('id'),
        total_revenue=Sum('booking__total_price'),
        total_commission=Sum('commission_earned'),
        avg_booking_value=Avg('booking__total_price'),
    )
    
    fraud_logger.log_booking_event('AGENT_DETAIL_DRILL_DOWN', 0, {
        'agent_id': agent_id,
        'agent_code': agent.agent_code,
        'period_days': period_days,
        'total_bookings': stats['total_bookings'] or 0
    })
    
    return Response({
        'agent': {
            'id': agent.id,
            'agent_code': agent.agent_code,
            'business_name': agent.business_name,
            'city': agent.city.name if agent.city else 'N/A',
            'tier': agent.current_tier.name if agent.current_tier else 'Bronze',
            'commission_pct': float(agent.commission_pct),
            'phone': str(agent.phone),
        },
        'stats': {
            'total_bookings': stats['total_bookings'] or 0,
            'total_revenue': float(stats['total_revenue'] or 0),
            'total_commission': float(stats['total_commission'] or 0),
            'avg_booking_value': float(stats['avg_booking_value'] or 0),
        },
        'bookings': bookings_data,
        'period_days': period_days,
        'as_of': timezone.now().isoformat()
    })