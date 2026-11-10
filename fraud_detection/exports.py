"""
CSV/Excel report export functionality for operators.
"""
import csv
import io
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.http import HttpResponse, FileResponse
from django.db.models import Count, Sum, Avg
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from bookings.models import Booking
from payments.models import Payment
from fraud_detection.models import (
    NoShowTracking, SeatHeatMapData, DemandForecast, FraudAlert, DuplicateBookingAlert
)


class ReportExporter:
    """Export operational reports to CSV and Excel."""
    
    @staticmethod
    def export_booking_report_csv(date_from, date_to, route_id=None):
        """Export booking report as CSV."""
        bookings = Booking.objects.filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to
        )
        
        if route_id:
            bookings = bookings.filter(trip__route_id=route_id)
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="bookings_{date_from}_{date_to}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Booking Reference', 'Trip ID', 'Route', 'Departure', 'Payer Name',
            'Phone', 'Seats', 'Total Price', 'Status', 'Created At'
        ])
        
        for booking in bookings.select_related('trip__route'):
            writer.writerow([
                booking.booking_reference,
                booking.trip.id,
                f"{booking.trip.route.origin} → {booking.trip.route.destination}",
                booking.trip.departure_time,
                booking.payer_name,
                booking.payment_phone,
                booking.number_of_seats,
                booking.total_price,
                booking.status,
                booking.created_at
            ])
        
        return response
    
    @staticmethod
    def export_revenue_report_csv(date_from, date_to, route_id=None):
        """Export revenue report as CSV."""
        payments = Payment.objects.filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
            status='COMPLETED'
        )
        
        if route_id:
            payments = payments.filter(booking__trip__route_id=route_id)
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="revenue_{date_from}_{date_to}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Date', 'Payment ID', 'Booking Reference', 'Amount', 'Currency',
            'Method', 'Route', 'Status'
        ])
        
        for payment in payments.select_related('booking__trip__route'):
            writer.writerow([
                payment.created_at.date(),
                payment.id,
                payment.booking.booking_reference,
                payment.amount,
                payment.currency,
                payment.method,
                f"{payment.booking.trip.route.origin} → {payment.booking.trip.route.destination}",
                payment.status
            ])
        
        return response
    
    @staticmethod
    def export_no_show_report_csv(date_from, date_to, route_id=None):
        """Export no-show report as CSV."""
        no_shows = NoShowTracking.objects.filter(
            scheduled_departure__date__gte=date_from,
            scheduled_departure__date__lte=date_to
        )
        
        if route_id:
            no_shows = no_shows.filter(route_id=route_id)
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="no_shows_{date_from}_{date_to}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Booking Reference', 'Route', 'Scheduled Departure', 'Time Slot',
            'Day of Week', 'Checked In', 'Boarded', 'Marked No-Show At'
        ])
        
        for no_show in no_shows.select_related('booking', 'route'):
            writer.writerow([
                no_show.booking.booking_reference,
                f"{no_show.route.origin} → {no_show.route.destination}",
                no_show.scheduled_departure,
                no_show.departure_time_slot,
                no_show.day_of_week,
                'Yes' if no_show.passenger_checked_in else 'No',
                'Yes' if no_show.passenger_boarded else 'No',
                no_show.marked_no_show_at
            ])
        
        return response
    
    @staticmethod
    def export_heat_map_report_excel(bus_id=None, route_id=None):
        """Export seat heat map data as Excel with formatting."""
        from io import BytesIO
        
        heat_maps = SeatHeatMapData.objects.all()
        
        if bus_id:
            heat_maps = heat_maps.filter(bus_id=bus_id)
        if route_id:
            heat_maps = heat_maps.filter(route_id=route_id)
        
        # Create workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Seat Heat Maps'
        
        # Add headers with formatting
        headers = [
            'Bus Plate', 'Route', 'Period Start', 'Period End', 'Total Seats',
            'Booked Seats', 'Avg Lead Time (hrs)', 'VIP %', 'Regular %',
            'Window %', 'Aisle %', 'Middle %'
        ]
        ws.append(headers)
        
        # Style header row
        header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF')
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
        
        # Add data
        for heat_map in heat_maps.select_related('bus', 'route'):
            ws.append([
                heat_map.bus.plate_number,
                f"{heat_map.route.origin} → {heat_map.route.destination}",
                heat_map.period_start,
                heat_map.period_end,
                heat_map.total_seats,
                heat_map.seats_booked,
                float(heat_map.avg_booking_lead_time_hours),
                float(heat_map.vip_seats_pct_booked),
                float(heat_map.regular_seats_pct_booked),
                float(heat_map.window_seats_pct_booked),
                float(heat_map.aisle_seats_pct_booked),
                float(heat_map.middle_seats_pct_booked),
            ])
        
        # Adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Return as file
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        response = FileResponse(
            output,
            as_attachment=True,
            filename=f"seat_heat_maps_{timezone.now().date()}.xlsx"
        )
        return response
    
    @staticmethod
    def export_demand_forecast_excel(route_id=None):
        """Export demand forecasts as Excel."""
        from io import BytesIO
        
        forecasts = DemandForecast.objects.all()
        if route_id:
            forecasts = forecasts.filter(route_id=route_id)
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Demand Forecasts'
        
        headers = [
            'Route', 'Forecast Date', 'Confidence Level', 'Forecast Accuracy',
            'Avg Historical Booking Lead Time', 'Forecast Sample (5 days)',
            'Actual Sample (5 days)'
        ]
        ws.append(headers)
        
        # Style header
        header_fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF')
        
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        
        for forecast in forecasts.select_related('route'):
            forecast_sample = list(forecast.forecast_data.items())[:5]
            actual_sample = list(forecast.actual_results.items())[:5]
            
            ws.append([
                f"{forecast.route.origin} → {forecast.route.destination}",
                forecast.forecast_date,
                float(forecast.confidence_level),
                float(forecast.forecast_accuracy) if forecast.forecast_accuracy else 'N/A',
                float(forecast.avg_booking_lead_time_hours),
                str(forecast_sample),
                str(actual_sample)
            ])
        
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        response = FileResponse(
            output,
            as_attachment=True,
            filename=f"demand_forecasts_{timezone.now().date()}.xlsx"
        )
        return response
    
    @staticmethod
    def export_fraud_alerts_csv(date_from=None, date_to=None, severity=None):
        """Export fraud alerts as CSV."""
        alerts = FraudAlert.objects.all()
        
        if date_from:
            alerts = alerts.filter(created_at__date__gte=date_from)
        if date_to:
            alerts = alerts.filter(created_at__date__lte=date_to)
        if severity:
            alerts = alerts.filter(severity=severity)
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="fraud_alerts_{timezone.now().date()}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Alert ID', 'Type', 'Severity', 'Status', 'Phone', 'User', 'Booking Ref',
            'Risk Score', 'Description', 'Action Taken', 'Created At', 'Reviewed At'
        ])
        
        for alert in alerts.select_related('user', 'booking'):
            writer.writerow([
                alert.alert_id,
                alert.alert_type,
                alert.severity,
                alert.status,
                alert.phone_number,
                alert.user.username if alert.user else 'N/A',
                alert.booking.booking_reference if alert.booking else 'N/A',
                float(alert.risk_score),
                alert.description[:100],
                alert.action_taken,
                alert.created_at,
                alert.reviewed_at
            ])
        
        return response
